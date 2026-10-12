#!/usr/bin/env python3
"""Moves aside loose .srt files in languages that do not belong next to tagged content.

MANUAL. Never run by the listener or by any scheduled job.
Dry run by default - nothing is moved until you add --apply.
Never deletes anything: files are moved to a quarantine folder with the
directory structure preserved, plus a manifest of what was moved from where.

The default is --tags native-original --lang en, that is, loose English subtitles
next to native-language originals. The native-original tag is set by the auto
tagging in Radarr/Sonarr on titles whose original language is Swedish; on that
content there should be Swedish text but no English.

The script only touches TAGGED titles. --lang must therefore not be empty -
without a language filter the Swedish subtitles would be moved aside too, which
is exactly the opposite of what this is for.

Embedded tracks inside the video file are never touched, only loose files on disk.

Examples:
    python3 manual_unwanted_language_removal.py
    python3 manual_unwanted_language_removal.py --lang en,da
    python3 manual_unwanted_language_removal.py --apply
"""

import argparse
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request

DEFAULT_TAGS = "native-original"
DEFAULT_LANGS = "en"
DEFAULT_ROOT = "/volume1/PlexMedia"
DEFAULT_QUARANTINE = "/volume1/PlexMedia/Removed Subs/unwanted_language"


def api(base, key, path):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"X-API-KEY": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def has_tag(item, tags):
    """True if the item carries any of the tags. Exact match, not prefix."""
    return any(str(t) in tags for t in (item.get("tags") or []))


def external_subs(item, langs):
    """Loose subtitle files on disk. Embedded tracks in the video file are never touched."""
    out = []
    for s in item.get("subtitles") or []:
        if not s.get("path"):
            continue
        if s.get("embedded_track_id") is not None:
            continue
        if langs and s.get("code2") not in langs:
            continue
        out.append((s.get("code2") or "??", s["path"]))
    return out


def collect(base, key, langs, tags):
    found = []

    movies = api(base, key, "/api/movies?start=0&length=-1").get("data", [])
    for m in movies:
        if not has_tag(m, tags):
            continue
        for code, path in external_subs(m, langs):
            found.append(("movie", m.get("title", "?"), code, path))

    series = api(base, key, "/api/series?start=0&length=-1").get("data", [])
    for s in series:
        if not has_tag(s, tags) or not s.get("episodeFileCount"):
            continue
        sid = s.get("sonarrSeriesId")
        eps = api(base, key, "/api/episodes?seriesid[]=%s" % sid).get("data", [])
        for e in eps:
            label = "%s %sx%s" % (s.get("title", "?"), e.get("season"), e.get("episode"))
            for code, path in external_subs(e, langs):
                found.append(("series", label, code, path))

    return found


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="do the move (without the flag it is only a dry run)")
    p.add_argument("--permanent", action="store_true",
                   help="delete the files instead of moving them to quarantine (needs --apply)")
    p.add_argument("--lang", default=DEFAULT_LANGS,
                   help="comma-separated language codes to move aside "
                        "(default: %s). Must not be empty." % DEFAULT_LANGS)
    p.add_argument("--tags", default=DEFAULT_TAGS,
                   help="comma-separated Bazarr tags to match (default: %s). "
                     "Exact match, not prefix match."
                     % DEFAULT_TAGS)
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="the library root the quarantine layout is kept relative to")
    p.add_argument("--quarantine", default=DEFAULT_QUARANTINE,
                   help="where the files are moved (default: %s)" % DEFAULT_QUARANTINE)
    args = p.parse_args()

    base = os.environ.get("BAZARR_URL", "").strip()
    key = os.environ.get("BAZARR_APIKEY", "").strip()
    if not base or not key:
        sys.exit("Set BAZARR_URL and BAZARR_APIKEY as environment variables first.")

    langs = {x.strip() for x in args.lang.split(",") if x.strip()}
    tags = {x.strip() for x in args.tags.split(",") if x.strip()}
    if not tags:
        sys.exit("--tags must not be empty. The script only touches titles that "
                 "carry one of the tags, so without a tag there is nothing for "
                 "it to find. Set Native tag on the Cleanup page.")
    if not langs:
        sys.exit("--lang must not be empty. The script only touches tagged titles, "
                 "and without a language filter the Swedish subtitles would be "
                 "moved aside too.")
    if not tags:
        sys.exit("--tags must not be empty.")

    print("Tags matched: %s" % ", ".join(sorted(tags)))
    print("Languages to remove: %s" % (", ".join(sorted(langs)) if langs else "all"))
    print()

    try:
        rows = collect(base, key, langs, tags)
    except Exception as exc:
        sys.exit("Could not read from Bazarr: %s" % exc)

    if not rows:
        print("Nothing to do - found no loose subtitle files (%s) that match." % (", ".join(sorted(langs)) if langs else "all"))
        return

    missing = [r for r in rows if not os.path.isfile(r[3])]
    present = [r for r in rows if os.path.isfile(r[3])]

    per_lang = {}
    for _, _, code, _ in present:
        per_lang[code] = per_lang.get(code, 0) + 1

    print("Found %d subtitle files on disk (%s)" % (
        len(present), ", ".join("%s: %d" % kv for kv in sorted(per_lang.items()))))
    if missing:
        print("%d entries in Bazarr point at files that do not exist - skipped." % len(missing))
    print()

    for kind, title, code, path in present:
        print("  [%s] %-45s %s  %s" % (kind, title[:45], code, path))
    print()

    if not args.apply:
        print("DRY RUN - nothing has been changed.")
        print("Run again with --apply when the list looks right.")
        return

    manifest_path = os.path.join(args.quarantine, "manifest.tsv")
    os.makedirs(args.quarantine, exist_ok=True)

    moved = failed = 0
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for kind, title, code, path in present:
            # Directory structure preserved relative to the library root (Filmer/..., Serier/...)
            root = args.root.rstrip("/")
            rel = path.lstrip("/")
            if root and path.startswith(root + "/"):
                rel = path[len(root) + 1:]
            dest = os.path.join(args.quarantine, rel)
            try:
                if args.permanent:
                    os.remove(path)
                    mf.write("%s\tDELETED\n" % path)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(path, dest)
                    mf.write("%s\t%s\n" % (path, dest))
                moved += 1
            except Exception as exc:
                print("  FAILED: %s (%s)" % (path, exc))
                failed += 1

    if args.permanent:
        print("Deleted %d files permanently." % moved)
    else:
        print("Moved %d files to %s" % (moved, args.quarantine))
    if failed:
        print("%d failed - see the lines above." % failed)
    print("Manifest (from -> to): %s" % manifest_path)
    print()
    print("Then run a library scan in Plex so the tracks disappear from the interface.")


if __name__ == "__main__":
    main()
