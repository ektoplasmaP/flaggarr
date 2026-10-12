#!/usr/bin/env python3
# ui.py - the flaggarr web page, served by webhook_listener.py on port 8899.
# Side menu: Overview, Languages, Run, Statistics, Log, Connections, General, About.
#
# The settings live in /config/settings.json and are the source of everything:
#   - the listener reads them on every event (switches, times, margin)
#   - the scripts get the language order as environment variables (see settings_env.py)
# When a key is missing DEFAULTS applies, which is exactly what the scripts had built in.
#
# Sign-in: optional. Without a password the page is open (home network). With a password
# set (General -> Security) sign-in is required everywhere, and the webhook from
# Radarr/Sonarr only answers direct calls - not through DSM's reverse proxy (X-Forwarded-For).

import ipaddress
import json
import os
import re
import signal
import subprocess
import threading
from datetime import datetime

VERSION = "0.86.10.10"          # shown in the footer. Bumped on every change to ui.py/the listener.

# The program lives in the image (/app), everything this installation owns lives
# in the mounted volume (/config): settings, logs, backups.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.environ.get("FLAGGARR_CONFIG_DIR", "/config")   # the mount; overridable so the tests need no /config
SETTINGS = CONFIG_DIR + "/settings.json"
LOG_DIR = CONFIG_DIR + "/log"

DEFAULTS = {
    # switches
    "listener": True,          # accept imports from Radarr/Sonarr
    "mkv_flags_audio": True,   # default AUDIO flag written into the file (mkvpropedit)
    "mkv_flags_subs": True,    # default SUBTITLE flag written into the file (mkvpropedit)
    "plex_rescan": True,    # step 2: ask Plex to re-read the folder
    "plex_audio": True,     # step 3a: the audio pick Plex saves
    "plex_subs": True,     # step 3b: the subtitle pick Plex saves
    # the listener's times
    "batch_sec": 15,            # gathers imports that arrive close together
    "after_scan_sec": 60,      # wait for Plex after the rescan
    "recent_min": 5,          # smallest number of "recently added" the Plex pick looks at
    "margin": 5,             # added to the number of files in the batch
    # languages - universal lists (ISO 639-2, space separated). The scripts get them via settings_env.py
    "native_langs": "eng",         # understood without text: an original in these -> no subtitle
    "same_sub_langs": "",          # text in the same language as the original
    "sub_langs": "eng",            # otherwise: first in the list that exists as a text track
    "audio_priority": "orig eng",       # the audio order; "orig" is the title's own language
    "audio_codec_rules": "",   # per-codec rules, e.g. truehd:prefer dtshd:avoid
    "import_url": "",                # the webhook address for Radarr/Sonarr, shown under Automation
    "sub_rules": "eng:none",            # per spoken language: none (understood) or same (text in it);
    "alt_tag": "",                      # titles carrying this Radarr/Sonarr tag follow the two lines below
    "alt_tag_radarr": True,             # where that tag is read from: Radarr (films) ...
    "alt_tag_sonarr": True,             # ... and Sonarr (series)
    "audio_map": "",                    # original language -> the language to hear instead, plus
                                        # "other:orig|list|skip" for the ones with no row of their own.
                                        # Only used when the placeholder is out of audio_priority
    "alt_audio_priority": "eng orig",       # instead of audio_priority - dubbed animation, say
    "alt_sub_rules": "eng:none",        # instead of sub_rules; sub_langs is shared with the main set
    "alt_audio_map": "",                # the tag rule's own per-language table
                                       # a language with no rule here falls to sub_langs
    # derived from sub_rules by apply_sub_rules() - the scripts read these
    # the weekly check is run by the listener itself (no DSM task): weekday 0=Monday..6=Sunday, "HH:MM"
    "weekly_on": True,
    "use_quarantine": True,                             # off: the cleanup jobs delete straight away
    "weekly_cleanup": False,                            # quarantine pass before the weekly flag run
    "weekly_day": 6,
    "weekly_time": "06:00",
    # media
    "media_root": "/volume1/PlexMedia",                 # only files under here are touched
    "movies_root": "",                                  # Radarr's library path; empty = use media_root
    "series_root": "",                                  # Sonarr's library path; empty = use media_root
    "tag_all_users": True,                              # the tag rule also applies to accounts with their own profile
    "quickstart_done": False,                           # the first-run wizard: shown until it is finished or skipped
    "quarantine": "/volume1/PlexMedia/Removed Subs",
    "trash_categories": "orphaned",                     # which Remove trash files categories the job includes
    "quarantine_trash": "flaggarr_trashfiles",                   # subfolder: Remove trash files
    "quarantine_langs": "flaggarr_unwanted_language",            # subfolder: Remove unwanted subs
    "native_tag": "",                        # the Radarr/Sonarr tag for native-language originals
    "unwanted_langs": "eng",                          # languages Remove unwanted subs removes (ISO 639-2, exported as 639-1)
    # subtitle tracks (what may be picked, and in what order within the same language)
    "sub_sdh": "never",            # never | last | allowed | first
    "sub_forced": "last",          # never | last | allowed | first
    "sub_pgs": "never",            # never | last | allowed | first  (Blu-ray picture subs)
    "sub_vobsub": "never",         # never | last | allowed | first  (DVD picture subs)
    "sub_ass": "never",            # never | last | allowed | first  (ASS/SSA styled subs)
    "sub_forced_mode": "nofull",   # nofull | native - forced subtitles also for a language you understand
    "sub_track_order": "text forced",
    "sub_track_display_order": "",   # UI only: the full row order incl. unticked rows, so ticking never moves a row  # the order in the UI; mirrored to the three keys above
    "mp4_plex_select": True,
    "m4v_plex_select": True,           # mp4/m4v: no flags can be written, but the Plex pick still runs
    "mov_plex_select": True,           # mov: no flags either, but the Plex pick still runs
    "mkv_plex_select": True,           # mkv is flagged in the file; this also sets the Plex pick (fallback)
    # sakerhet
    "auth_lan": True,              # the password is asked for at home too; off exempts the home network
    "signout_on": True,            # sign a browser out automatically after signout_min minutes
    "signout_min": "60",          # counted from sign-in; only used when signout_on is set
    "users_no_image_subs": False,  # per-user passes: never pick PGS or VobSub for shared accounts
    "brute_on": False,             # lock the address after too many failed sign-ins
    "brute_tries": 5,             # number of failed attempts before the lockout kicks in
    "brute_reset_days": 7,        # a wrong sign-in is forgotten this many days after the last one
    # general
    "log_max_lines": 20000,                          # the listener log is trimmed to this once it grows 1.5x
    "job_log_max_mb": 2,                             # a job log is capped by size, not by line count
}
SWITCHES = ("alt_tag_radarr", "alt_tag_sonarr", "tag_all_users", "quickstart_done", "listener", "mkv_flags_audio", "mkv_flags_subs", "plex_rescan", "plex_audio", "plex_subs", "weekly_on", "weekly_cleanup", "use_quarantine", "auth_lan", "signout_on", "users_no_image_subs", "mp4_plex_select", "m4v_plex_select", "mov_plex_select", "mkv_plex_select", "brute_on")
# Languages the page can choose from - the same list as LANGUAGE_CODES in auto_set_plex_defaults.py.
# (name as Plex uses it, ISO 639-2 code as the mkv flag uses it)
LANGUAGES = [("english", "eng"), ("swedish", "swe"), ("norwegian", "nor"), ("danish", "dan"),
         ("finnish", "fin"), ("icelandic", "ice"), ("german", "ger"), ("french", "fre"),
         ("spanish", "spa"), ("italian", "ita"), ("dutch", "dut"), ("portuguese", "por"),
         ("polish", "pol"), ("russian", "rus"), ("ukrainian", "ukr"), ("japanese", "jpn"),
         ("korean", "kor"), ("chinese", "chi"), ("hindi", "hin")]
CODE_FIELDS = ("audio_priority", "native_langs", "same_sub_langs", "sub_langs", "unwanted_langs")   # stored as codes
# ISO 639-2 -> 639-1, for scripts that want two-letter codes (manual_unwanted_language_removal.py --lang)
ISO1 = {"eng": "en", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi", "ice": "is", "ger": "de", "fre": "fr",
        "spa": "es", "ita": "it", "dut": "nl", "por": "pt", "pol": "pl", "rus": "ru", "ukr": "uk", "jpn": "ja",
        "kor": "ko", "chi": "zh", "hin": "hi"}
NUMBERS = ("batch_sec", "after_scan_sec", "recent_min", "margin", "weekly_day", "log_max_lines", "brute_tries", "brute_reset_days", "job_log_max_mb")
TEXT_FIELDS = ("audio_priority", "audio_codec_rules", "import_url", "weekly_time", "signout_min",
        "media_root", "movies_root", "series_root", "quarantine", "trash_categories", "quarantine_trash", "quarantine_langs", "native_tag", "unwanted_langs", "native_langs", "same_sub_langs", "sub_langs",
        "sub_sdh", "sub_forced", "sub_forced_mode", "sub_pgs", "sub_vobsub", "sub_ass", "sub_track_order", "sub_track_display_order", "sub_rules", "audio_map", "alt_tag", "alt_audio_priority",
        "alt_sub_rules", "alt_audio_map")

# name -> (title, description, {mode: command}, log file). The order is the order of the chain:
# clean the disk first, then the flags in the files, last the Plex picks that choose among what is there.
TRASH_CATEGORIES = ("orphaned", "redundant", "subs_folder")

JOBS = {
    "trash": ("Remove trash files",
              "No video beside them, a copy of a subtitle track that is already inside the video, or tucked away in a Subs folder.",
              {"dry": ["sh", APP_DIR + "/run_trashfiles_cleanup.sh", "dry"],
               "apply": ["sh", APP_DIR + "/run_trashfiles_cleanup.sh", "apply"],
               "delete": ["sh", APP_DIR + "/run_trashfiles_cleanup.sh", "delete"]},
              LOG_DIR + "/manual_trashfiles_cleanup.log"),
    "langs": ("Remove unwanted subs",
              "A language you do not want, beside a title carrying the tag set under Cleanup.",
              {"dry": ["sh", APP_DIR + "/run_unwanted_language_removal.sh", "dry"],
               "apply": ["sh", APP_DIR + "/run_unwanted_language_removal.sh", "apply"],
               "delete": ["sh", APP_DIR + "/run_unwanted_language_removal.sh", "delete"]},
              LOG_DIR + "/manual_unwanted_language_removal.log"),
    "weekly": ("Set MKV flags — whole library",
                  "Goes through every file and writes the default audio and subtitle flags where they are missing or wrong. Same as the scheduled weekly check.",
                  {"dry": ["python3", APP_DIR + "/auto_mkv_defaultflags_batch.py", "--quiet"],
                   "apply": ["sh", APP_DIR + "/schedule_helper.sh"]},
                  LOG_DIR + "/ui_weekly.log"),
    "plex": ("Set Plex selections — whole library",
                "Rewrites the saved audio and subtitle track in Plex for every title, not just recent ones. Dry run shows what would change.",
                {"dry": ["python3", APP_DIR + "/auto_set_plex_defaults.py"],
                 "apply": ["python3", APP_DIR + "/auto_set_plex_defaults.py", "--apply"]},
                LOG_DIR + "/ui_plex.log"),
}

# key -> (title, file, category). "listener" is in both - filtered on category.
LOG_FILES = {
    "listener": ("Listener", LOG_DIR + "/webhook_listener.log", None),
    "error": ("Crashes (stderr)", LOG_DIR + "/webhook_listener.err", "system"),
    "trash": ("Remove trash files", LOG_DIR + "/manual_trashfiles_cleanup.log", "action"),
    "langs": ("Remove unwanted subs", LOG_DIR + "/manual_unwanted_language_removal.log", "action"),
    "weekly": ("Set MKV flags", LOG_DIR + "/ui_weekly.log", "action"),
    "plex": ("Set Plex selections", LOG_DIR + "/ui_plex.log", "action"),
}

_status = {n: {"running": False, "last_run": None, "mode": None, "rc": None, "stopped": False, "queued": None} for n in JOBS}
_lock = threading.Lock()
_procs = {}          # job name -> subprocess.Popen, only while that job is running


# ---------- settings ----------

TRACK_KEYS = (("sdh", "sub_sdh"), ("forced", "sub_forced"), ("pgs", "sub_pgs"),
              ("vobsub", "sub_vobsub"), ("ass", "sub_ass"))


PLEX_ROW = "skip"          # the reserved row that means: write nothing here
FOLLOW_ROW = "follow"      # the sub_rules row that means: take the audio's word for it
NEVER_ROW = "never"        # the sub_rules row that means: never any subtitle at all
ORIGINAL_ROW = "orig"      # the audio-order row that stands for the title's own
                           # language; track_rules.ORIGINAL is the same string


def apply_sub_rules(s):
    """Mirrors sub_rules to native_langs/same_sub_langs, which the scripts read,
    and keeps the audio order free of repeats and of the placeholder twice over."""
    pairs = []
    for bit in str(s.get("sub_rules") or "").split():
        code, _, mode = bit.partition(":")
        if code and code != ORIGINAL_ROW and code not in [p[0] for p in pairs]:
            base = mode[:-7] if mode.endswith("+forced") else mode      # "swe+forced": forced tracks only
            if mode not in ("none", "") and not (len(base) == 3 and base.isalpha() and base != code):
                mode = "same"                          # none, same, or the language to read instead
            if code in (NEVER_ROW, FOLLOW_ROW, PLEX_ROW):
                mode = ""                              # reserved rows carry no value
            pairs.append((code, mode))
    held = (PLEX_ROW, FOLLOW_ROW, NEVER_ROW)           # reserved rows, not languages
    s["native_langs"] = " ".join(k for k, m in pairs if m == "none" and k not in held)
    s["same_sub_langs"] = " ".join(k for k, m in pairs if m == "same" and k not in held)
    if any(k == FOLLOW_ROW for k, _ in pairs):
        heard = []                                     # what you hear you understand,
        for code in str(s.get("audio_priority") or "").split():
            if code not in (ORIGINAL_ROW, PLEX_ROW) and code not in heard:
                heard.append(code)
        for bit in str(s.get("audio_map") or "").split():
            want = bit.partition(":")[2]
            if want and want not in ("list", ORIGINAL_ROW, PLEX_ROW) and want not in heard:
                heard.append(want)
        s["native_langs"] = " ".join(heard)            # so the table is ignored
        s["same_sub_langs"] = ""                       # and nothing gets its own text
    if any(k == NEVER_ROW for k, _ in pairs):
        pairs = [(NEVER_ROW, "")]                      # never any subtitle: the table says nothing else
        s["native_langs"] = ""
        s["same_sub_langs"] = ""
    s["sub_rules"] = " ".join(k if m == "" else "%s:%s" % (k, m) for k, m in pairs)
    order = []
    for code in str(s.get("audio_priority") or "").split():
        if code not in order:
            order.append(code)
    if ORIGINAL_ROW in order and order[0] != ORIGINAL_ROW:   # first, last or absent - never
        order = [c for c in order if c != ORIGINAL_ROW]      # in between, so a list that has
        order.append(ORIGINAL_ROW)                           # it somewhere else means "prefer a dub"
    s["audio_priority"] = " ".join(order)
    return s


