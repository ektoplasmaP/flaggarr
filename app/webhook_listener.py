#!/usr/bin/env python3
# webhook_listener.py - receives the push from Radarr and Sonarr on import.
#
# The order is the whole point: flags are written first, then Plex is asked to
# reread the file, and only after that are the saved Plex selections corrected.
# Radarr/Sonarr often ask Plex to scan before the flags are in place, so we
# force a fresh read here.
#
# Radarr/Sonarr: Settings -> Connect -> Webhook
#   URL: http://<your-nas>:8899/   Method: POST
#   Trigger: On Import / On Upgrade
#
# Log: /config/log/webhook_listener.log

# Webhook listener for Radarr and Sonarr. HTTP server on port 8899.
#
# Handles On File Import and On File Upgrade. Reads the file path and the
# original language from the payload, checks that the file sits under the media
# root, is an .mkv and exists on disk, and then runs:
#   1. auto_set_mkv_defaultflags.sh          writes the flags into the file
#   2. a rescan of that one folder in Plex
#   3. auto_set_plex_defaults.py --recent N   after 60 s, N = files in the batch + 5 (at least 5)
#
# Answers 200 immediately and works in a background thread, so Radarr never
# times out. The connections come from settings.json via ui.load_env().
#
# Post-processing sits in a queue with one worker thread. When several episodes
# land close together they are collected for 15 seconds and run in one go -
# earlier the later episodes were simply dropped.
#
# NOTE: DSM's text editor does not save this file reliably - edits roll back.
# Upload it through File Station instead.

import json
import os
import re
import subprocess
import threading
import queue
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8899
# The program lives in the image (/app); this installation's own files live in
# the mounted volume (/config).
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = "/config"
SETDEFAULTS = APP_DIR + "/auto_set_mkv_defaultflags.sh"
PLEXPICK = APP_DIR + "/auto_set_plex_defaults.py"
LOG_DIR = CONFIG_DIR + "/log"
LOGFILE = LOG_DIR + "/webhook_listener.log"
os.makedirs(LOG_DIR, exist_ok=True)   # a freshly mounted /config volume is empty

import ui  # the web page with the switches and run buttons, same port
START_TIME = datetime.now().strftime("%Y-%m-%d %H:%M")

os.environ["PATH"] = "/usr/local/bin:/opt/bin:" + os.environ.get("PATH", "")

plexlas = threading.Lock()
_plex_queue = queue.Queue()
_plexstart = threading.Lock()
_plexstartad = False


def log(row):
    txt = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + row
    print(txt, flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(txt + "\n")
    except OSError:
        pass


def files(data):
    film = data.get("movie") or {}
    serie = data.get("series") or {}
    titel = film or serie
    lang = ((titel.get("originalLanguage") or {}).get("name") or "").strip()
    root = film.get("folderPath") or serie.get("path") or ""
    kandidater = []
    if data.get("movieFile"):
        kandidater.append(data["movieFile"])
    if data.get("episodeFile"):
        kandidater.append(data["episodeFile"])
    kandidater = kandidater + (data.get("episodeFiles") or [])
    ut = []
    for k in kandidater:
        p = k.get("path") or os.path.join(root, k.get("relativePath") or "")
        if p:
            ut.append((p, lang))
    return ut


def plex_rescan_folder(folder):
    url = (os.environ.get("PLEX_URL") or "").rstrip("/")
    token = (os.environ.get("PLEX_TOKEN") or "").strip()
    if not url or not token:
        log("plex-scan: PLEX_URL or PLEX_TOKEN missing")
        return
    try:
        req = urllib.request.Request(
            url + "/library/sections?X-Plex-Token=" + token,
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as exc:
        log("plex-scan: could not read sections: %s" % exc)
        return
    for s in ((data.get("MediaContainer") or {}).get("Directory") or []):
        for plats in (s.get("Location") or []):
            root = (plats.get("path") or "").rstrip("/")
            if root and (folder == root or folder.startswith(root + "/")):
                target = (url + "/library/sections/" + str(s.get("key"))
                       + "/refresh?path=" + urllib.parse.quote(folder)
                       + "&X-Plex-Token=" + token)
                try:
                    urllib.request.urlopen(target, timeout=30).read()
                    log("plex-scan: asked Plex to rescan " + folder)
                except Exception as exc:
                    log("plex-scan: ERROR %s" % exc)
                return
    log("plex-scan: no Plex section found for " + folder)


def plex(count=0):
    if not os.path.isfile(PLEXPICK):
        log("not found: " + PLEXPICK)
        return
    # As many as came in with the batch plus the margin, never fewer than the
    # configured minimum. Plex "recentlyAdded" is sorted by when it was added,
    # so the margin covers anything Plex added on its own meanwhile.
    inst = ui.read_settings()
    n = max(inst["recent_min"], count + inst["margin"])
    cmd = ["python3", PLEXPICK, "--apply", "--recent", str(n)]
    if not inst["plex_subs"]:
        cmd.append("--audio-only")
    if not inst["plex_audio"]:
        cmd.append("--subs-only")
    r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, **ui.script_env()))
    changed = lambda out: int((re.search(r"Changed (\d+) titles?", out or "") or [0, 0])[1])   # the script's own summary line
    if r.returncode == 0:
        log("plex-pick: done, %d selections set (%d files in the batch, checked %d most recent)" % (changed(r.stdout), count, n))
    else:
        log("plex-pick: ERROR " + (r.stdout + r.stderr).strip())
    for uname, uenv in ui.user_runs():           # once more per custom user profile, with that account’s token
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, **uenv))
        if r.returncode == 0:
            log("plex-pick (%s): done, %d selections set" % (uname, changed(r.stdout)))
        else:
            log("plex-pick (%s): ERROR " % uname + (r.stdout + r.stderr).strip())


