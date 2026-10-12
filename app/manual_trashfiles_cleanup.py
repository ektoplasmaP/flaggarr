#!/usr/bin/env python3
"""Finds external .srt files that should not be there, and moves them aside.

MANUAL. Never run by the listener or by any scheduled job.
Dry run by default - nothing is moved until you add --apply.
Never deletes anything: files are moved to a quarantine folder with the
directory structure preserved, plus a manifest of what was moved from where.

Two categories, both things no Bazarr tag can express:

  orphaned     .srt with no video file of a matching name in the same folder.
               Plex never sees them, Bazarr does not count them, they just sit there.

  subs_folder  .srt sitting in a Subs/Subtitles folder next to the movie folder.
               Plex reads those in some setups, so they do NOT count as
               orphaned. Only included if you ask for the category explicitly.

  redundant    .srt whose language already exists inside the file as a subtitle
               track flaggarr is allowed to use: plain text always (SubRip/SRT
               in mkv, mov_text in mp4), plus PGS, VobSub or ASS/SSA when that
               type is set to something other than "never". Bazarr should not
               fetch one of those - but older downloads are still around from
               before the settings were right.

Category two needs mkvmerge or ffprobe. If both are missing it is skipped.

Examples:
    python3 manual_trashfiles_cleanup.py
    python3 manual_trashfiles_cleanup.py --category orphaned
    python3 manual_trashfiles_cleanup.py --apply
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

DEFAULT_ROOT = "/volume1/PlexMedia"
DEFAULT_QUARANTINE = "/volume1/PlexMedia/Removed Subs/trashfiles"

VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".webm")

CATEGORIES = ("orphaned", "redundant", "subs_folder")

# Folder names media servers look for subtitles in. An .srt in here belongs to
# the video file in the folder above, and so is not orphaned.
SUBS_DIR = ("subs", "subtitles", "sub", "undertexter")

# Which subtitle tracks inside the file make an external .srt redundant.
# Plain text always counts. An image or styled track (PGS, VobSub, ASS/SSA)
# counts only when its Subtitle types setting is something other than "never":
# if flaggarr would never pick such a track, the external .srt is still needed.
TEXT_CODECS = ("subrip", "srt", "mov_text")

TRACK_TYPES = ("pgs", "vobsub", "ass")

# SUB_PGS / SUB_VOBSUB / SUB_ASS come from the settings (Subtitles -> Subtitle
# types). Missing means "never", so a run without the environment behaves the
# way this script always did.
COUNTED_TYPES = tuple(
    k for k in TRACK_TYPES
    if os.environ.get("SUB_" + k.upper(), "never").strip().lower() != "never")


def track_type(codec):
    """"pgs", "vobsub" or "ass" for a non-text subtitle codec, "" for plain text.

    Same rule as track_rules.sub_type: an unrecognised non-text codec counts as
    pgs, so nothing slips past the gate just for being exotic.
    """
    c = (codec or "").lower()
    if any(k in c for k in TEXT_CODECS):
        return ""
    if "vobsub" in c or "dvdsub" in c or "dvd_subtitle" in c:
        return "vobsub"
    if "substation" in c or c in ("ass", "ssa"):
        return "ass"
    return "pgs"


def counts_as_subtitle(codec):
    """True if a track with this codec makes an external .srt of the same language redundant."""
    t = track_type(codec)
    return t == "" or t in COUNTED_TYPES

# Suffixes Bazarr and others append to the filename before .srt. They are
# peeled off one at a time to find the video file's base name.
FLAG_SUFFIX = ("forced", "sdh", "hi", "cc", "hearing", "default")

LANGUAGES = {
    "sv": "sv", "swe": "sv", "sve": "sv", "swedish": "sv",
    "en": "en", "eng": "en", "english": "en",
    "no": "no", "nor": "no", "nb": "no", "nob": "no", "norwegian": "no",
    "da": "da", "dan": "da", "danish": "da",
    "fi": "fi", "fin": "fi", "finnish": "fi",
    "de": "de", "ger": "de", "deu": "de", "german": "de",
    "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr",
    "es": "es", "spa": "es", "spanish": "es",
    "it": "it", "ita": "it", "italian": "it",
    "nl": "nl", "dut": "nl", "nld": "nl", "dutch": "nl",
    "pt": "pt", "por": "pt", "pl": "pl", "pol": "pl",
    "ja": "ja", "jpn": "ja", "ko": "ko", "kor": "ko",
    "zh": "zh", "chi": "zh", "zho": "zh",
    "ru": "ru", "rus": "ru", "ar": "ar", "ara": "ar",
    "und": "und", "unknown": "und",
}


def normalise_lang(bit):
    """'eng' and 'English' -> 'en'. Unknown bit -> None."""
    return LANGUAGES.get((bit or "").strip().lower())


def split_srt_name(filename):
    """Splits 'Film (2010).en.forced.srt' into ('Film (2010)', 'en').

    Returns (base name, language code or None). Peels off flag suffixes and an
    optional language code, in the order they usually sit.
    """
    stem = filename[:-4] if filename.lower().endswith(".srt") else filename
    lang = None
    while True:
        dot = stem.rfind(".")
        if dot <= 0:
            break
        bit = stem[dot + 1:].lower()
        if bit in FLAG_SUFFIX:
            stem = stem[:dot]
            continue
        code = normalise_lang(bit)
        if code and lang is None:
            lang = code
            stem = stem[:dot]
            continue
        break
    return stem, lang


def find_video(folder, stem, cache):
    """The video file the .srt belongs to, or None."""
    entries = cache.get(folder)
    if entries is None:
        try:
            entries = os.listdir(folder)
        except OSError:
            entries = []
        cache[folder] = entries
    low = stem.lower()
    for name in entries:
        root, ext = os.path.splitext(name)
        if ext.lower() in VIDEO_EXT and root.lower() == low:
            return os.path.join(folder, name)
    return None


def only_video(folder, cache):
    """The video file in the folder if there is exactly one, otherwise None."""
    entries = cache.get(folder)
    if entries is None:
        try:
            entries = os.listdir(folder)
        except OSError:
            entries = []
        cache[folder] = entries
    hits = [os.path.join(folder, n) for n in entries
               if os.path.splitext(n)[1].lower() in VIDEO_EXT]
    return hits[0] if len(hits) == 1 else None


def tool_exists(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep) + ["/usr/local/bin"]:
        if directory and os.path.isfile(os.path.join(directory, name)):
            return os.path.join(directory, name)
    return None


def internal_sub_langs(video, mkvmerge, ffprobe):
    """Language codes for text-based subtitle tracks inside the video file.

    Returns a set, or None if the file could not be read. None and an empty
    set are not the same thing - None means "don't know" and must never lead
    to anything being moved.
    """
    if video.lower().endswith(".mkv") and mkvmerge:
        try:
            out = subprocess.check_output(
                [mkvmerge, "--command-line-charset", "UTF-8",
                 "--output-charset", "UTF-8", "-J", video],
                stderr=subprocess.DEVNULL)
            data = json.loads(out.decode("utf-8", "replace"))
        except Exception:
            return None
        found = set()
        for track in data.get("tracks", []):
            if track.get("type") != "subtitles":
                continue
            codec = (track.get("codec") or "").lower()
            if not counts_as_subtitle(codec):
                continue
            props = track.get("properties") or {}
            code = normalise_lang(props.get("language") or "und")
            if code:
                found.add(code)
        return found

    if ffprobe:
        try:
            out = subprocess.check_output(
                [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-select_streams", "s", video],
                stderr=subprocess.DEVNULL)
            data = json.loads(out.decode("utf-8", "replace"))
        except Exception:
            return None
        found = set()
        for track in data.get("streams", []):
            codec = (track.get("codec_name") or "").lower()
            if not counts_as_subtitle(codec):
                continue
            tags = track.get("tags") or {}
            code = normalise_lang(tags.get("language") or "und")
            if code:
                found.add(code)
        return found

    return None


def collect_files(root, categories, mkvmerge, ffprobe):
    """Walks the tree and returns a list of (category, file_path, detail)."""
    findings = []
    cache = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [k for k in dirs
                        if k not in ("#recycle", "@eaDir")
                        and k != "Removed Subs"]
        for name in files:
            if not name.lower().endswith(".srt"):
                continue
            file_path = os.path.join(folder, name)
            stem, lang = split_srt_name(name)
            video = find_video(folder, stem, cache)

            if video is None and os.path.basename(folder).lower() in SUBS_DIR:
                parent = os.path.dirname(folder)
                video = find_video(parent, stem, cache)
                if video is None:
                    video = only_video(parent, cache)
                if video is not None:
                    if "subs_folder" in categories:
                        findings.append(("subs_folder", file_path,
                                     "sits in %s next to %s" % (os.path.basename(folder),
                                                                 os.path.basename(video))))
                    continue

            if video is None:
                if "orphaned" in categories:
                    findings.append(("orphaned", file_path, "no video named %s" % stem))
                continue

            if "redundant" not in categories:
                continue
            if lang is None:
                continue
            internal = internal_sub_langs(video, mkvmerge, ffprobe)
            if internal is None:
                continue
            if lang in internal:
                findings.append(("redundant", file_path,
                             "%s already exists as a subtitle track in %s" % (lang, os.path.basename(video))))
    return findings


def main():
    p = argparse.ArgumentParser(description="Moves aside external .srt files that should not be there.")
    p.add_argument("--apply", action="store_true",
                   help="do the move (without the flag it is only a dry run)")
    p.add_argument("--root", default=DEFAULT_ROOT,
                   help="folder to walk (default: %s)" % DEFAULT_ROOT)
    p.add_argument("--category", default="all",
                   help="what kind of junk to look for. 'all' = orphaned "
                        "+ redundant. Several can be combined with commas, e.g. "
                        "orphaned,redundant. subs_folder must be asked for explicitly")
    p.add_argument("--quarantine", default=DEFAULT_QUARANTINE,
                   help="where the files are moved (default: %s)" % DEFAULT_QUARANTINE)
    p.add_argument("--permanent", action="store_true",
                   help="delete the files instead of moving them to quarantine (needs --apply). Cannot be undone.")
    p.add_argument("--limit", type=int, default=0,
                   help="stop after N hits, 0 = no limit")
    args = p.parse_args()

    if not os.path.isdir(args.root):
        sys.exit("Cannot find %s" % args.root)

    if args.category == "all":
        categories = ("orphaned", "redundant")
    else:
        categories = tuple(k.strip() for k in args.category.split(",") if k.strip())
        unknown = [k for k in categories if k not in CATEGORIES]
        if unknown:
            sys.exit("Unknown category: %s" % ", ".join(unknown))
        if not categories:
            sys.exit("No category selected")
    if "subs_folder" in categories:
        print("NOTE: Plex reads Subs folders in some setups. Check the")
        print("      list extra carefully before running with --apply.")
        print()

    mkvmerge = tool_exists("mkvmerge")
    ffprobe = tool_exists("ffprobe")
    if "redundant" in categories and not mkvmerge and not ffprobe:
        print("Warning: neither mkvmerge nor ffprobe was found - skipping 'redundant'.")
        categories = tuple(k for k in categories if k != "redundant")
        if not categories:
            sys.exit("Nothing left to look for.")

    print("Root: %s" % args.root)
    print("Categories: %s" % ", ".join(categories))
    print()

    findings = collect_files(args.root, categories, mkvmerge, ffprobe)
    if args.limit:
        findings = findings[:args.limit]

    if not findings:
        print("Nothing to do - found no .srt that breaks the rules.")
        return

    per_category = {}
    for category, _, _ in findings:
        per_category[category] = per_category.get(category, 0) + 1
    print("Found %d files (%s)" % (
        len(findings), ", ".join("%s: %d" % kv for kv in sorted(per_category.items()))))
    print()

    for category, file_path, detail in findings:
        print("  [%s] %s" % (category, file_path))
        print("      %s" % detail)
    print()

    if not args.apply:
        print("DRY RUN - nothing has been changed.")
        print("Run again with --apply when the list looks right.")
        return

    manifest_path = os.path.join(args.quarantine, "manifest.tsv")
    os.makedirs(args.quarantine, exist_ok=True)

    moved = failed = 0
    with open(manifest_path, "a", encoding="utf-8") as mf:
        for category, file_path, detail in findings:
            rel = os.path.relpath(file_path, args.root)
            dest = os.path.join(args.quarantine, rel)
            try:
                if args.permanent:
                    os.remove(file_path)
                    mf.write("%s\t%s\tDELETED\n" % (category, file_path))
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(file_path, dest)
                    mf.write("%s\t%s\t%s\n" % (category, file_path, dest))
                moved += 1
            except Exception as exc:
                print("  FAILED: %s (%s)" % (file_path, exc))
                failed += 1

    if args.permanent:
        print("Deleted %d files permanently." % moved)
    else:
        print("Moved %d files to %s" % (moved, args.quarantine))
    if failed:
        print("%d failed - see the lines above." % failed)
    print("Manifest (category, from, to): %s" % manifest_path)
    print()
    print("Then run a library scan in Plex so the tracks disappear from the interface.")


if __name__ == "__main__":
    main()