def apply_alt_rules(s):
    """The tag rule keeps its own order and its own table, normalised the same way.
    It borrows sub_langs from the main set - one fallback list is enough."""
    t = {"audio_priority": s.get("alt_audio_priority") or "", "sub_rules": s.get("alt_sub_rules") or "",
         "audio_map": s.get("alt_audio_map") or ""}
    apply_sub_rules(t)
    s["alt_audio_priority"] = t["audio_priority"]
    s["alt_sub_rules"] = t["sub_rules"]
    s["alt_native_langs"] = t["native_langs"]
    s["alt_same_sub_langs"] = t["same_sub_langs"]
    return s


def apply_track_order(s):
    """Mirrors sub_track_order to sub_sdh/sub_forced/sub_pgs/sub_vobsub/sub_ass, which the scripts read."""
    parts = str(s.get("sub_track_order") or "").split()
    parts = [x for d in parts for x in (("pgs", "vobsub", "ass") if d == "bitmap" else (d,))]
    parts = [d for d in parts if d in ("text", "sdh", "forced", "pgs", "vobsub", "ass")]
    parts = [d for i, d in enumerate(parts) if d not in parts[:i]]
    if "text" not in parts:
        parts.append("text")
    boundary = parts.index("text")
    for t, k in TRACK_KEYS:
        s[k] = "never" if t not in parts else ("first" if parts.index(t) < boundary else "last")
    s["sub_track_order"] = " ".join(parts)
    return s


def read_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if d and "quickstart_done" not in d:
        d["quickstart_done"] = True                # an installation that already has settings is not a first run
    for k in SWITCHES:
        if k in d:
            s[k] = bool(d[k])
    for k in NUMBERS:
        try:
            s[k] = max(0, int(d.get(k, s[k])))
        except (TypeError, ValueError):
            pass
    for k in TEXT_FIELDS:
        v = str(d.get(k) or "").strip()
        if v:
            s[k] = " ".join(v.split())
    for k in ("quarantine_trash", "quarantine_langs"):        # a folder name only, never a path
        s[k] = s[k].replace("\\", "/").strip("/ ").split("/")[0] or DEFAULTS[k]
    apply_sub_rules(s)
    apply_alt_rules(s)
    return s


def _read_raw():
    try:
        with open(SETTINGS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    d = _read_raw()
    d.update(s)
    with open(SETTINGS, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


# ---------- sign-in ----------

import hashlib
import hmac
import secrets
import time

_fails = {}          # ip -> [failed attempts, time of the most recent]
COOKIE_NAME = "flaggarr"


def password_set():
    return bool(_read_raw().get("password"))


def _hash(pw, salt=None, it=200000):
    salt = salt or secrets.token_hex(16)
    d = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), it).hex()
    return "pbkdf2$%d$%s$%s" % (it, salt, d)


def check_password(pw):
    stored = _read_raw().get("password") or ""
    try:
        _, it, salt, _d = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(_hash(pw, salt, int(it)), stored)


def set_password(pw):
    """A new password and a new secret, so every old cookie stops being valid."""
    save_settings({"password": _hash(pw) if pw else "", "secret": secrets.token_hex(32)})


RESET_FILE = CONFIG_DIR + "/RESET_PASSWORD"
NEW_PASSWORD_FILE = CONFIG_DIR + "/NEW_PASSWORD.txt"


def reset_credentials(log=None, source="the page"):
    """Sets a freshly generated password and writes it to config/NEW_PASSWORD.txt."""
    new_value = secrets.token_urlsafe(9)
    set_password(new_value)
    try:
        with open(NEW_PASSWORD_FILE, "w") as f:
            f.write("password: %s\n\nSign in and set your own password, then delete this file.\n" % new_value)
        os.chmod(NEW_PASSWORD_FILE, 0o600)
    except OSError:
        pass
    if log:
        log("UI: sign-in reset from %s - new random password in config/NEW_PASSWORD.txt" % source)
    return new_value


def check_reset(log=None):
    """Forgot the password? Create an empty file config/RESET_PASSWORD - on the next call a
    freshly generated password is set and written to config/NEW_PASSWORD.txt.
    RESET_PASSWORD is deleted afterwards."""
    if not os.path.exists(RESET_FILE):
        return
    reset_credentials(log, "config/RESET_PASSWORD")
    try:
        os.remove(RESET_FILE)
    except OSError:
        pass


def _secret():
    d = _read_raw()
    if not d.get("secret"):
        save_settings({"secret": secrets.token_hex(32)})
        d = _read_raw()
    return d["secret"]


def _sig(exp):
    return hmac.new(_secret().encode(), str(exp).encode(), "sha256").hexdigest()


def _client(h):
    """The address to trust. X-Forwarded-For is a chain the caller can prepend to, so the first
    entry is whatever they felt like writing - only the LAST one was added by our own reverse
    proxy. Taking that one keeps the brute force lock and the log lines honest.

    But the header is only worth anything when the request really came from a proxy of ours.
    Reached directly - a port forwarded straight to 8899 - the caller writes the whole chain
    themselves, and a new address per guess would hand them a fresh brute force counter every
    time and a made-up address in the log. So the header counts only from a peer on our own
    network, and from anywhere else the socket has the last word."""
    peer = h.client_address[0] if h.client_address else ""
    if _private(peer):
        xf = h.headers.get("X-Forwarded-For")
        if xf:
            parts = [p.strip() for p in xf.split(",") if p.strip()]
            if parts:
                return parts[-1]
        rip = (h.headers.get("X-Real-IP") or "").strip()
        if rip:
            return rip
    return peer


def _private(addr):
    """True for loopback, the docker bridge, the home network and overlay ranges - the places
    a reverse proxy of ours can sit."""
    try:
        a = ipaddress.ip_address((addr or "").strip())
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a.version == net.version and a in net for net in LAN_NETS)


def via_proxy(h):
    return bool(h.headers.get("X-Forwarded-For") or h.headers.get("X-Real-IP"))


LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "100.64.0.0/10",      # the last one carries Tailscale and similar overlays
    "::1/128", "fc00::/7", "fe80::/10",
))


def from_lan(h):
    """True only for a request that really arrived from a private address.

    Judged on the socket's peer address and never on a header, because
    X-Forwarded-For and X-Real-IP are written by whoever is talking to us. A
    request carrying either came through a proxy, and the peer is then the
    proxy, which says nothing about who is behind it - so it counts as remote.

    One caveat worth knowing: if Docker is publishing the port through its
    userland proxy, every request reaches the container from the bridge
    gateway and so looks local. That is why a password, once set, is required
    everywhere by default rather than only off the home network.
    """
    if via_proxy(h):
        return False
    try:
        addr = ipaddress.ip_address((h.client_address[0] or "").strip())
    except (AttributeError, IndexError, TypeError, ValueError):
        return False                        # no address to judge: treat it as remote
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr.version == net.version and addr in net for net in LAN_NETS)


def note_direct_host(h):
    """Remember the address of the last direct (non-proxy) request - an
    address like that is reachable on the home network, so it is what
    Radarr and Sonarr should send webhooks to. Shown under Automation."""
    if not from_lan(h):
        return
    host = (h.headers.get("Host") or "").strip()
    if not host or host.split(":")[0] in ("localhost", "127.0.0.1"):
        return
    url = "http://" + host
    try:
        if _read_raw().get("import_url") != url:
            save_settings({"import_url": url})
    except OSError:
        pass


def signed_in(h):
    if not password_set():
        return False            # nothing is signed in until a password exists
    if from_lan(h) and not read_settings().get("auth_lan", True):
        return True                         # the owner has exempted the home network
    for bit in (h.headers.get("Cookie") or "").split(";"):
        k, _, v = bit.strip().partition("=")
        if k == COOKIE_NAME:
            tok, _, sig = v.partition(".")
            if tok.startswith("i") and tok[1:].isdigit() and hmac.compare_digest(sig, _sig(tok)):
                s = read_settings()
                dur = max(1, int(s.get("signout_min") or 60)) * 60 if s.get("signout_on") else 30 * 86400
                if int(tok[1:]) + dur > time.time():
                    return True
    return False


def _cookie(h, value, maxage):
    secure = "; Secure" if h.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
    return "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict%s" % (COOKIE_NAME, value, maxage, secure)


def _window_cookie(h, value):
    """Same cookie, but without Max-Age: it lives until the browser window is closed. Used when
    the local-network requirement is switched on - the window that switched it on stays signed in,
    and the next one has to sign in."""
    secure = "; Secure" if h.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
    return "%s=%s; Path=/; HttpOnly; SameSite=Strict%s" % (COOKIE_NAME, value, secure)


def _forget_old_fails(s):
    """Wrong sign-ins are only remembered for brute_reset_days days. Once the most recent one
    from an address is older than that the address starts over from zero. 0 keeps them until
    flaggarr restarts."""
    days = max(0, int(s.get("brute_reset_days", 7) or 0))
    if not days:
        return
    cutoff = time.time() - days * 86400
    for ip in [k for k, v in _fails.items() if v[1] < cutoff]:
        _fails.pop(ip, None)


FAIL_WINDOW = 300       # how long a lock lasts, and the window the whole-door count looks at
FAIL_MAX = 5000         # the most addresses remembered at once


def _recent_fails():
    """Failed attempts from every address together, inside the lock window."""
    now = time.time()
    return sum(n for n, t in _fails.values() if now - t < FAIL_WINDOW)


def _locked_out(ip):
    s = read_settings()
    _forget_old_fails(s)
    if not s.get("brute_on", True):
        return False
    boundary = max(1, int(s.get("brute_tries", 5) or 5))
    n, t = _fails.get(ip, (0, 0))
    if n >= boundary and time.time() - t < FAIL_WINDOW:
        return True
    # Behind our own proxy the caller writes the address we see, so a lock on one address is
    # walked around by sending a new one per guess. Once the failures from everywhere together
    # pass four times the limit the whole door closes for the same five minutes.
    if _recent_fails() >= boundary * 4:
        return True
    return False