def _plex_worker():
    while True:
        folder, count = _plex_queue.get()
        folders = [folder]
        time.sleep(ui.read_settings()["batch_sec"])
        try:
            while True:
                m, a = _plex_queue.get_nowait()
                count += a
                if m not in folders:
                    folders.append(m)
        except queue.Empty:
            pass
        try:
            inst = ui.read_settings()
            if inst["plex_rescan"]:
                for m in folders:
                    plex_rescan_folder(m)
            else:
                log("plex rescan off in UI, skipping")
            time.sleep(inst["after_scan_sec"])
            if inst["plex_audio"] or inst["plex_subs"]:
                plex(count)
            else:
                log("plex selection off in UI, skipping")
        except Exception as e:
            log("post-processing: error " + str(e))


def post_process(folder, count=1):
    # count = how many files in this folder just got flags. The sum over the
    # batch decides how many "recently added" the Plex step looks at.
    global _plexstartad
    with _plexstart:
        if not _plexstartad:
            threading.Thread(target=_plex_worker, daemon=True).start()
            _plexstartad = True
    _plex_queue.put((folder, count))
    log("post-processing: queued %s (%d files)" % (folder, count))


def handle_import(data):
    h = data.get("eventType", "?")
    if h not in ("Download", "Rename"):
        log(h + " - ignored")
        return
    inst = ui.read_settings()
    if not inst["listener"]:
        log(h + " - listener off in UI, ignored")
        return
    hits = files(data)
    if not hits:
        log(h + " - no file in the call")
        return
    done_paths = []
    for p, lang in hits:
        if not p.startswith(ui.read_settings()["media_root"].rstrip("/") + "/"):   # Media -> Media root
            log("outside the media root, skipping: " + p)
        elif not p.lower().endswith(".mkv"):
            andelse = os.path.splitext(p)[1].lower()
            _sel = {".mp4": "mp4_plex_select", ".m4v": "m4v_plex_select", ".mov": "mov_plex_select"}
            if andelse in _sel and inst.get(_sel[andelse], andelse != ".mov"):
                log("%s holds no flags - Plex selection only: %s" % (andelse[1:], p))
                done_paths.append(p)                       # counts towards the batch so the Plex step runs
            else:
                log("not mkv, skipping: " + p)
        elif not os.path.isfile(p):
            log("file not found: " + p)
        elif not lang:
            log("original language unknown: " + p)
        elif not (inst.get("mkv_flags_audio", True) or inst.get("mkv_flags_subs", True)):
            log("mkv flags off in UI, leaving the file: " + p)
            if inst.get("mkv_plex_select", True):
                done_paths.append(p)
        else:
            r = subprocess.run(["sh", SETDEFAULTS, "--original", lang, p],
                               capture_output=True, text=True,
                               env=dict(os.environ, **ui.script_env()))
            if r.returncode == 0:
                log("OK (" + lang + ") " + p + " :: " + r.stdout.strip())
                if inst.get("mkv_plex_select", True):
                    done_paths.append(p)
            else:
                log("ERROR " + p + ": " + (r.stdout + r.stderr).strip())
    if done_paths:
        # One webhook can carry several files, sometimes in several folders (a
        # season pack). Queue each folder separately with its own count.
        per_mapp = {}
        for p in done_paths:
            m = os.path.dirname(p)
            per_mapp[m] = per_mapp.get(m, 0) + 1
        for m, n in per_mapp.items():
            post_process(m, n)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if ui.handle_post(self, body, log):
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            log("could not parse the call as JSON")
            return
        threading.Thread(target=handle_import, args=(data,), daemon=True).start()

    def do_GET(self):
        if ui.handle_get(self, LOGFILE, START_TIME):
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"webhook_listener running\n")

    def log_message(self, *a):
        pass


ui.load_env()          # connections from settings.json -> os.environ
ui.start_scheduler(log)    # the weekly check, scheduled here instead of in DSM
log("listening on port " + str(PORT))
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
