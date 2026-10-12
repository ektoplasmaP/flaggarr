#!/usr/bin/env python3
"""
Sets default flags on every existing mkv file.

Walks through everything Radarr and Sonarr know about, looks up the original
language per title and calls auto_set_mkv_defaultflags.sh with the right
--original. That is the part that makes a foreign-language film default to its
native audio instead of the English dub.

New files are handled by webhook_listener.py, which gets a webhook from
Radarr/Sonarr on import. This run is the safety net and goes once a week.

  export RADARR_URL=http://radarr:7878
  export RADARR_APIKEY=...
  export SONARR_URL=http://sonarr:8989
  export SONARR_APIKEY=...

  python3 auto_mkv_defaultflags_batch.py                 # dry run, shows what would happen
  python3 auto_mkv_defaultflags_batch.py --quiet         # dry run, summary only
  python3 auto_mkv_defaultflags_batch.py --limit 5       # try five files first
  python3 auto_mkv_defaultflags_batch.py --apply         # do it

Files that are not .mkv are skipped - mkvpropedit can only change Matroska.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request

SETDEFAULTS = "/app/auto_set_mkv_defaultflags.sh"


def get(url, key):
    req = urllib.request.Request(url, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def collect_files(radarr, rkey, sonarr, skey):
    """(path, original language) for everything Radarr and Sonarr has files for."""
    out = []

    for m in get(radarr.rstrip("/") + "/api/v3/movie", rkey):
        lang = ((m.get("originalLanguage") or {}).get("name") or "").strip()
        f = (m.get("movieFile") or {}).get("path")
        if lang and f:
            out.append((f, lang))

    for s in get(sonarr.rstrip("/") + "/api/v3/series", skey):
        lang = ((s.get("originalLanguage") or {}).get("name") or "").strip()
        if not lang:
            continue
        try:
            files = get(sonarr.rstrip("/") + "/api/v3/episodefile?seriesId=%s" % s["id"],
                        skey)
        except Exception as exc:
            print("  could not read files for %s: %s" % (s.get("title"), exc))
            continue
        for f in files:
            if f.get("path"):
                out.append((f["path"], lang))

    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="do it (without this flag it is only a dry run)")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="stop after N files - handy for a first try")
    p.add_argument("--quiet", action="store_true",
                   help="do not show what each file would change to in a dry run")
    p.add_argument("--script", default=SETDEFAULTS,
                   help="path to auto_set_mkv_defaultflags.sh (default: %s)" % SETDEFAULTS)
    args = p.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in
           ("RADARR_URL", "RADARR_APIKEY", "SONARR_URL", "SONARR_APIKEY")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        sys.exit("Missing environment variables: %s" % ", ".join(missing))

    if not os.path.isfile(args.script):
        sys.exit("Cannot find %s" % args.script)

    try:
        every = collect_files(env["RADARR_URL"], env["RADARR_APIKEY"],
                              env["SONARR_URL"], env["SONARR_APIKEY"])
    except Exception as exc:
        sys.exit("Could not read from Radarr/Sonarr: %s" % exc)

    mkv = [(f, l) for f, l in every if f.lower().endswith(".mkv")]
    others = len(every) - len(mkv)
    absent = [f for f, _ in mkv if not os.path.isfile(f)]
    mkv = [(f, l) for f, l in mkv if os.path.isfile(f)]

    print("%d files known, of which %d mkv." % (len(every), len(mkv)))
    if others:
        print("%d skipped (not mkv)." % others)
    if absent:
        print("%d are not on disk and were skipped." % len(absent))


    if args.limit:
        mkv = mkv[:args.limit]
        print("Limited to %d files." % len(mkv))
    print("")

    ok = failed = 0
    for i, (f, lang) in enumerate(mkv, 1):
        cmd = ["sh", args.script, "--original", lang]
        if not args.apply:
            cmd.append("--dry-run")
        cmd.append(f)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            ok += 1
            if not args.apply and not args.quiet:
                for row in r.stdout.strip().splitlines():
                    print(row)
        else:
            failed += 1
            print("ERROR %s" % f)
            for row in (r.stdout + r.stderr).strip().splitlines():
                print("     %s" % row)
        if i % 50 == 0:
            print("... %d of %d" % (i, len(mkv)))

    print("")
    if args.apply:
        print("Done: %d files handled, %d errors." % (ok, failed))
    else:
        print("DRY RUN: %d files would be handled, %d errors." % (ok, failed))
        print("Run again with --apply when it looks right.")


if __name__ == "__main__":
    main()