def sign_in(h, pw):
    ip = _client(h)
    if _locked_out(ip):
        return None, "too many attempts - wait five minutes"
    if check_password(pw or ""):
        _fails.pop(ip, None)
        try:                      # signed in - NEW_PASSWORD.txt is no longer needed
            os.remove(NEW_PASSWORD_FILE)
        except OSError:
            pass
        iss = "i%d" % int(time.time())            # issue time - signed_in applies the sign-out setting live
        return "%s.%s" % (iss, _sig(iss)), None
    n, _ = _fails.get(ip, (0, 0))
    _fails[ip] = (n + 1, time.time())
    if len(_fails) > FAIL_MAX:            # a caller rotating addresses cannot grow this without end
        for k in sorted(_fails, key=lambda k: _fails[k][1])[:len(_fails) // 2]:
            _fails.pop(k, None)
    time.sleep(0.5)
    return None, "wrong password"



# ---------- plex sign-in (per-user sessions) ----------

def _plex_client_id():
    d = _read_raw().get("plex_client_id")
    if isinstance(d, str) and d:
        return d
    import secrets as _secrets
    cid = _secrets.token_hex(12)
    save_settings({"plex_client_id": cid})
    return cid


def _plextv(path, token=None, method="GET"):
    import urllib.request
    headers = {"Accept": "application/json", "X-Plex-Product": "flaggarr",
               "X-Plex-Client-Identifier": _plex_client_id()}
    if token:
        headers["X-Plex-Token"] = token
    req = urllib.request.Request("https://plex.tv" + path, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def plex_pin_new():
    d = _plextv("/api/v2/pins?strong=true", method="POST")
    return {"id": d.get("id"), "code": d.get("code"), "client": _plex_client_id()}


def plex_pin_user(pin_id):
    """None while the pin is unapproved; (uid, name) once approved - uid empty when the account has no share."""
    d = _plextv("/api/v2/pins/%d" % int(pin_id))
    token = d.get("authToken")
    if not token:
        return None
    me = _plextv("/api/v2/user", token=token)
    uid = str(me.get("id") or "")
    for x in plex_users(True):
        if x["id"] == uid:
            return uid, x["name"]
    own = (active_env().get("PLEX_TOKEN") or "").strip()
    if own:
        try:
            if str(_plextv("/api/v2/user", token=own).get("id") or "") == uid:   # the server owner - full admin
                return "@owner", str(me.get("username") or "owner")
        except Exception:
            pass
    return "", "%s (plex id %s)" % (str(me.get("username") or "?"), uid or "?")


def user_cookie(uid):
    tok = "u%s-%d" % (uid, int(time.time()))
    return "%s.%s" % (tok, _sig(tok))


def session_user(h):
    """The uid of a Plex-signed-in user session, else None. The owner uses signed_in()."""
    for bit in (h.headers.get("Cookie") or "").split(";"):
        k, _, v = bit.strip().partition("=")
        if k == COOKIE_NAME:
            tok, _, sig = v.partition(".")
            if tok.startswith("u") and "-" in tok and hmac.compare_digest(sig, _sig(tok)):
                uid, _, iss = tok[1:].partition("-")
                if iss.isdigit() and int(iss) + 30 * 86400 > time.time():
                    return uid
    return None


def user_row(uid):
    base = read_settings()
    p = user_profiles().get(uid)
    if not isinstance(p, dict):
        p = {}
    name = str(p.get("name") or "") or next((x["name"] for x in plex_users() if x["id"] == uid), "?")
    return {"id": uid, "name": name, "enabled": bool(p.get("enabled")),
            "own": str(p.get("own") or ""), "adm": str(p.get("adm") or ""), "rst": str(p.get("rst") or ""),   # the guest page reads own: set up before, or a first visit
            "profile": profile_view(p, base)}


def user_status(uid):
    """What a per-user session may see: their own profile and the lists the editor needs."""
    row = user_row(uid)
    return {"version": VERSION, "me": {"id": uid, "name": row["name"], "enabled": row["enabled"]},
            "languages": LANGUAGES, "code_fields": list(CODE_FIELDS),
            "settings": dict(row["profile"]), "defaults": dict(DEFAULTS),
            "server": {k: str(read_settings()[k]) for k in USER_KEYS},   # what applies while the profile is off
            "no_img": bool(read_settings().get("users_no_image_subs")),
            "jobs": {}, "stat": {}, "events": [], "auth": True, "signed": True,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def script_env(s=None):
    """Environment variables the scripts read - the same ones settings_env.py prints."""
    s = s or read_settings()
    d = {k.upper(): str(s[k]) for k in TEXT_FIELDS}
    for k in ("native_langs", "same_sub_langs", "sub_langs"):      # the Plex script wants names
        d[k.upper() + "_NAMES"] = names_from_codes(s[k])
    d["UNWANTED_LANGS"] = " ".join(ISO1.get(k, k) for k in s["unwanted_langs"].split())   # 639-1 for the cleanup script
    d["TRASH_CATEGORIES"] = ",".join([k for k in d.get("TRASH_CATEGORIES", "").split(",") if k in TRASH_CATEGORIES]) or "orphaned"
    d["MEDIA_ROOTS"] = ":".join(MEDIA_ROOTS_())          # the .sh scripts loop over these
    d["MKV_FLAGS_AUDIO"] = "1" if s.get("mkv_flags_audio", True) else "0"
    d["MKV_FLAGS_SUBS"] = "1" if s.get("mkv_flags_subs", True) else "0"
    if s.get("users_no_image_subs"):
        d["SUB_PGS"] = d["SUB_VOBSUB"] = "never"                # the switch applies to everyone
    for k in ("alt_native_langs", "alt_same_sub_langs"):
        d[k.upper()] = str(s.get(k) or "")
        d[k.upper() + "_NAMES"] = names_from_codes(s.get(k) or "")
    if str(s.get("alt_tag") or "").strip():
        d["FLAGGARR_TAG_MAP"] = write_tag_map()
    d.update({k: v for k, v in active_env().items() if v})
    return d


def names_from_codes(codes):
    """'swe eng' -> 'swedish english' (the Plex names auto_set_plex_defaults.py uses)."""
    table = {k: n for n, k in LANGUAGES}
    return " ".join(table.get(k, k) for k in str(codes).split())


# ---------- connections ----------

# key -> (title, secret?). The order is the order the page shows.
ENV_FIELDS = [
    ("PLEX_URL", "Plex URL", False), ("PLEX_TOKEN", "Plex token", True),
    ("RADARR_URL", "Radarr URL", False), ("RADARR_APIKEY", "Radarr API key", True),
    ("SONARR_URL", "Sonarr URL", False), ("SONARR_APIKEY", "Sonarr API key", True),
    ("BAZARR_URL", "Bazarr URL", False), ("BAZARR_APIKEY", "Bazarr API key", True),
]


def read_env():
    """The connections (PLEX_URL, PLEX_TOKEN, RADARR_URL ...) from settings.json, key 'connections'."""
    d = _read_raw().get("connections")
    return {k: str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def save_env(new_value):
    d = read_env()
    d.update({k: str(v).strip() for k, v in new_value.items() if k})
    save_settings({"connections": d})
    for k, v in d.items():                 # the listener and everything it starts should see the new values at once
        os.environ[k] = v
    return d


def load_env():
    """Run by the listener at start: put the connections into os.environ (plex_rescan_folder reads them there)."""
    for k, v in active_env().items():
        if v and not os.environ.get(k):
            os.environ[k] = v


def env_status():
    d = read_env()
    return [{"key": k, "label": r, "secret": secret, "value": d.get(k, "")} for k, r, secret in ENV_FIELDS]


# id -> (title, url key, api key). The order is the order of the tabs.
SERVICES = [
    ("plex", "Plex", "PLEX_URL", "PLEX_TOKEN"),
    ("radarr", "Radarr", "RADARR_URL", "RADARR_APIKEY"),
    ("sonarr", "Sonarr", "SONARR_URL", "SONARR_APIKEY"),
    ("bazarr", "Bazarr", "BAZARR_URL", "BAZARR_APIKEY"),
]


def services_status():
    d = read_env()
    return [{"id": i, "label": r, "urlkey": u, "keykey": k, "url": d.get(u, ""), "key": d.get(k, ""),
              "enabled": service_enabled(i, d)}
            for i, r, u, k in SERVICES]


def service_enabled(service, d=None):
    """A connection can be switched off on the Connections page. Off is stored as PLEX_ENABLED=0
    and friends; anything else, including a missing key, counts as on."""
    d = read_env() if d is None else d
    return str(d.get(service.upper() + "_ENABLED", "1")).strip().lower() not in ("0", "false", "off", "no")


def active_env():
    """The connections as the rest of the app should see them. A switched-off service has no
    address and no key here, which every consumer already treats as 'not set up' - so switching
    one off needs no special case anywhere else. The _ENABLED keys never reach the scripts."""
    d = read_env()
    for i, _r, u, k in SERVICES:
        if not service_enabled(i, d):
            d[u] = ""
            d[k] = ""
    return {key: v for key, v in d.items() if not key.endswith("_ENABLED")}


ICONS = {"plex": "/web/favicon.ico", "radarr": "/Content/Images/logo.svg", "sonarr": "/Content/Images/logo.svg",
        "bazarr": "/images/favicon.ico"}
_icon_cache = {}


def fetch_icon(service):
    """Fetches the app's own icon from the app (cached in memory). Returns (kind, bytes) or None."""
    import urllib.request
    if service in _icon_cache:
        return _icon_cache[service]
    row = next((t for t in SERVICES if t[0] == service), None)
    if not row or service not in ICONS:
        return None
    url = (read_env().get(row[2], "") or "").strip().rstrip("/")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        with urllib.request.urlopen(urllib.request.Request(url + ICONS[service], headers={"Accept": "image/*"}), timeout=6) as r:
            kind = r.headers.get("Content-Type", "image/x-icon").split(";")[0]
            data = r.read(200000)
    except Exception:
        return None
    if not data or "html" in kind:
        return None
    _icon_cache[service] = (kind, data)
    return _icon_cache[service]


def test_connection(service, url=None, key_name=None):
    """Tries a service. Returns {ok, info}. The key is never logged or returned."""
    import urllib.request
    import urllib.error
    import xml.etree.ElementTree as ET
    row = next((t for t in SERVICES if t[0] == service), None)
    if not row:
        return {"ok": False, "info": "unknown service"}
    d = read_env()
    url = (url if url is not None else d.get(row[2], "")).strip().rstrip("/")
    key_name = (key_name if key_name is not None else d.get(row[3], "")).strip()
    if not url:
        return {"ok": False, "info": "no address"}
    if not key_name:
        return {"ok": False, "info": "no token" if service == "plex" else "no API key"}
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        if service == "plex":
            req = urllib.request.Request(url + "/identity", headers={"X-Plex-Token": key_name, "Accept": "application/xml"})
        elif service == "bazarr":
            req = urllib.request.Request(url + "/api/system/status", headers={"X-API-KEY": key_name, "Accept": "application/json"})
        else:
            req = urllib.request.Request(url + "/api/v3/system/status", headers={"X-Api-Key": key_name, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            body = r.read().decode("utf-8", "replace")
        if service == "plex":
            root = ET.fromstring(body)
            ver = root.get("version") or "?"
            return {"ok": True, "info": "Plex Media Server " + ver}
        j = json.loads(body)
        if service == "bazarr":
            j = j.get("data", j)
            ver = j.get("bazarr_version") or j.get("version") or "?"
        else:
            ver = j.get("version") or "?"
        return {"ok": True, "info": row[1] + " " + str(ver)}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "info": "rejected (%d) - wrong %s?" % (e.code, "token" if service == "plex" else "API key")}
        return {"ok": False, "info": "HTTP %d" % e.code}
    except urllib.error.URLError as e:
        return {"ok": False, "info": "cannot reach: " + str(getattr(e, "reason", e))}
    except Exception as e:
        return {"ok": False, "info": "error: " + type(e).__name__}


# ---------- logs ----------

def _tail(p, n=15):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-n:]
    except OSError:
        return []


def _last_run(p, max_lines=600, tail=5000):
    """The whole output of the most recent run: everything after the last header line '<ts>  mode: ...'.
    max_lines=0 keeps every line (the statistics count the whole run, not a window of it)."""
    lines = _tail(p, tail)
    start = 0
    for i, r in enumerate(lines):
        if "mode:" in r and _LOGLINE.match(r):
            start = i
    out = [r for r in lines[start:] if not r.startswith("=====")]
    if max_lines and len(out) > max_lines:
        out = ["… (%d lines skipped)" % (len(out) - max_lines)] + out[-max_lines:]
    return out


_LOGLINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)$")


def _classify(txt):
    t = txt.lower()
    if "error" in t or "failed" in t or "cannot" in t or "not found" in t or "traceback" in t:
        return "error"
    if t.startswith("ui:"):
        return "ui"
    if "ignored" in t or "skipp" in t or "off in ui" in t or "rejected" in t:
        return "skip"
    if t.startswith("ok (") or " done" in t or "finished" in t:
        return "ok"
    if t.startswith("start"):
        return "start"
    return "info"


def _category(txt):
    """system = the listener itself; action = something was done to media."""
    t = txt.lower()
    if t.startswith("ui: start") or (t.startswith("ui:") and " done" in t):
        return "action"
    if t.startswith("ui:") or "listening on port" in t or "could not parse" in t \
       or t.endswith("- ignored") or "no file in the call" in t or "webhook via proxy" in t \
       or "signed in" in t or "sign-in" in t or "signed_in" in t or "weekly check scheduled" in t:
        return "system"
    return "action"


def log_entries(file, n=300, category=None):
    """The log as a list of {ts, text, kind, category} - newest first. category filters: system | action."""
    lines = _tail(file, n)
    out = []
    for r in lines:
        m = _LOGLINE.match(r)
        if m:
            out.append({"ts": m.group(1), "text": m.group(2), "kind": _classify(m.group(2)),
                       "category": _category(m.group(2))})
        elif out:
            out[-1]["text"] += "\n" + r          # continuation line (traceback, block)
        else:
            out.append({"ts": "", "text": r, "kind": "info", "category": "action"})
    if category:
        out = [p for p in out if p["category"] == category]
    out.reverse()
    return out


# ---------- jobs ----------

def job_label(name):
    """What the job is called in the UI - log lines use this, not the internal name."""
    return "Weekly check" if name == "weekly" else (JOBS[name][0] if name in JOBS else name)


def _run_script(name, mode, log, extra=None):
    title, _, modes, logfile = JOBS[name]
    cmd = modes[mode]
    jword = "%s (%s)" % (job_label(name), "dry run" if mode == "dry" else mode)
    start = datetime.now()
    pre_log = 0
    try:
        pre_log = os.path.getsize(logfile)
    except OSError:
        pass
    log("UI: starting %s" % jword)
    env = dict(os.environ)
    env.update(script_env())
    env["PYTHONUNBUFFERED"] = "1"          # child output arrives line by line, not in 8 kB chunks
    _rid = ""
    if mode == "apply" and name in ("weekly", "plex"):   # a real run keeps a journal, so it can be undone
        _rid = new_run_id()
        env["FLAGGARR_UNDO"] = undo_file(_rid, name)
    if name in ("trash", "langs"):                      # the quarantine folder must exist before the job runs
        try:
            os.makedirs(os.path.join(QUARANTINE_(), read_settings()[
                "quarantine_trash" if name == "trash" else "quarantine_langs"]), exist_ok=True)
        except OSError:
            pass
    if extra:
        env.update(extra)
    with _lock:
        _status[name]["stopped"] = False
    stream = None
    if name in ("weekly", "plex"):          # stream their output straight to the log, so progress shows live
        try:
            stream = open(logfile, "a", encoding="utf-8")
            stream.write("=" * 67 + "\n%s  mode: %s\n" % (start.strftime("%Y-%m-%d %H:%M:%S"), mode) + "=" * 67 + "\n")
            stream.flush()
            pre_log = stream.tell()
        except OSError:
            stream = None
    try:
        proc = subprocess.Popen(cmd, stdout=stream or subprocess.PIPE,
                                stderr=subprocess.STDOUT if stream else subprocess.PIPE, text=True, env=env,
                                start_new_session=True)   # its own process group, so Stop can reach its children too
        with _lock:
            _procs[name] = proc
        out_s, err_s = proc.communicate()
        rc = proc.returncode
        out = ((out_s or "") + (err_s or "")).strip()
    except Exception as e:
        rc, out = -1, str(e)
    if stream:
        try:
            stream.close()
        except OSError:
            pass
    if not out:
        try:  # jobs that write to the log file, not stdout - judge the run on what they appended
            with open(logfile) as _f:
                _f.seek(pre_log)
                out = _f.read().strip()
        except OSError:
            pass
    with _lock:
        stopped = _status[name]["stopped"]
        _procs.pop(name, None)
    if name == "plex" and rc == 0 and not stopped:
        for uname, uenv in user_runs():          # the same pass again, once per custom user profile
            with _lock:
                if _status[name]["stopped"]:
                    break
            u_rc = -1
            if _rid:                                  # the account gets a journal of its own
                uenv = dict(uenv, FLAGGARR_UNDO=undo_file(_rid, name, uenv.get("FLAGGARR_USER_ID", "")))
            try:
                with open(logfile, "a", encoding="utf-8") as uf:
                    uf.write("\n--- selections for %s ---\n" % uname)
                    uf.flush()
                    uproc = subprocess.Popen(cmd, stdout=uf, stderr=subprocess.STDOUT, text=True,
                                             env=dict(os.environ, PYTHONUNBUFFERED="1", **uenv),
                                             start_new_session=True)
                    with _lock:
                        _procs[name] = uproc
                    uproc.communicate()
                    u_rc = uproc.returncode
            except Exception:
                pass
            with _lock:
                _procs.pop(name, None)
            if u_rc != 0 and rc == 0:
                rc = u_rc
        with _lock:
            stopped = _status[name]["stopped"]
        try:
            with open(logfile) as _f:
                _f.seek(pre_log)
                out = _f.read().strip()
        except OSError:
            pass
    if name in ("weekly", "plex"):          # close the run block in the log
        try:
            with open(logfile, "a", encoding="utf-8") as f:
                f.write("\n")
        except OSError:
            pass
    with _lock:
        _status[name].update({"running": False, "last_run": datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "mode": mode, "rc": rc})
    if stopped:
        log("UI: %s stopped by request" % jword)
        return
    _oc, _err = job_outcome((out or "").splitlines(), rc)
    if _oc.startswith("errors:"):
        _word = "finished, %s error(s)" % _oc.split(":", 1)[1]
    else:
        _word = {"ok": "OK", "nothing": "nothing to do",
                 "none": "finished (no details logged)"}.get(_oc, "FAILED")
    log("UI: %s done, %s%s" % (jword, _word, (" - " + _err) if _err else ""))


def stop_job(name):
    """Sends SIGTERM to a running job's whole process group. Returns True if a process was found to signal."""
    if name not in _status:
        return False
    with _lock:
        proc = _procs.get(name)
        if not proc or proc.poll() is not None:
            return False
        _status[name]["stopped"] = True
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    return True


def run_job(name, mode, log, extra=None):
    if name not in JOBS or mode not in JOBS[name][2]:
        return False, "unknown job"
    with _lock:
        if _status[name]["running"]:
            return False, "already running"
        if name in ("weekly", "plex") and _cleanup_running():   # the library jobs wait for the cleanup
            if _status[name]["queued"]:
                return False, "already waiting for the cleanup"
            _queue.append((name, mode, extra))
            _status[name]["queued"] = mode
            first = len(_queue) == 1
        else:
            first = None
            _status[name]["running"] = True
    if first is not None:
        if first:
            threading.Thread(target=_run_queue, args=(log,), daemon=True).start()
        log("UI: %s queued, waiting for the cleanup" % job_label(name))
        return True, "queued - it starts as soon as the cleanup is done"
    threading.Thread(target=_run_script, args=(name, mode, log, extra), daemon=True).start()
    return True, "started"


_queue = []          # (job, mode, extra) - library jobs waiting for the cleanup, in the order asked for


def _cleanup_running():
    return any(_status[j]["running"] for j in ("trash", "langs"))


def _run_queue(log):
    """Waits for the cleanup to finish, then runs the queued library jobs one after another."""
    while True:
        time.sleep(3)
        with _lock:
            if _cleanup_running():
                continue
            if not _queue:
                return
            name, mode, extra = _queue.pop(0)
            _status[name]["queued"] = None
            if _status[name]["running"]:
                continue
            _status[name]["running"] = True
        log("UI: cleanup done, starting %s" % job_label(name))
        try:
            _run_script(name, mode, log, extra)      # blocks, so a second queued job waits its turn
        except Exception as e:
            with _lock:
                _status[name]["running"] = False
            log("queue error in %s: %s" % (name, e))


def dequeue_job(name):
    """Takes a waiting job out of the queue. True when it was there."""
    with _lock:
        for i, q in enumerate(_queue):
            if q[0] == name:
                _queue.pop(i)
                _status[name]["queued"] = None
                return True
    return False


def run_chain(steps, log):
    """Run several jobs one after another in a single thread, in the order given. Each step is (job, mode)."""
    steps = [(n, m) for n, m in steps if n in JOBS and m in JOBS[n][2]]
    if not steps:
        return False, "unknown job"
    with _lock:
        if any(_status[n]["running"] for n, _ in steps):
            return False, "already running"
        for n, _ in steps:
            _status[n]["running"] = True

    def _sequence():
        for n, m in steps:
            try:
                _run_script(n, m, log)
            except Exception as e:
                with _lock:
                    _status[n]["running"] = False
                log("chain error in %s: %s" % (n, e))

    threading.Thread(target=_sequence, daemon=True).start()
    return True, "started"


# ---------- the weekly check, scheduled by the listener itself ----------

_schedule_time = {"last_run": None, "next": None}


def _weekly_at(s):
    """The weekday and the time the weekly check is set to, as (day, hour, minute)."""
    try:
        h, m = [int(x) for x in str(s.get("weekly_time", "06:00")).split(":")]
    except ValueError:
        h, m = 6, 0
    return int(s.get("weekly_day", 6)) % 7, h, m


def _next_weekly(s, now=None):
    """The next time the weekly check is due. For the countdown on the page."""
    from datetime import timedelta
    day, h, m = _weekly_at(s)
    now = now or datetime.now()
    k = now.replace(hour=h, minute=m, second=0, microsecond=0)
    k += timedelta(days=(day - k.weekday()) % 7)
    if k <= now:
        k += timedelta(days=7)
    return k


def _weekly_due(s, now=None):
    """True while the clock is inside the minute the weekly check is set to.

    This is deliberately not "_next_weekly() == now". That comparison can
    never be true: _next_weekly always answers with a time in the future, so
    at the moment the target minute arrives it has already moved the target a
    week on, and the run was skipped every week instead of happening.
    """
    now = now or datetime.now()
    day, h, m = _weekly_at(s)
    return now.weekday() == day and now.hour == h and now.minute == m


def start_scheduler(log):
    """Thread that runs the weekly check (schedule_helper.sh) on the chosen weekday and time. Replaces the DSM task."""
    def _tick():
        while True:
            try:
                s = read_settings()
                _schedule_time["next"] = (_next_weekly(s).strftime("%Y-%m-%d %H:%M")
                                          if s.get("weekly_on") else None)
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                if s.get("weekly_on") and _weekly_due(s) and _schedule_time["last_run"] != stamp:
                    _schedule_time["last_run"] = stamp
                    if s.get("weekly_cleanup"):
                        clean = "apply" if s.get("use_quarantine", True) else "delete"
                        log("weekly check scheduled run, cleanup first (%s)" % ("quarantine" if clean == "apply" else "delete"))
                        run_chain([("trash", clean), ("langs", clean), ("weekly", "apply"), ("plex", "apply")], log)
                    else:
                        log("weekly check scheduled run")
                        run_job("weekly", "apply", log)
                time.sleep(20)
            except Exception as e:
                try:
                    log("scheduler error: " + str(e))
                except Exception:
                    pass
                time.sleep(60)
    threading.Thread(target=_tick, daemon=True).start()


def QUARANTINE_():
    return read_settings()["quarantine"].rstrip("/") or DEFAULTS["quarantine"]


def MEDIA_ROOT_():
    """Where the library is mounted. The mount wins: the setting is only a fallback for odd setups."""
    found = mounted_media()
    if found:
        return found[0].rstrip("/")
    return read_settings()["media_root"].rstrip("/") or DEFAULTS["media_root"]


_SKIP_MOUNTS = ("/config", "/proc", "/sys", "/dev", "/etc", "/var/lib", "/run", "/tmp", "/app")


def mounted_media():
    """The library folder as docker compose mounted it. The path is the same inside and outside the
    container, so it is set in compose, not here. Empty when nothing looks like a media mount."""
    out = []
    try:
        with open("/proc/self/mounts", "r") as fh:
            for line in fh:
                bits = line.split()
                if len(bits) < 2:
                    continue
                p = bits[1].replace("\\040", " ")
                if p in ("/",) or p.startswith(_SKIP_MOUNTS) or not os.path.isdir(p):
                    continue
                if p not in out:
                    out.append(p)
    except OSError:
        pass
    return out


def roots_state():
    """Per library field: does the folder exist, and does it sit inside what compose mounted?
    A path outside the mount is invisible to the container, so the jobs would find nothing there."""
    s = read_settings()
    mounts = [m.rstrip("/") for m in mounted_media()] or [MEDIA_ROOT_()]
    out = {}
    for k in ("movies_root", "series_root"):
        p = str(s.get(k) or "").rstrip("/")
        if not p:
            continue
        inside = any(p == m or p.startswith(m + "/") for m in mounts)
        out[k] = {"inside": inside, "exists": os.path.isdir(p)}
    return out


def MEDIA_ROOTS_():
    """The library paths to walk: Radarr's and Sonarr's when they are set, otherwise the one Media root."""
    s = read_settings()
    out = []
    for k in ("movies_root", "series_root"):
        p = str(s.get(k) or "").rstrip("/")
        if p and p not in out:
            out.append(p)
    return out or [m.rstrip("/") for m in mounted_media()] or [MEDIA_ROOT_()]
CATEGORY_NAMES = {"orphaned": "orphaned", "subs_folder": "subs folder", "redundant": "redundant",
                  "foraldralos": "orphaned", "subsmapp": "subs folder", "overflodig": "redundant"}   # Swedish names in old manifests
_mkv_cache = {"ts": 0, "count": None, "video": None}


def video_counts():
    """Video files under PlexMedia (except the quarantine), per container: mkv is what the flag
    script can write, everything else only gets a Plex selection. Cached for an hour - the library is large."""
    if _mkv_cache["video"] is not None and time.time() - _mkv_cache["ts"] < 3600:
        return _mkv_cache["video"]
    n = {"mkv": 0, "mp4": 0, "m4v": 0, "other": 0}
    other = ("avi", "ts", "m2ts", "wmv", "mov", "webm", "flv", "mpg", "mpeg", "divx", "iso")   # what Plex plays besides the three
    for base in MEDIA_ROOTS_():
        try:
            for root, dirs, files in os.walk(base):
                if root.startswith(QUARANTINE_()):
                    dirs[:] = []
                    continue
                for f in files:
                    ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
                    if ext in n:
                        n[ext] += 1
                    elif ext in other:
                        n["other"] += 1
        except OSError:
            pass
    _mkv_cache.update({"ts": time.time(), "count": n["mkv"], "video": n})
    return n


def mkv_count():
    """Number of .mkv under PlexMedia (except the quarantine)."""
    return video_counts()["mkv"]


BACKUP_DIR = os.path.join(os.path.dirname(SETTINGS), "backup")
ENGLISH_ONLY = {"native_langs": "", "same_sub_langs": "eng", "sub_langs": "eng",
            "audio_priority": "eng", "unwanted_langs": ""}


def make_backup():
    """Writes a timestamped copy of settings.json into config/backup. Returns the file name."""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(SETTINGS) as f:
            data = f.read()
        name = "settings-%s-v%s.json" % (datetime.now().strftime("%Y%m%d-%H%M%S"), VERSION)
        with open(os.path.join(BACKUP_DIR, name), "w") as f:
            f.write(data)
        return name
    except OSError:
        return ""


def backup_list(n=5):
    try:
        return sorted((f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")), reverse=True)[:n]
    except OSError:
        return []


def reset_settings():
    """Defaults + English only as audio and text. Connections and sign-in are kept."""
    old = {}
    try:
        with open(SETTINGS) as f:
            old = json.load(f)
    except (OSError, ValueError):
        pass
    ny = dict(DEFAULTS)
    ny.update(ENGLISH_ONLY)
    for k in ("connections", "password", "secret"):
        if k in old:
            ny[k] = old[k]
    with open(SETTINGS, "w") as f:
        json.dump(ny, f, indent=2, ensure_ascii=False)


def reset_everything(log=None):
    """Back to a clean slate: the preferences, the connections and the sign-in all go.
    A backup is written first, so the old settings are never lost for good.
    A fresh random password for admin ends up in config/NEW_PASSWORD.txt."""
    make_backup()
    ny = dict(DEFAULTS)
    ny.update(ENGLISH_ONLY)
    with open(SETTINGS, "w") as f:
        json.dump(ny, f, indent=2, ensure_ascii=False)
    reset_credentials(log, "Reset everything")
    clear_logs("all")
    return True



def delete_backup(name):
    """Deletes a single backup file in config/backup."""
    if not name or "/" in name or name in (".", "..") or not name.endswith(".json"):
        return False
    try:
        os.remove(os.path.join(BACKUP_DIR, name))
        return True
    except OSError:
        return False


def restore_backup(name):
    """Writes a backup file back over settings.json. Takes a backup of the current one first."""
    if not name or "/" in name or name in (".", "..") or not name.endswith(".json"):
        return False
    source = os.path.join(BACKUP_DIR, name)
    if not os.path.isfile(source):
        return False
    try:
        with open(source) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    make_backup()
    try:
        with open(SETTINGS, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        return False
    return True

def clear_quarantine(folder):
    """Deletes everything in a quarantine folder permanently (only under QUARANTINE). Returns the number of files."""
    if not folder or "/" in folder or folder in (".", ".."):
        return 0
    import shutil
    root = os.path.join(QUARANTINE_(), folder)
    if not os.path.isdir(root):
        return 0
    n = 0
    for r, dirs, files in os.walk(root):
        n += len(files)
    shutil.rmtree(root)
    os.makedirs(root, exist_ok=True)
    _mkv_cache["count"] = None
    return n


def _quarantine_manifest_row(cols):
    """Both cleanup scripts write manifest.tsv, but in different shapes: trash writes
    category\tpath\tdest, langs writes path\tdest (no category). Column 0 has a slash
    only in the second form. Returns (original_path, dest_or_DELETED) or None."""
    if len(cols) >= 2 and "/" in cols[0]:
        return cols[0], cols[1]
    if len(cols) >= 3:
        return cols[1], cols[2]
    return None


def restore_quarantine(folder):
    """Moves every file in a quarantine folder back to where it came from, using the
    manifest. Rows already permanently deleted (dest == DELETED) have nothing to move
    and are left alone. A restored or now-missing row is dropped from the manifest;
    a row whose original path is occupied again is kept, so nothing is silently lost.
    Returns (restored, missing, conflicts)."""
    if not folder or "/" in folder or folder in (".", ".."):
        return 0, 0, 0
    import shutil
    root = os.path.join(QUARANTINE_(), folder)
    manifest_path = os.path.join(root, "manifest.tsv")
    if not os.path.isfile(manifest_path):
        return 0, 0, 0
    with open(manifest_path, encoding="utf-8", errors="replace") as f:
        rows = [r.rstrip("\n") for r in f if r.strip()]
    keep = []
    restored = missing = conflicts = 0
    for row in rows:
        parsed = _quarantine_manifest_row(row.split("\t"))
        if not parsed:
            keep.append(row)
            continue
        orig, dest = parsed
        if dest == "DELETED":
            keep.append(row)                # nothing on disk, permanently gone already
            continue
        if not os.path.isfile(dest):
            missing += 1                    # not in quarantine any more (e.g. removed outside the UI) - drop the stale row
            continue
        if os.path.exists(orig):
            conflicts += 1
            keep.append(row)                # something is already there - leave it for a human to sort out
            continue
        try:
            os.makedirs(os.path.dirname(orig), exist_ok=True)
            shutil.move(dest, orig)
            restored += 1
        except OSError:
            conflicts += 1
            keep.append(row)
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + ("\n" if keep else ""))
    _mkv_cache["count"] = None
    return restored, missing, conflicts


def quarantine_files(folder):
    """Lists what is actually sitting in a quarantine folder right now, read from its
    manifest. Each entry is the original path, its size in bytes, and its category (the
    langs job does not tag one). Rows already permanently deleted are skipped, as are
    rows whose file has gone missing on disk."""
    if not folder or "/" in folder or folder in (".", ".."):
        return []
    root = os.path.join(QUARANTINE_(), folder)
    manifest_path = os.path.join(root, "manifest.tsv")
    if not os.path.isfile(manifest_path):
        return []
    out = []
    with open(manifest_path, encoding="utf-8", errors="replace") as f:
        for row in f:
            if "\t" not in row or row.startswith("#"):
                continue
            cols = row.rstrip("\n").split("\t")
            parsed = _quarantine_manifest_row(cols)
            if not parsed or parsed[1] == "DELETED":
                continue
            orig, dest = parsed
            try:
                size = os.path.getsize(dest)
            except OSError:
                continue
            cat = cols[0].strip() if len(cols) >= 3 and "/" not in cols[0] else ""
            out.append({"path": orig, "bytes": size, "category": CATEGORY_NAMES.get(cat, cat) if cat else ""})
    out.sort(key=lambda r: r["path"])
    return out
_OK = re.compile(r"^OK \(([^)]+)\)")
_MOVED = re.compile(r"(Moved|Deleted|Flyttade|Raderade) (\d+) files")   # English since v1.32, Swedish before that


def _all_entries(file):
    """The whole log, oldest first (log_entries only takes the tail)."""
    try:
        with open(file, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for r in lines:
        m = _LOGLINE.match(r)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


_bib_cache = {"ts": 0, "data": None}


def library_counts():
    """Movies, series and episodes according to Radarr/Sonarr. Cached for an hour - two API calls."""
    if _bib_cache["data"] is not None and time.time() - _bib_cache["ts"] < 3600:
        return _bib_cache["data"]
    import urllib.request
    d = active_env()
    out = {"movies": None, "series": None, "episodes": None}

    def _fetch(url, key_name, path):
        url = (url or "").strip().rstrip("/")
        if not url or not (key_name or "").strip():
            return None
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        try:
            req = urllib.request.Request(url + path, headers={"X-Api-Key": key_name.strip(),
                                                             "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            return None

    mv = _fetch(d.get("RADARR_URL"), d.get("RADARR_APIKEY"), "/api/v3/movie")
    if isinstance(mv, list):
        out["movies"] = sum(1 for m in mv if m.get("hasFile"))
    sr = _fetch(d.get("SONARR_URL"), d.get("SONARR_APIKEY"), "/api/v3/series")
    if isinstance(sr, list):
        out["series"] = len(sr)
        out["episodes"] = sum(int((x.get("statistics") or {}).get("episodeFileCount") or 0) for x in sr)
    _bib_cache.update({"ts": time.time(), "data": out})
    return out



_tags_cache = {"ts": 0.0, "data": None}


def arr_tags(sources=("radarr", "sonarr")):
    """Tag labels straight from Radarr and Sonarr, so the field can offer them. Both are read
    and cached for 10 min; `sources` picks which of them the list is built from."""
    if _tags_cache["data"] is None or time.time() - _tags_cache["ts"] >= 600:
        import urllib.request
        d = active_env()
        per = {}
        for src, base, key in (("radarr", d.get("RADARR_URL"), d.get("RADARR_APIKEY")),
                               ("sonarr", d.get("SONARR_URL"), d.get("SONARR_APIKEY"))):
            per[src] = []
            base = (base or "").strip().rstrip("/")
            if not base or not (key or "").strip():
                continue
            if not base.startswith(("http://", "https://")):
                base = "http://" + base
            try:
                req = urllib.request.Request(base + "/api/v3/tag", headers={"X-Api-Key": key.strip(), "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    for t in json.loads(r.read().decode("utf-8")):
                        lbl = str(t.get("label") or "").strip()
                        if lbl and lbl not in per[src]:
                            per[src].append(lbl)
            except Exception:
                pass
        _tags_cache.update({"ts": time.time(), "data": per})
    out = []
    for src in sources:
        for lbl in _tags_cache["data"].get(src) or []:
            if lbl not in out:
                out.append(lbl)
    out.sort(key=str.lower)
    return out

_tagmap_cache = {"ts": 0.0, "data": None}


def arr_tag_map(sources=("radarr", "sonarr")):
    """Folder name -> the tags Radarr and/or Sonarr put on that title. Cached for 10 min.

    Keyed on the last part of the arr's own path, never the whole path, so it still
    matches when the container sees the library under a different mount point.
    Both arrs are read and cached; `sources` picks which of them the map is built from."""
    if _tagmap_cache["data"] is None or time.time() - _tagmap_cache["ts"] >= 600:
        import urllib.request
        d = active_env()
        per = {}
        for src, base, key, path in (("radarr", d.get("RADARR_URL"), d.get("RADARR_APIKEY"), "/api/v3/movie"),
                                     ("sonarr", d.get("SONARR_URL"), d.get("SONARR_APIKEY"), "/api/v3/series")):
            per[src] = {}
            base = (base or "").strip().rstrip("/")
            if not base or not (key or "").strip():
                continue
            if not base.startswith(("http://", "https://")):
                base = "http://" + base
            head = {"X-Api-Key": key.strip(), "Accept": "application/json"}
            try:
                labels = {}
                req = urllib.request.Request(base + "/api/v3/tag", headers=head)
                with urllib.request.urlopen(req, timeout=15) as r:
                    for tg in json.loads(r.read().decode("utf-8")):
                        labels[tg.get("id")] = str(tg.get("label") or "").strip()
                req = urllib.request.Request(base + path, headers=head)
                with urllib.request.urlopen(req, timeout=90) as r:
                    for it in json.loads(r.read().decode("utf-8")):
                        folder = os.path.basename(str(it.get("path") or "").rstrip("/"))
                        names = [labels[x] for x in (it.get("tags") or []) if labels.get(x)]
                        if folder and names:
                            per[src][folder] = names
            except Exception:
                pass
        _tagmap_cache.update({"ts": time.time(), "data": per})
    out = {}
    for src in sources:
        for folder, names in (_tagmap_cache["data"].get(src) or {}).items():
            out[folder] = out.get(folder, []) + [n for n in names if n not in out.get(folder, [])]
    return out


def write_tag_map():
    """The map as a file, since the scripts run as separate processes. Built only from the
    arrs ticked under Tag list -> Use from."""
    s = read_settings()
    sources = [x for x in ("radarr", "sonarr") if s.get("alt_tag_" + x, True)]
    p = os.path.join(CONFIG_DIR, "tag_map.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(arr_tag_map(sources), f, ensure_ascii=False)
    except OSError:
        return ""
    return p


def statistics(listener_log):
    from datetime import timedelta
    now = datetime.now()
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
    per_day = {d: {"ok": 0, "error": 0, "plex": 0} for d in days}
    by_lang, total, error, plex, scan, starts, test, sel = {}, 0, 0, 0, 0, 0, 0, 0
    first = None
    for ts, txt in _all_entries(listener_log):
        d = ts[:10]
        first = first or ts
        m = _OK.match(txt)
        kind = _classify(txt)
        if m:
            total += 1
            by_lang[m.group(1)] = by_lang.get(m.group(1), 0) + 1
            if d in per_day:
                per_day[d]["ok"] += 1
        elif kind == "error" and _IMPORT_ERR(txt):       # the chart and the counts mean imports that failed
            error += 1
            if d in per_day:
                per_day[d]["error"] += 1
        elif txt.startswith("plex-pick: done"):
            plex += 1
            ms = re.search(r"(\d+) selections set", txt)     # logged since v0.86.10.4; older lines count as a pass only
            if ms and d in per_day:
                sel += int(ms.group(1))
            if d in per_day:
                per_day[d]["plex"] += 1
        elif txt.startswith("plex-scan:"):
            scan += 1
        elif txt.startswith("listening on port"):
            starts += 1
    seven = days[-7:]
    # the quarantine: one row per file actually moved there, read from manifest.tsv
    # (a DELETED row means quarantine was off for that run - nothing sits on disk for it)
    quarantine = []
    try:
        for name in sorted(os.listdir(QUARANTINE_())):
            mp = os.path.join(QUARANTINE_(), name, "manifest.tsv")
            if not os.path.isfile(mp):
                continue
            category, n, size = {}, 0, 0
            with open(mp, encoding="utf-8", errors="replace") as f:
                for row in f:
                    if "\t" not in row or row.startswith("#"):
                        continue
                    cols = row.rstrip("\n").split("\t")
                    parsed = _quarantine_manifest_row(cols)
                    if not parsed or parsed[1] == "DELETED":
                        continue
                    try:
                        size += os.path.getsize(parsed[1])
                    except OSError:
                        continue           # manifest row survives a manual delete outside the UI
                    n += 1
                    k = cols[0].strip()
                    if k and "/" not in k and len(k) <= 30:      # the 2-column form has no category
                        k = CATEGORY_NAMES.get(k, k)
                        category[k] = category.get(k, 0) + 1
            quarantine.append({"folder": name, "files": n, "bytes": size,
                             "categories": sorted(category.items(), key=lambda x: -x[1])})
    except OSError:
        pass
    # the job logs: runs and moved/deleted files
    jobs = []
    for name, (title, _, _, logfile) in JOBS.items():
        entries = _all_entries(logfile)
        moves = row = runs = 0
        last_run = None
        try:
            with open(logfile, encoding="utf-8", errors="replace") as f:
                for r in f:
                    m = _MOVED.search(r)
                    if m:
                        if m.group(1) in ("Moved", "Flyttade"):
                            moves += int(m.group(2))
                        else:
                            row += int(m.group(2))
                    m2 = _LOGLINE.match(r)
                    if m2 and "mode:" in r:          # the run_ wrappers' header line: "<ts>  mode: dry|apply"
                        runs += 1
                        last_run = m2.group(1) + " (" + r.split("mode:", 1)[1].strip() + ")"
        except OSError:
            pass
        if entries and not last_run:                  # ui_*.log: one row per start from the web page
            runs = runs or len([1 for t, x in entries if x.lower().startswith("ui: starting")])
            last_run = entries[-1][0]
        jobs.append({"id": name, "title": title, "runs_count": runs, "last_run": last_run,
                     "moved": moves, "deleted": row})
    return {
        "since": first[:10] if first else None,
        "mkv_total": mkv_count(),
        "video": video_counts(),
        "library_counts": library_counts(),
        "flagged": {"total": total, "seven": sum(per_day[d]["ok"] for d in seven),
                     "thirty": sum(v["ok"] for v in per_day.values())},
        "error": {"total": error, "thirty": sum(v["error"] for v in per_day.values())},
        "plex": {"picks": plex, "scan": scan, "selections": sel},
        "listener": {"starts": starts, "test": test},
        "by_language": sorted(by_lang.items(), key=lambda x: -x[1]),
        "days": [{"day": d, **per_day[d]} for d in days],
        "quarantine": quarantine,
        "dry": dry_summary(),
        "weekly": weekly_summary(),
        "started": {j["id"]: _started_by(listener_log, (j["last_run"] or "")[:16]) for j in jobs},
        "recent": recent_imports(listener_log),
        "jobs": jobs,
    }


_DRY_TOTAL = re.compile(r"Original language known for (\d+) files \((\d+) movies?, (\d+) episodes?\)")
_DRY_UNMATCHED = re.compile(r"^(\d+) files in Plex have no match")
_DRY_CHANGE = re.compile(r"(?:(\d+) titles? (?:would change|changed)|Changed (\d+) titles?)")
_DRY_LINE = re.compile(r"^  (\S.*?)\s\s+\[(\w+)\] (.+)$")
_DRY_CODEC = re.compile(r"audio \S+?/(\S+) -> \S+?/(\S+)")
_EPISODE = re.compile(r"S\d{1,2}E\d{1,2}", re.I)


def dry_summary():
    """What the last run of the Plex job found, read back from its own log. Reading the
    log costs nothing next to scanning the library again - the price is that the numbers
    are as old as that run, which is why Statistics prints when they were measured."""
    out = {"when": None, "mode": None, "total": 0, "movies": 0, "episodes": 0,
           "unmatched": 0, "change": 0, "audio": 0, "subs": 0, "both": 0,
           "movie_change": 0, "episode_change": 0, "codecs": [], "sections": []}
    lines = _last_run(JOBS["plex"][3], max_lines=0, tail=100000)   # the whole run: a library-sized log is thousands of lines
    if not lines:
        return out
    for _i, _l in enumerate(lines):          # per-user passes sit below the owner’s - not in these numbers
        if _l.startswith("--- selections for "):
            lines = lines[:_i]
            break
    head = _LOGLINE.match(lines[0] or "")
    if head:
        out["when"] = head.group(1)[:16]
        if "mode:" in lines[0]:
            out["mode"] = lines[0].split("mode:", 1)[1].strip()
    codecs = {}
    sections = {}
    section = None
    for r in lines:
        line = r.rstrip()
        m = _DRY_TOTAL.search(line)
        if m:
            out["total"], out["movies"], out["episodes"] = (int(x) for x in m.groups())
            continue
        m = _DRY_UNMATCHED.match(line.strip())
        if m:
            out["unmatched"] = int(m.group(1))
            continue
        m = _DRY_CHANGE.search(line)
        if m:
            out["change"] = int(m.group(1) or m.group(2))
            continue
        m = _DRY_LINE.match(line)
        if m:
            title, rest = m.group(1), m.group(3)
            has_audio = "audio " in rest
            has_subs = "text " in rest
            if has_audio and has_subs:
                out["both"] += 1
            elif has_audio:
                out["audio"] += 1
            elif has_subs:
                out["subs"] += 1
            if _EPISODE.search(title):
                out["episode_change"] += 1
            else:
                out["movie_change"] += 1
            c = _DRY_CODEC.search(rest)
            if c:
                codecs[c.group(1)] = codecs.get(c.group(1), 0) + 1
            if section:
                sections[section] = sections.get(section, 0) + 1
            continue
        bare = line.strip()
        if bare and not line.startswith(" ") and not line.startswith("...") and not line.startswith("…") \
                and not _LOGLINE.match(line) and "====" not in line:      # "... N files checked so far" is progress, not a library
            section = bare
    out["codecs"] = sorted(codecs.items(), key=lambda x: -x[1])
    out["sections"] = sorted(sections.items(), key=lambda x: -x[1])
    out["settled"] = max(0, out["total"] - out["change"])
    return out


_WEEKLY_DONE = re.compile(r"Done: (\d+) files handled, (\d+) errors?")


def _started_by(listener_log, when):
    """"scheduled" when the listener's own scheduler kicked off a run at about that minute,
    "manual" when it was started from the Run page - "" when the time is unknown."""
    if not when:
        return ""
    try:
        t0 = datetime.strptime(when[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    for ts, txt in _all_entries(listener_log):
        if txt.startswith("weekly check scheduled run"):
            try:
                d = (t0 - datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")).total_seconds()
            except ValueError:
                continue
            if -60 <= d <= 3600:      # the chain runs cleanup first, so the flag run can start a while after the trigger
                return "scheduled"
    return "manual"


def weekly_summary():
    """The last whole-library flag run, read back from its log: when, how many files, how many errors."""
    out = {"when": None, "mode": None, "handled": 0, "errors": 0}
    lines = _last_run(JOBS["weekly"][3], max_lines=0, tail=20000)
    if not lines:
        return out
    head = _LOGLINE.match(lines[0] or "")
    if head:
        out["when"] = head.group(1)[:16]
        if "mode:" in lines[0]:
            out["mode"] = lines[0].split("mode:", 1)[1].strip()
    for r in lines:
        m = _WEEKLY_DONE.search(r)
        if m:
            out["handled"], out["errors"] = int(m.group(1)), int(m.group(2))
    return out


_IMPORT_OK = re.compile(r"^OK \(([^)]+)\) (\S.*?\.\w{2,4})(?: :: (.*))?$")
_IMPORT_ERR = lambda txt: _classify(txt) == "error" and not txt.lower().startswith("ui:") and ("/" in txt or "mkv" in txt.lower())   # an import that failed, not the UI talking about a job


def recent_imports(listener_log, days=30):
    """Every import the listener handled in the last `days` days, newest first: what the
    Statistics list and its numbers are built from. An OK line carries the language and the
    file; an error line about a file carries the reason."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    out = []
    for ts, txt in reversed(_all_entries(listener_log)):
        if ts[:10] < cutoff:
            break
        m = _IMPORT_OK.match(txt)
        if m:
            lang, path, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
            mt = re.match(r"\[defaultflags\] OK (.*)$", rest)
            name = (mt.group(1) if mt else os.path.basename(path)).strip()
            name = re.sub(r"\s*\[.*$", "", name).rsplit(".mkv", 1)[0].strip() or os.path.basename(path)
            out.append({"ts": ts, "ok": bool(mt), "skip": not mt, "lang": lang, "title": name, "file": path, "note": "" if mt else rest,   # skip: the script ran but wrote nothing (no audio tracks, say)
                        "kind": "episode" if _EPISODE.search(name) or "/Serier/" in path else "movie"})
            continue
        if _IMPORT_ERR(txt):
            pm = re.search(r"(/\S[^:]*?\.(?:mkv|mp4|m4v))", txt)
            path = pm.group(1) if pm else ""
            name = os.path.basename(path) if path else txt[:90]
            name = re.sub(r"\s*\[.*$", "", name).rsplit(".mkv", 1)[0].strip() or name
            out.append({"ts": ts, "ok": False, "lang": "", "title": name, "file": path, "note": txt[:200],
                        "kind": "episode" if _EPISODE.search(name) or "/Serier/" in path else "movie"})
    return out


def rotate_log(file, max_lines):
    """Trims the log to max_lines once it has grown to 1.5x. Cheap check on file size first."""
    try:
        if max_lines < 20 or os.path.getsize(file) < max_lines * 60:
            return
        with open(file, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        if len(lines) > max_lines * 1.5:
            with open(file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines[-max_lines:]) + "\n")
    except OSError:
        pass


def rotate_job_log(file, max_mb):
    """A job log is capped by size, not by line count - a line here runs from 40 to 150 bytes,
    so megabytes are the only number that says what it costs on disk. Once the file has grown
    past 1.5x the cap the oldest part goes, cut at a run header so no run is left half-shown.
    0 means no cap."""
    cap = int(float(max_mb or 0) * 1024 * 1024)
    if cap <= 0:
        return
    try:
        if os.path.getsize(file) <= cap * 1.5:
            return
        with open(file, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    keep, size = [], 0
    for r in reversed(lines):                 # newest first, until the cap is full
        size += len(r.encode("utf-8")) + 1
        if size > cap:
            break
        keep.append(r)
    keep.reverse()
    for i, r in enumerate(keep):              # drop the run that the cut went through
        if "mode:" in r and _LOGLINE.match(r):
            keep = keep[i:]
            break
    try:
        with open(file, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + "\n" if keep else "")
    except OSError:
        pass

def _all_log_files():
    """Every log file flaggarr writes: LOG_FILES + the jobs' own."""
    out = [p for _, p, _ in LOG_FILES.values()]
    for _, _, _, p in JOBS.values():
        if p not in out:
            out.append(p)
    return out


def log_size():
    n = 0
    for p in _all_log_files():
        try:
            n += os.path.getsize(p)
        except OSError:
            pass
    return n


def _keep_category(path, keep):
    """Rewrites the listener log keeping only one tab's entries. A continuation line (traceback, block)
    belongs to the entry above it. True when the file was rewritten."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return False
    out, take = [], True
    for r in lines:
        m = _LOGLINE.match(r)
        if m:
            take = _category(m.group(2)) == keep
        if take:
            out.append(r)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        return True
    except OSError:
        return False


def clear_logs(what):
    """Empties log files. "action" or "system" clears one tab of the Log page - that tab's own files are
    emptied and the listener log keeps the other tab's lines. "listener" = the listener log + crashes,
    anything else = every log file. Returns the number of files touched."""
    n = 0
    if what in ("action", "system"):
        files = [p for _, p, c in LOG_FILES.values() if c == what]
        if _keep_category(LOG_FILES["listener"][1], "system" if what == "action" else "action"):
            n += 1
    elif what == "listener":
        files = [LOG_FILES["listener"][1], LOG_FILES["error"][1]]
    else:
        files = _all_log_files()
    for p in files:
        try:
            if os.path.isfile(p):
                with open(p, "w", encoding="utf-8"):
                    pass
                n += 1
        except OSError:
            pass
    return n


_bazarr_cache = {"ts": 0, "data": None}
IMAGE_SUB_KEYS = ("ignore_pgs_subs", "ignore_vobsub_subs", "ignore_ass_subs")


def browse_dirs(path):
    """The subfolders of `path`, so the page can offer a folder picker. Reads nothing else."""
    p = os.path.abspath(str(path or "/") or "/")
    out = {"path": p, "up": os.path.dirname(p.rstrip("/")) or "/", "dirs": []}
    try:
        for name in sorted(os.listdir(p), key=str.lower):
            if not name.startswith(".") and os.path.isdir(os.path.join(p, name)):
                out["dirs"].append(name)
    except OSError as exc:
        out["err"] = str(exc)
    return out


def arr_root_folders():
    """The library paths Radarr and Sonarr report, so the two fields can be filled in with a click."""
    import urllib.request
    out = {"radarr": [], "sonarr": []}
    d = active_env()
    for src, base, key in (("radarr", d.get("RADARR_URL"), d.get("RADARR_APIKEY")),
                           ("sonarr", d.get("SONARR_URL"), d.get("SONARR_APIKEY"))):
        base = (base or "").strip().rstrip("/")
        key = (key or "").strip()
        if not base or not key:
            continue
        if not base.startswith(("http://", "https://")):
            base = "http://" + base
        try:
            req = urllib.request.Request(base + "/api/v3/rootfolder",
                                         headers={"X-Api-Key": key, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                for row in json.loads(r.read().decode("utf-8", "replace")) or []:
                    p = str(row.get("path") or "").rstrip("/")
                    if p and p not in out[src]:
                        out[src].append(p)
        except Exception:
            pass
    return out


def bazarr_image_subs(fresh=False):
    """Which image formats Bazarr is set to ignore. Cached 1 h (`fresh` asks Bazarr now), None if Bazarr does not answer."""
    import urllib.request
    now = time.time()
    if not fresh and now - _bazarr_cache["ts"] < 3600:
        return _bazarr_cache["data"]
    d = active_env()
    url = (d.get("BAZARR_URL") or "").strip().rstrip("/")
    key_name = (d.get("BAZARR_APIKEY") or "").strip()
    out = None
    if url and key_name:
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        found = {}

        def leta(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in IMAGE_SUB_KEYS:
                        found[k] = bool(v)
                    else:
                        leta(v)
            elif isinstance(o, list):
                for v in o:
                    leta(v)

        try:
            req = urllib.request.Request(url + "/api/system/settings", headers={"X-API-KEY": key_name, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                leta(json.loads(r.read().decode("utf-8", "replace")))
            out = found or None
        except Exception:
            out = None
    _bazarr_cache.update({"ts": now, "data": out})
    return out


# ---------- per-user profiles ----------

_users_cache = {"t": 0.0, "v": None}

USER_KEYS = ("audio_priority", "audio_map", "sub_rules", "sub_langs", "sub_track_order", "sub_forced_mode", "audio_codec_rules")   # audio_map: the Custom rows


def profile_view(p, base):
    """The keys a profile shows, with yours filled in where it says nothing."""
    out = dict(p)
    return {k: str(out.get(k, base[k])) for k in USER_KEYS}


def plex_users(fresh=False):
    """Accounts your server is shared with, with their per-server access tokens, read from
    plex.tv with the owner’s token - nobody’s password is involved. Cached for an hour."""
    import time
    import urllib.request
    import xml.etree.ElementTree as ET
    if not fresh and _users_cache["v"] is not None and time.time() - _users_cache["t"] < 3600:
        return _users_cache["v"]
    env = active_env()
    url = (env.get("PLEX_URL") or "").strip().rstrip("/")
    token = (env.get("PLEX_TOKEN") or "").strip()
    if not url or not token:
        return []
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url + "/identity", headers={"X-Plex-Token": token}), timeout=8) as r:
            machine = ET.fromstring(r.read()).get("machineIdentifier") or ""
        with urllib.request.urlopen(urllib.request.Request(
                "https://plex.tv/api/servers/%s/shared_servers" % machine,
                headers={"X-Plex-Token": token}), timeout=10) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        _users_cache["err"] = str(e)
        return _users_cache["v"] or []
    users = [{"id": str(s.get("userID") or ""), "name": s.get("username") or s.get("email") or s.get("name") or ("user %s" % (s.get("userID") or "?")),
              "token": s.get("accessToken") or ""} for s in root.iter("SharedServer")]
    users = [x for x in users if x["id"] and x["token"]]
    _users_cache.update({"t": time.time(), "v": users})
    _users_cache["err"] = ""
    return users


def user_profiles():
    d = _read_raw().get("user_profiles")
    return d if isinstance(d, dict) else {}


def save_user_profile(uid, data, own=False):
    d = user_profiles()
    row = d.get(uid) if isinstance(d.get(uid), dict) else {}
    row["enabled"] = bool(data.get("enabled"))
    if isinstance(data.get("name"), str):
        row["name"] = data["name"]
    for k in USER_KEYS:
        if k in data:
            row[k] = str(data[k])
    if data.get("reset"):   # back to yours: a profile with no values of its own follows yours live
        for k in USER_KEYS:
            row.pop(k, None)
    # Who touched it last, and when: "own" the account itself from its own page, "adm" you from
    # the Users page. The Users page reads these two to say edited / overridden.
    row["own" if own else "adm"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if data.get("reset") and not own:
        row["rst"] = row["adm"]          # the badge says reset by admin, not overridden
    else:
        row.pop("rst", None)             # any later save, by you or the account, replaces it
    d[uid] = row
    save_settings({"user_profiles": d})


# ---------- undo: what the real runs changed, so it can be put back ----------

UNDO_DIR = CONFIG_DIR + "/undo"
UNDO_KEEP = 12       # journals of this many runs are kept, the oldest are dropped
UNDO_JOB_LABEL = {"weekly": "Set MKV flags", "plex": "Set Plex selections",
                  "import-mkv": "MKV flags on import", "import-plex": "Plex selections on import"}
_undo = {"running": False, "run": "", "done": 0, "total": 0, "failed": 0, "finished": None}


def undo_file(rid, job, uid=""):
    """Journal path for one real run. The scripts append what each change looked like before they made it,
    so the run can be put back: mkv flags as mkvpropedit arguments (tsv), Plex as stream ids (jsonl)."""
    ext = "tsv" if job in ("weekly", "import-mkv") else "jsonl"
    return os.path.join(UNDO_DIR, "%s_%s%s.%s" % (rid, job, ("_u%s" % uid) if uid else "", ext))


def new_run_id():
    try:
        os.makedirs(UNDO_DIR, exist_ok=True)
    except OSError:
        pass
    _prune_undo()
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _undo_files():
    try:
        return sorted(f for f in os.listdir(UNDO_DIR) if re.match(r"^\d{8}-\d{6}_", f))
    except OSError:
        return []


def _prune_undo():
    runs = sorted({f.split("_", 1)[0] for f in _undo_files()})
    for rid in (runs[:-UNDO_KEEP] if len(runs) > UNDO_KEEP else []):
        for f in _undo_files():
            if f.startswith(rid + "_"):
                try:
                    os.remove(os.path.join(UNDO_DIR, f))
                except OSError:
                    pass


def undo_list():
    """One entry per real run that changed something, newest first. Empty journals are not listed."""
    runs = {}
    for f in _undo_files():
        m = re.match(r"^(\d{8}-\d{6})_([a-z-]+)(?:_u(\w+))?\.(tsv|jsonl)$", f)
        if not m:
            continue
        try:
            with open(os.path.join(UNDO_DIR, f), encoding="utf-8", errors="replace") as fh:
                n = sum(1 for r in fh if r.strip())
        except OSError:
            n = 0
        if not n:
            continue
        rid, job = m.group(1), m.group(2)
        e = runs.setdefault(rid, {"run": rid, "jobs": {},
                                  "time": "%s-%s-%s %s:%s" % (rid[0:4], rid[4:6], rid[6:8], rid[9:11], rid[11:13])})
        j = e["jobs"].setdefault(job, {"job": job, "label": UNDO_JOB_LABEL.get(job, job), "changes": 0, "accounts": 0})
        j["changes"] += n
        j["accounts"] += 1
    out = []
    for rid in sorted(runs, reverse=True):
        e = runs[rid]
        e["jobs"] = sorted(e["jobs"].values(), key=lambda x: x["job"])
        out.append(e)
    return out


def undo_status():
    with _lock:
        return dict(_undo)


def _state_fingerprint():
    """Everything the page shows that changes on its own, squashed into one comparable value:
    the jobs, the undo, and the size of the logs (an import shows up there)."""
    with _lock:
        jobs = tuple((n, v.get("running"), v.get("queued"), v.get("last_run"), v.get("done"), v.get("total")) for n, v in sorted(_status.items()))
        undo = (_undo.get("running"), _undo.get("done"), _undo.get("total"), _undo.get("finished"))
    return (jobs, undo, log_size())

def undo_run(rid, log):
    """Puts one real run back: the mkv flags with the saved mkvpropedit arguments, the Plex selections with
    the saved stream ids and the token of the account the journal belongs to. Runs in its own thread."""
    if not re.match(r"^\d{8}-\d{6}$", rid or ""):
        return False, "unknown run"
    files = [f for f in _undo_files() if f.startswith(rid + "_")]
    if not files:
        return False, "nothing to undo for that run"
    with _lock:
        if _undo["running"]:
            return False, "an undo is already running"
        _undo.update({"running": True, "run": rid, "done": 0, "total": 0, "failed": 0, "finished": None})

    def _work():
        import urllib.parse
        import urllib.request
        d = active_env()
        base = (d.get("PLEX_URL") or "").strip().rstrip("/")
        tokens = {"": (d.get("PLEX_TOKEN") or "").strip()}
        try:
            for x in plex_users():
                tokens[str(x["id"])] = x["token"]
        except Exception:
            pass
        items = []
        for f in files:
            m = re.match(r"^\d{8}-\d{6}_[a-z-]+(?:_u(\w+))?\.(tsv|jsonl)$", f)
            if not m:
                continue
            try:
                with open(os.path.join(UNDO_DIR, f), encoding="utf-8", errors="replace") as fh:
                    for r in fh:
                        if r.strip():
                            items.append((m.group(2), m.group(1) or "", r.rstrip("\n")))
            except OSError:
                pass
        with _lock:
            _undo["total"] = len(items)
        failed = 0
        for ext, uid, r in items:
            ok = False
            try:
                if ext == "tsv":
                    path, args = r.split("\t", 1)
                    ok = subprocess.run(["mkvpropedit", "--command-line-charset", "UTF-8", path] + args.split(),
                                        capture_output=True, text=True, timeout=180).returncode == 0
                else:
                    rec = json.loads(r)
                    token = tokens.get(uid, "")
                    params = {"allParts": 1, "X-Plex-Token": token}
                    if rec.get("audio"):
                        params["audioStreamID"] = rec["audio"]
                    if "sub" in rec:
                        params["subtitleStreamID"] = rec["sub"] or 0
                    if base and token and len(params) > 2:
                        url = "%s/library/parts/%s?%s" % (base, rec["part"], urllib.parse.urlencode(params))
                        req = urllib.request.Request(url, method="PUT")
                        with urllib.request.urlopen(req, timeout=60) as resp:
                            ok = 200 <= resp.status < 300
            except Exception:
                ok = False
            if not ok:
                failed += 1
            with _lock:
                _undo["done"] += 1
                _undo["failed"] = failed
        for f in files:                                   # a run can only be put back once
            try:
                os.remove(os.path.join(UNDO_DIR, f))
            except OSError:
                pass
        with _lock:
            _undo.update({"running": False, "finished": datetime.now().strftime("%Y-%m-%d %H:%M")})
        log("UI: undo of the run from %s done - %d put back, %d failed" % (rid, len(items) - failed, failed))

    threading.Thread(target=_work, daemon=True).start()
    return True, "started"


def user_runs():
    """One (name, env) per shared account - the Plex selection pass runs once more per entry,
    with that account’s token. A custom profile goes on top of yours; without one it gets yours."""
    profs = user_profiles()
    out = []
    base = read_settings()
    for x in plex_users():
        p = profs.get(x["id"])
        if not (isinstance(p, dict) and p.get("enabled")):
            p = {}                                      # no custom profile: yours, as they are
        merged = dict(base)
        merged.update(profile_view(p, base))
        apply_sub_rules(merged)
        apply_track_order(merged)
        if base.get("users_no_image_subs"):   # the page-wide switch beats every profile
            merged["sub_pgs"] = merged["sub_vobsub"] = "never"
        if not base.get("tag_all_users", True):          # the tag rule is yours alone
            merged["alt_tag"] = ""
            for k in list(merged):
                if k.startswith("alt_"):
                    merged[k] = "" if isinstance(merged[k], str) else merged[k]
        env = script_env(merged)
        env["PLEX_TOKEN"] = x["token"]
        env["FLAGGARR_USER"] = x["name"]
        env["FLAGGARR_USER_ID"] = str(x["id"])
        out.append((x["name"], env))
    return out


def clear_cache():
    """Drops the cached counts (mkv, Radarr/Sonarr) and the icons."""
    _mkv_cache.update({"ts": 0, "count": None, "video": None})
    _bib_cache.update({"ts": 0, "data": None})
    _icon_cache.clear()
    _bazarr_cache.update({"ts": 0, "data": None})
    _users_cache.update({"t": 0.0, "v": None})
    _tags_cache.update({"ts": 0, "data": None})


_FAIL = re.compile(r"Traceback \(most recent call last\)|(?:^|\s)\w*(?:Error|Exception): |^ERROR\b", re.M)
_CRASH = re.compile(r"Traceback \(most recent call last\)|^\w+(?:Error|Exception): ", re.M)   # the script itself died


def job_outcome(lines, rc):
    """ok / nothing / failed for the last run, read from what the script printed.
    The exit code alone cannot be trusted: a script that dies before it starts can
    still come back as 0 through a wrapper, which is exactly how a broken job looked
    healthy for a day."""
    text = "\n".join(lines or [])
    errs = [r for r in (lines or []) if _FAIL.search(r)]
    hard = [r for r in (lines or []) if _CRASH.search(r)]
    if rc not in (0, None) or hard:
        line = (hard + errs + [""])[0].strip()[:200]
        return "failed", line or ("exit code %s" % rc)
    if errs:
        return "errors:%d" % len(errs), errs[-1].strip()[:200]
    if not text.strip():
        return "none", ""
    low = text.lower()
    if "nothing to do" in low or re.search(r"\b0 (?:titles|files)\b", low):
        return "nothing", ""
    return "ok", ""


def status(listener_log, start_time):
    _s = read_settings()
    _max_lines = int(_s.get("log_max_lines") or 20000)
    _max_mb = _s.get("job_log_max_mb", 2)
    _job_logs = {p for _, _, _, p in JOBS.values()}
    for _logfile in set(_all_log_files() + [listener_log]):
        if _logfile in _job_logs:                 # capped by size
            rotate_job_log(_logfile, _max_mb)
        else:                                     # the listener and error logs, by lines
            rotate_log(_logfile, _max_lines)
    with _lock:
        jobs = {n: dict(v) for n, v in _status.items()}
    for n, (title, desc, modes, logfile) in JOBS.items():
        jobs[n]["title"] = title
        jobs[n]["desc"] = desc
        jobs[n]["modes"] = list(modes)
        jobs[n]["log"] = _last_run(logfile)
        if jobs[n]["stopped"]:
            jobs[n]["outcome"], jobs[n]["error_line"] = "stopped", ""
        else:
            jobs[n]["outcome"], jobs[n]["error_line"] = job_outcome(jobs[n]["log"], jobs[n]["rc"])
        if not jobs[n]["last_run"]:                      # after a restart: the most recent header line in the log
            for r in reversed(_tail(logfile, 5000)):
                m = _LOGLINE.match(r)
                if m and "mode:" in r:
                    jobs[n]["last_run"] = m.group(1)[:16]
                    jobs[n]["mode"] = r.split("mode:", 1)[1].strip()
                    jobs[n]["rc"] = 0 if jobs[n]["rc"] is None else jobs[n]["rc"]
                    break
    entries = log_entries(listener_log, 400, "action")
    today = datetime.now().strftime("%Y-%m-%d")
    from datetime import timedelta
    day_window = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    all_items = log_entries(listener_log, 400)
    open_count, last_error = 0, None            # unresolved listener errors: errors newer than the most recent successful action
    for p in all_items:                               # newest first
        if p["kind"] == "ok":
            break
        if p["kind"] == "error":
            open_count += 1
            if last_error is None:
                last_error = p["text"].split("\n")[0][:120]
    stat = {"imports_today": sum(1 for p in entries if p["ts"].startswith(today) and p["text"].startswith("OK (")),
            "errors_today": sum(1 for p in entries if p["ts"].startswith(today) and p["kind"] == "error"),
            "errors_24h": sum(1 for p in all_items if p["ts"] >= day_window and p["kind"] == "error"),
            "open_issues": open_count,
            "last_error": last_error,
            "last_import": next((p["ts"] for p in entries if p["text"].startswith("OK (")), None)}
    return {
        "settings": read_settings(),
        "defaults": dict(DEFAULTS, quarantine=MEDIA_ROOT_() + "/Removed Subs"),
        "backups": backup_list(),
        "undo": undo_list(),
        "undo_status": undo_status(),
        "bazarr_image_subs": bazarr_image_subs(),
        "languages": LANGUAGES,
        "code_fields": list(CODE_FIELDS),
        "jobs": jobs,
        "events": entries[:12],
        "stat": stat,
        "up_since": start_time,
        "log_bytes": log_size(),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "media_root_ok": os.path.isdir(MEDIA_ROOT_()),
        "media_mounts": mounted_media(),
        "roots_state": roots_state(),
        "media_root_now": MEDIA_ROOT_(),
        "auth": password_set(),
        "next_weekly": _schedule_time["next"],
    }


# ---------- the pages ----------

STATIC = os.path.join(APP_DIR, "static")
_sidcache = {}


def page(name):
    """Reads a page from static/. Cached on mtime, so an edited file shows up without a restart."""
    p = os.path.join(STATIC, name)
    try:
        m = os.path.getmtime(p)
    except OSError:
        return "<!doctype html><meta charset=\"utf-8\"><title>flaggarr</title><p>missing static/%s</p>" % name
    if _sidcache.get(name, (None,))[0] != m:
        try:
            with open(p, encoding="utf-8") as f:
                _sidcache[name] = (m, f.read())
        except OSError:
            return "<!doctype html><meta charset=\"utf-8\"><title>flaggarr</title><p>could not read static/%s</p>" % name
    return _sidcache[name][1]


# ---------- http ----------

def mkv_check(path):
    """Why a file could not be written: the account's access to it, and what mkvtoolnix makes of it.
    Behind the Check file buttons on Run and Log. Only files under the library."""
    import pwd, grp, stat as _stat
    path = os.path.realpath(str(path or ""))
    root = MEDIA_ROOT_().rstrip("/")
    out = {"ok": False, "path": path}
    if not root or not path.startswith(root + "/"):
        out["msg"] = "only files under the library folder can be checked"
        return out
    if not os.path.isfile(path):
        out["msg"] = "no such file"
        return out
    st_ = os.stat(path)
    def _name(fn, i):
        try:
            return fn(i)
        except KeyError:
            return str(i)
    me = os.geteuid()
    out.update({"size": st_.st_size, "mode": oct(_stat.S_IMODE(st_.st_mode))[2:],
                "owner": "%s:%s" % (_name(lambda i: pwd.getpwuid(i).pw_name, st_.st_uid), _name(lambda i: grp.getgrgid(i).gr_name, st_.st_gid)),
                "account": _name(lambda i: pwd.getpwuid(i).pw_name, me),
                "readable": os.access(path, os.R_OK)})
    # A real open for writing, closed again at once: the mode bits say little on a Synology share, where an ACL
    # or the DOS read-only attribute can still refuse the write. Nothing is written.
    try:
        os.close(os.open(path, os.O_RDWR))
        out["writable"], out["write_error"] = True, ""
    except OSError as e:
        out["writable"], out["write_error"] = False, "%s (errno %d)" % (e.strerror, e.errno)
    def run(cmd, secs):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=secs)
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return -1, "timed out after %d s" % secs
        except OSError as e:
            return -1, str(e)
    # Short limits: the answer has to be back before DSM's reverse proxy gives up on the request.
    rc1, ident = run(["mkvmerge", "--command-line-charset", "UTF-8", "--output-charset", "UTF-8", "-i", path], 20)
    rc2, info = run(["mkvinfo", "--command-line-charset", "UTF-8", "--output-charset", "UTF-8", path], 20) if rc1 != -1 else (-1, "not run - mkvmerge already stalled")
    out.update({"identify": ident[-3000:], "identify_rc": rc1, "info": info[-4000:], "info_rc": rc2})
    who = "The account %s (owner %s, mode %s)" % (out["account"], out["owner"], out["mode"])
    if not out["readable"]:
        v = who + " cannot read the file."
    elif not out["writable"]:
        v = who + " cannot open the file for writing: " + out["write_error"] + ". " + ("The mode allows it, so the share refuses it - a Synology ACL or the file's read-only attribute. In File Station: Properties -> Permission for the account, and untick Read-only under General." if out["mode"] == "777" else "Give it write access on the NAS: chown or chmod, or the shared folder's permissions with 'Apply to this folder, sub-folders and files'.")
    elif rc1 == -1 or rc2 == -1:
        v = "Reading the file stalls - it is damaged, or the disk has trouble reading it. Get the file again, and check the drive's health in Storage Manager if it happens to more files."
    elif rc1 != 0 or rc2 != 0:
        v = "mkvtoolnix cannot read the whole file - damaged, cut short, or still being written. Get the file again."
    else:
        v = "The file reads fine and the account can write to it - another program probably held it at the time. Run the flags again."
    out.update({"ok": True, "verdict": v, "retry": out["readable"] and out["writable"] and rc1 == 0 and rc2 == 0})
    return out


def mkv_retry(path, log):
    """The flag script on one file, as a real run with its own undo journal. Behind Check file."""
    path = os.path.realpath(str(path or ""))
    root = MEDIA_ROOT_().rstrip("/")
    if not root or not path.startswith(root + "/") or not os.path.isfile(path):
        return False, "only files under the library folder"
    env = dict(os.environ)
    env.update(script_env())
    env["FLAGGARR_UNDO"] = undo_file(new_run_id(), "weekly")
    try:
        r = subprocess.run(["sh", APP_DIR + "/auto_set_mkv_defaultflags.sh", path], capture_output=True, text=True, env=env, timeout=50)
        rc, text = r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, text = -1, "timed out after 50 s"
    log("UI: MKV flags for one file (%s): %s" % (os.path.basename(path), "OK" if rc == 0 else "FAILED"))
    return rc == 0, text


def _json(h, obj, cookie=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(200)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    if cookie:
        h.send_header("Set-Cookie", cookie)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def handle_get(h, listener_log, start_time):
    """Returns True if the call was a UI call and has been answered."""
    from urllib.parse import urlparse, parse_qs
    u = urlparse(h.path)
    check_reset()
    note_direct_host(h)
    if not from_lan(h) and not password_set():
        body = page("blocked.html").encode("utf-8")
        h.send_response(403)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
        return True
    if not password_set():
        # First run, and the only thing the page offers is making an account.
        # flaggarr writes into media files and moves subtitles aside, so there
        # is no mode where it answers for someone who has not signed in.
        body = page("setup.html").encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        h.wfile.write(body)
        return True
    if u.path == "/api/icon":
        ik = fetch_icon((parse_qs(u.query).get("t") or [""])[0])
        if not ik:
            h.send_response(404); h.end_headers()
            return True
        h.send_response(200)
        h.send_header("Content-Type", ik[0]); h.send_header("Content-Length", str(len(ik[1])))
        h.send_header("Cache-Control", "public, max-age=86400"); h.end_headers()
        h.wfile.write(ik[1])
        return True
    if u.path == "/demo/login":                       # the demo's Log out: the sign-in page, as a guest meets it
        body = page("login.html").encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Cache-Control", "no-store")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
        return True
    if not signed_in(h):
        _uid = session_user(h)
        if _uid:
            if u.path == "/api/status":
                _json(h, user_status(_uid))
                return True
            if u.path == "/api/plex_users":
                _json(h, {"users": [user_row(_uid)], "err": ""})
                return True
            if not u.path.startswith("/api/") and (u.path in ("/", "/index.html", "/ui") or via_proxy(h)):
                body = page("app.html").encode("utf-8")
                h.send_response(200)
                h.send_header("Content-Type", "text/html; charset=utf-8")
                h.send_header("Cache-Control", "no-store")
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                h.wfile.write(body)
                return True
        if u.path.startswith("/api/"):
            h.send_response(401)
            h.send_header("Content-Type", "application/json")
            h.end_headers()
            h.wfile.write(b'{"ok": false, "msg": "sign in"}')
            return True
        if u.path in ("/", "/index.html", "/ui") or via_proxy(h):
            body = page("login.html").encode("utf-8")
            h.send_response(401)
            h.send_header("Content-Type", "text/html; charset=utf-8")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
            return True
        return False
    if u.path == "/api/status":
        st_ = status(listener_log, start_time)
        st_["signed"] = COOKIE_NAME + "=" in (h.headers.get("Cookie") or "")
        _json(h, st_)
        return True
    if u.path == "/api/plex_users":
        base = read_settings()
        profs = user_profiles()
        users = []
        for x in plex_users(bool(parse_qs(u.query).get("fresh"))):
            p = profs.get(x["id"])
            if not isinstance(p, dict):
                p = {}
            users.append({"id": x["id"], "name": x["name"], "enabled": bool(p.get("enabled")), "own": str(p.get("own") or ""), "adm": str(p.get("adm") or ""), "rst": str(p.get("rst") or ""),
                          "profile": profile_view(p, base)})
        _json(h, {"users": users, "err": _users_cache.get("err", "")})
        return True
    if u.path == "/api/stats":
        _json(h, statistics(listener_log))
        return True
    if u.path == "/api/browse":
        _json(h, browse_dirs((parse_qs(u.query).get("path") or ["/"])[0]))
        return True
    if u.path == "/api/root_folders":
        _json(h, {"folders": arr_root_folders()})
        return True
    if u.path == "/api/bazarr_image_subs":
        _json(h, {"data": bazarr_image_subs(fresh=True)})
        return True
    if u.path == "/api/arr_tags":
        _json(h, {"tags": arr_tags(), "by": {s: arr_tags((s,)) for s in ("radarr", "sonarr")}})
        return True
    if u.path == "/api/mkv_check":
        _json(h, mkv_check((parse_qs(u.query).get("path") or [""])[0]))
        return True
    if u.path == "/api/quarantine_files":
        folder = (parse_qs(u.query).get("folder") or [""])[0]
        _json(h, {"files": quarantine_files(folder)})
        return True
    if u.path == "/api/connections":
        _json(h, {"fields": env_status(), "services": services_status()})
        return True
    if u.path == "/api/events":                     # a stream that only says "something changed"; the page then fetches /api/status
        import time
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("X-Accel-Buffering", "no")     # DSM's reverse proxy must pass it through unbuffered
        h.end_headers()
        last, ticks = None, 0
        try:
            while True:
                fp = _state_fingerprint()
                if fp != last:
                    if last is not None:
                        h.wfile.write(b"data: changed\n\n"); h.wfile.flush()
                    last = fp
                elif ticks % 30 == 0:
                    h.wfile.write(b": ping\n\n"); h.wfile.flush()   # keeps the proxy from closing an idle stream
                ticks += 1
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return True
    if u.path == "/api/log":
        q = parse_qs(u.query)
        file = (q.get("file") or ["listener"])[0]
        try:
            n = min(2000, max(20, int((q.get("n") or ["300"])[0])))
        except ValueError:
            n = 300
        category = (q.get("category") or [""])[0] or None
        if file == "all":
            merged = []
            for k, (t2, p2, c2) in LOG_FILES.items():
                if category and c2 and c2 != category:
                    continue
                for e in log_entries(p2, n, category if c2 is None else None):
                    e["job"] = k
                    merged.append(e)
            merged.sort(key=lambda e: e["ts"], reverse=True)
            _json(h, {"file": "all", "title": "All jobs", "entries": merged[:n]})
            return True
        title, p, _ = LOG_FILES.get(file, LOG_FILES["listener"])
        if file != "listener":
            category = None                       # the job logs are already one category
        ents = log_entries(p, n, category)
        for e in ents:
            e["job"] = file
        _json(h, {"file": file, "title": title, "entries": ents})
        return True
    if u.path in ("/", "/index.html", "/ui"):
        body = page("app.html").encode("utf-8")
        etag = '"%d-%d"' % (len(body), int(_sidcache.get("app.html", (0,))[0]))
        if h.headers.get("If-None-Match") == etag:      # unchanged since the last fetch
            h.send_response(304)
            h.send_header("ETag", etag)
            h.send_header("Cache-Control", "no-cache")
            h.end_headers()
            return True
        h.send_response(200)
        h.send_header("ETag", etag)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Cache-Control", "no-cache")          # revalidate, so a new version shows up at once
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)
        return True
    return False


def handle_post(h, body, log):
    """Returns True if the call was a UI call and has been answered."""
    p = h.path.split("?")[0]
    check_reset(log)
    note_direct_host(h)
    if not from_lan(h) and not password_set():
        h.send_response(403)
        h.end_headers()
        return True
    if not p.startswith("/api/"):
        if not from_lan(h):                    # the webhook should only reach us directly from the home network
            log("webhook via proxy rejected (%s)" % _client(h))
            h.send_response(403)
            h.end_headers()
            return True
        return False
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except ValueError:
        data = {}
    if h.headers.get("X-Requested-With") != "flaggarr":       # blocks forms from other sites
        _json(h, {"ok": False, "msg": "bad request"})
        return True
    if not password_set() and p != "/api/password":
        _json(h, {"ok": False, "msg": "set a password first"})   # first run: only the account can be made
        return True
    set_cookie = None                                   # a route may hand the browser a new session
    if p == "/api/login":
        cookie, error = sign_in(h, str(data.get("password", "")))
        if cookie:
            log("UI: signed in from %s" % _client(h))
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.send_header("Set-Cookie", _cookie(h, cookie, 30 * 86400))
            h.end_headers()
            h.wfile.write(b'{"ok": true}')
        else:
            log("UI: failed sign-in from %s" % _client(h))
            _json(h, {"ok": False, "msg": error})
        return True
    if p == "/api/logout":
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Set-Cookie", _cookie(h, "gone", 0))
        h.end_headers()
        h.wfile.write(b'{"ok": true}')
        return True
    if p == "/api/plex_pin":
        try:
            _json(h, dict(plex_pin_new(), ok=True))
        except Exception:
            _json(h, {"ok": False, "msg": "could not reach plex.tv"})
        return True
    if p == "/api/plex_pin_check":
        try:
            r = plex_pin_user(data.get("id"))
        except (TypeError, ValueError):
            r = ("", "?")
        except Exception:
            _json(h, {"ok": False, "done": True, "msg": "could not reach plex.tv"})
            return True
        if r is None:
            _json(h, {"ok": False, "wait": True})
        elif r[0] == "@owner":
            log("UI: owner %s signed in with Plex from %s" % (r[1], _client(h)))
            tok = "i%d" % int(time.time())
            body = json.dumps({"ok": True, "name": r[1]}).encode("utf-8")
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.send_header("Set-Cookie", _cookie(h, "%s.%s" % (tok, _sig(tok)), 30 * 86400))
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        elif r[0]:
            log("UI: %s signed in with Plex from %s" % (r[1], _client(h)))
            body = json.dumps({"ok": True, "name": r[1]}).encode("utf-8")
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.send_header("Set-Cookie", _cookie(h, user_cookie(r[0]), 30 * 86400))
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)
        else:
            log("UI: Plex sign-in refused from %s - %s has no share here" % (_client(h), r[1]))
            _json(h, {"ok": False, "done": True, "msg": "That Plex account has no access to this server"})
        return True
    _u_uid = session_user(h)
    if _u_uid and p == "/api/user_profile":
        data["id"] = _u_uid
        data.pop("name", None)
        save_user_profile(_u_uid, data, True)
        log("UI: %s saved their own profile" % user_row(_u_uid)["name"])
        _json(h, {"ok": True})
        return True
    making_the_first_account = p == "/api/password" and not password_set()
    if not signed_in(h) and not making_the_first_account:
        h.send_response(401)
        h.send_header("Content-Type", "application/json")
        h.end_headers()
        h.wfile.write(b'{"ok": false, "msg": "sign in"}')
        return True
    if p == "/api/logout":
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Set-Cookie", _cookie(h, "x", 0))
        h.end_headers()
        h.wfile.write(b'{"ok": true}')
        return True
    if p == "/api/password":
        new_value = str(data.get("new", ""))
        if password_set() and not check_password(str(data.get("current", ""))):
            _json(h, {"ok": False, "msg": "current password is wrong"})
            return True
        if new_value and len(new_value) < 8:
            _json(h, {"ok": False, "msg": "at least 8 characters"})
            return True
        set_password(new_value)
        log("UI: password %s" % ("set" if new_value else "removed - the page is open again"))
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        if new_value:
            iss = "i%d" % int(time.time())        # same shape signed_in expects, so the new password signs you in
            h.send_header("Set-Cookie", _cookie(h, "%s.%s" % (iss, _sig(iss)), 30 * 86400))
        else:
            h.send_header("Set-Cookie", _cookie(h, "x", 0))
        h.end_headers()
        h.wfile.write(b'{"ok": true}')
        return True
    if p == "/api/credentials_remove":
        new_value = reset_credentials(log)
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Set-Cookie", _cookie(h, "x", 0))
        h.end_headers()
        h.wfile.write(json.dumps({"ok": True, "new_password": new_value}).encode("utf-8"))
        return True
    if p == "/api/settings":
        was_open = not bool(read_settings().get("auth_lan"))
        s = read_settings()
        for k in SWITCHES:
            if k in data:
                s[k] = bool(data[k])
        for k in NUMBERS:
            if k in data:
                try:
                    s[k] = max(0, int(data[k]))
                except (TypeError, ValueError):
                    pass
        for k in TEXT_FIELDS:
            if k in data:
                v = " ".join(str(data[k]).split())
                s[k] = v or DEFAULTS[k]
        apply_sub_rules(s)
        apply_alt_rules(s)
        apply_track_order(s)
        save_settings(s)
        if was_open and s.get("auth_lan") and password_set() and not signed_in(h):
            _iss = "i%d" % int(time.time())        # you asked for the lock; you are not who it locks out
            set_cookie = _window_cookie(h, "%s.%s" % (_iss, _sig(_iss)))
        elif not was_open and not s.get("auth_lan") and from_lan(h):
            set_cookie = _cookie(h, "gone", 0)    # the lock is off, so the session it needed goes quietly with it
        log("UI: settings saved " + json.dumps({k: data[k] for k in data if k in s}, ensure_ascii=False))
        reply = {"ok": True, "settings": s}
    elif p == "/api/connections":
        d = {k: data[k] for k, _, _ in ENV_FIELDS if k in data}
        d.update({k: str(data[k]) for k in data if k.endswith("_ENABLED")})
        save_env(d)
        _icon_cache.clear()
        log("UI: connections saved (" + ", ".join(sorted(d)) + ")")
        reply = {"ok": True}
    elif p == "/api/user_profile":
        uid = str(data.get("id") or "")
        names = {x["id"]: x["name"] for x in plex_users()}
        if uid not in names:
            reply = {"ok": False, "msg": "unknown user"}
        else:
            data["name"] = names[uid]
            save_user_profile(uid, data)
            log("UI: user profile %s (%s)" % ("reset to yours" if data.get("reset") else "saved", names[uid]))
            reply = {"ok": True}
    elif p == "/api/cache":
        clear_cache()
        log("UI: cached counts cleared")
        reply = {"ok": True}
    elif p == "/api/signout_all":
        save_settings({"secret": secrets.token_hex(32)})   # every cookie, admin and Plex user, stops being valid
        log("UI: every session signed out")
        reply = {"ok": True}
    elif p == "/api/logs_clear":
        what = str(data.get("what") or "")
        n = clear_logs(what)
        _w = {"listener": "listener", "action": "actions tab", "system": "system tab"}.get(what, "all")
        log("UI: logs cleared (%s, %d files)" % (_w, n))
        reply = {"ok": True, "n": n}
    elif p == "/api/quarantine_clear":
        n = clear_quarantine(str(data.get("folder", "")))
        log("UI: quarantine %s emptied permanently (%d files)" % (data.get("folder"), n))
        reply = {"ok": True, "count": n}
    elif p == "/api/test":
        reply = test_connection(str(data.get("service", "")), data.get("url"), data.get("key"))
    elif p == "/api/backup":
        name = make_backup()
        reply = {"ok": bool(name), "name": name}
    elif p == "/api/reset":
        make_backup()
        reset_settings()
        log("UI: settings reset to defaults (English only)")
        reply = {"ok": True}
    elif p == "/api/reset_everything":
        reset_everything(log)
        log("UI: everything reset - preferences, connections and sign-in")
        reply = {"ok": True}
    elif p == "/api/backup_delete":
        name = str(data.get("file", ""))
        ok = delete_backup(name)
        if ok:
            log("UI: backup %s deleted" % name)
        reply = {"ok": ok}
    elif p == "/api/restore":
        name = str(data.get("file", ""))
        ok = restore_backup(name)
        if ok:
            log("UI: settings restored from %s" % name)
        reply = {"ok": ok}
    elif p == "/api/run":
        name = data.get("job")
        extra = None
        if name == "trash":
            selected = [k for k in (data.get("categories") or []) if k in TRASH_CATEGORIES]
            extra = {"TRASH_CATEGORIES": ",".join(selected or ["orphaned"])}
        ok, msg = run_job(name, data.get("mode"), log, extra)
        reply = {"ok": ok, "msg": msg}
    elif p == "/api/stop":
        name = str(data.get("job", ""))
        ok = stop_job(name)
        if ok:
            log("UI: stop requested for %s" % job_label(name))
        elif dequeue_job(name):
            ok = True
            log("UI: %s taken out of the queue" % job_label(name))
        reply = {"ok": ok, "msg": "" if ok else "not running"}
    elif p == "/api/undo":
        ok, msg = undo_run(str(data.get("run") or ""), log)
        reply = {"ok": ok, "msg": msg}
    elif p == "/api/mkv_retry":
        ok, msg = mkv_retry(data.get("path"), log)
        reply = {"ok": ok, "msg": msg}
    elif p == "/api/quarantine_restore":
        folder = str(data.get("folder", ""))
        restored, missing, conflicts = restore_quarantine(folder)
        log("UI: quarantine %s restored (%d files, %d missing, %d conflicts)" % (folder, restored, missing, conflicts))
        reply = {"ok": True, "restored": restored, "missing": missing, "conflicts": conflicts}
    else:
        reply = {"ok": False, "msg": "unknown address"}
    _json(h, reply, set_cookie)
    return True
