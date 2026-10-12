#!/usr/bin/env python3
"""Corrects the saved Plex audio and subtitle selections for your account. The file itself is never touched.

Run automatically by webhook_listener.py after every import (--recent 5), and
can be run by hand over the whole library.

Audio: the original language first when it is known, then the audio priority list.

Subtitles, based on the original language of the title:
    a language you understand -> no subtitles at all
    a same-language title     -> subtitles in that language
    anything else             -> the subtitle list, in order

A full subtitle always beats a forced one, even across the language boundary: a
full English subtitle wins over a forced track in your own language. Within the
same rank the language order decides, then internal SRT before external. Track
type is decided before language - see Subtitles -> Subtitle types in the UI.

Only text-based subtitles can be selected (TEXT_CODECS): SubRip/SRT in mkv and
mov_text in mp4/m4v. PGS and VobSub are images and cannot be preselected.
ASS/SSA does not count either - it is usually a signs track, not dialogue.

This script is the only way to preselect an external .srt or mov_text in an
mp4: a default flag is a property of a track inside a Matroska file, and when
it cannot be set, the saved Plex selection is what decides.

The script fetches full metadata per title (/library/metadata/<ratingKey>), not
just the section listing. includeMediaStreams=1 is only respected on individual
items - without that step the stream list comes back empty.

Flags: --apply, --audio-only, --subs-only, --recent N, --wait SEC.
Without --apply it is only a dry run.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import track_rules as rules   # the shared selection rules, next to this file

AUDIO = 2
SUBTITLE = 3

# The language table lives in track_rules.py, together with the picking rules,
# so both ends of the chain use the same one. Here it is turned back into
# name -> accepted codes, which is how Radarr/Sonarr and Plex are matched up.
LANGUAGE_CODES = {name: set((code,) + aliases)
                  for name, (code, aliases) in rules.LANGUAGES.items()}


# The lists from the UI (Media), as Plex names: understood without subtitles /
# subtitled in the same language / otherwise in order.
NATIVE_NAMES = tuple(os.environ.get("NATIVE_LANGS_NAMES", "swedish").split())
SAME_SUB_NAMES = tuple(os.environ.get("SAME_SUB_LANGS_NAMES", "english").split())
SUB_NAMES = tuple(os.environ.get("SUB_LANGS_NAMES", "swedish english").split())
# Audio when the original language is missing from the file: first match in
# this list (ISO 639-2, same as the mkv flag).
AUDIO_PRIO = tuple(os.environ.get("AUDIO_PRIORITY", "eng swe").split())
# The alternative set, for titles carrying the tag under Languages -> Tag rule.
ALT_AUDIO_PRIO = tuple(os.environ.get("ALT_AUDIO_PRIORITY", "").split())
ALT_NATIVE_NAMES = tuple(os.environ.get("ALT_NATIVE_LANGS_NAMES", "").split())
ALT_SAME_NAMES = tuple(os.environ.get("ALT_SAME_SUB_LANGS_NAMES", "").split())
# Subtitle track types (UI: Subtitles -> Subtitle types)
SUB_SDH = os.environ.get("SUB_SDH", "never")        # never | last | allowed | first
SUB_FORCED = os.environ.get("SUB_FORCED", "last")   # never | last | allowed | first
SUB_PGS = os.environ.get("SUB_PGS", "never")
SUB_VOBSUB = os.environ.get("SUB_VOBSUB", "never")
SUB_FORCED_MODE = os.environ.get("SUB_FORCED_MODE", "nofull")   # nofull | native
SUB_ASS = os.environ.get("SUB_ASS", "never")  # never | last (PGS/VobSub as a last resort - internal tracks only)
CODEC_RULES = os.environ.get("AUDIO_CODEC_RULES", "").replace(",", " ").split()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def plex(base, token, path, **params):
    params["X-Plex-Token"] = token
    sep = "&" if "?" in path else "?"
    url = "%s%s%s%s" % (base.rstrip("/"), path, sep, urllib.parse.urlencode(params))
    return get(url, {"Accept": "application/json"})


def plex_put(base, token, path, **params):
    params["X-Plex-Token"] = token
    url = "%s%s?%s" % (base.rstrip("/"), path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, method="PUT")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def language_map(base, key, kind):
    """File path -> original language (lower case), from Radarr or Sonarr."""
    h = {"X-Api-Key": key}
    b = base.rstrip("/")
    m = {}

    if kind == "radarr":
        for mv in get(b + "/api/v3/movie", h):
            lang = ((mv.get("originalLanguage") or {}).get("name") or "").lower()
            f = mv.get("movieFile") or {}
            if lang and f.get("path"):
                m[f["path"]] = lang
    else:
        for s in get(b + "/api/v3/series", h):
            lang = ((s.get("originalLanguage") or {}).get("name") or "").lower()
            if not lang:
                continue
            try:
                files = get(b + "/api/v3/episodefile?seriesId=%s" % s["id"], h)
            except Exception:
                continue
            for f in files:
                if f.get("path"):
                    m[f["path"]] = lang
    return m


def _codec_of(stream):
    """The codec name the rules are matched against.

    Plex reports every DTS flavour as `dca` and puts the flavour in `profile`, so the two are folded
    into one name here - the same name the mkv side gets from mkvmerge for the same track. Without
    this a `dtshd` rule would reach the flags inside the files but never the selections saved in Plex.
    """
    codec = (stream.get("codec") or "").lower()
    if codec in ("dca", "dts"):
        parts = (stream.get("profile") or "").lower().replace("+", " ").replace(":", " ").split()
        if "ma" in parts:                     # DTS-HD Master Audio, lossless. DTS:X sits on top of it.
            return "dtshd"
        if "hra" in parts:                    # DTS-HD High Resolution, lossy
            return "dtshdhra"
        if "es" in parts:
            return "dtses"
        return "dts"
    return codec


def _tracks(streams):
    """Plex streams -> the normalised Tracks track_rules works on.

    ref is the Plex stream itself, so the caller gets it straight back.
    """
    out = []
    for i, s in enumerate(streams):
        kind = ("audio" if s.get("streamType") == AUDIO
                else "subtitles" if s.get("streamType") == SUBTITLE else None)
        if not kind:
            continue
        title = ((s.get("title") or "") + " " + (s.get("displayTitle") or "")).lower()
        out.append(rules.Track(
            kind=kind,
            ref=s,
            lang=s.get("languageCode") or "und",
            order=i,
            channels=s.get("channels") or 0,
            commentary=rules.is_commentary(s.get("title"), s.get("displayTitle")),
            text=(s.get("codec") or "").lower() in rules.TEXT_CODECS,
            sdh=_is_sdh(s),
            forced=s.get("forced") in (True, 1, "1"),
            default=bool(s.get("selected")),
            external=bool(s.get("key")),
            codec=_codec_of(s),
        ))
    return out


def _codec_label(stream):
    """Language plus codec for the log, e.g. "eng/truehd". Without the codec a line
    like "audio eng -> eng" says nothing at all when only the track changed."""
    if not stream:
        return "-"
    lang = stream.get("languageCode") or "und"
    codec = _codec_of(stream)
    return lang + ("/" + codec if codec else "")


def selected(streams, stype):
    for s in streams:
        if s.get("streamType") == stype and s.get("selected"):
            return s
    return None


def _is_sdh(s):
    """Whether the track is SDH. Ranked by the SDH setting under Subtitle types."""
    if s.get("hearingImpaired") in (True, 1, "1"):
        return True
    t = ((s.get("title") or "") + " " + (s.get("displayTitle") or "")).lower()
    return "sdh" in t or "hearing" in t


def pick_audio(streams, codes):
    """The best audio track in one language, given every spelling of its code."""
    track = rules.pick_audio(_tracks(streams), rules.canon_list(sorted(codes)), codecs=CODEC_RULES)
    return track.ref if track else None


def pick_subtitle(streams, names, forced=None):
    """The subtitle track to select, for these languages in order (Plex names).
    `forced` is "only" when the row for the spoken language asks for forced subtitles."""
    want = rules.canon_list([rules.to_code(n) for n in names])
    track = rules.pick_subtitle(_tracks(streams), want, SUB_SDH, forced or SUB_FORCED,
                                SUB_PGS, SUB_VOBSUB, SUB_ASS)
    return track.ref if track else None


HANDLE_SEEN = [0]          # progress heartbeat for the whole run
UNDO_PATH = os.environ.get("FLAGGARR_UNDO", "").strip()


def _journal(part, changes, cur_audio, cur_sub):
    """Appends what this title had selected before the change, one JSON line per title, so a real run
    can be put back from the UI. Only written when ui.py points FLAGGARR_UNDO at a journal."""
    if not UNDO_PATH:
        return
    rec = {"part": part.get("id")}
    if "audioStreamID" in changes:
        rec["audio"] = (cur_audio or {}).get("id") or 0
    if "subtitleStreamID" in changes:
        rec["sub"] = (cur_sub or {}).get("id") or 0
    try:
        with open(UNDO_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass



def handle(base, token, title_name, part, orig, apply_changes, log, unknown_langs,
           audio_only=False, subs_only=False):
    HANDLE_SEEN[0] += 1
    if HANDLE_SEEN[0] % 200 == 0:
        print("... %d files checked so far" % HANDLE_SEEN[0], flush=True)
    streams = part.get("Stream") or []
    if not streams or not orig:
        return 0

    codes = LANGUAGE_CODES.get(orig)
    if not codes:
        unknown_langs.add(orig)
        return 0

    alt = rules.alt_tag_hit(part.get("file") or "")   # tagged titles follow the other set
    prio_set = ALT_AUDIO_PRIO if alt else AUDIO_PRIO
    native_set = ALT_NATIVE_NAMES if alt else NATIVE_NAMES
    same_set = ALT_SAME_NAMES if alt else SAME_SUB_NAMES
    if rules.SKIP in prio_set:                       # Let Plex decide, audio
        subs_only = True
    if rules.has_skip(os.environ.get("ALT_SUB_RULES" if alt else "SUB_RULES", "")):
        audio_only = True                            # Let Plex decide, subtitles
    if subs_only and audio_only:
        return 0

    changes = {}
    cur_audio = selected(streams, AUDIO)
    cur_sub = selected(streams, SUBTITLE)
    description = []

    prio = list(prio_set)
    if rules.ORIGINAL not in prio:            # no placeholder: the per-language table decides
        table = rules.language_map("ALT_AUDIO_MAP" if alt else "AUDIO_MAP")
        want = table.get(rules.to_code(orig))
        if not want and table.get("other") == rules.canon(rules.SKIP):
            return 0                          # the table says this title is not ours to touch
        if not want and (table.get("other") or rules.ORIGINAL) == rules.ORIGINAL:
            want = rules.ORIGINAL             # an empty row means the same as orig, as on the mkv side
        if want:
            prio = [want] + prio
        prio = prio + [rules.ORIGINAL]            # the same fallback as track_rules.expand_original
    target = None
    spoken = orig                             # the language that will actually play
    if subs_only and cur_audio and cur_audio.get("language"):
        spoken = cur_audio["language"]        # we are not changing it, so read it
    if not subs_only:
        for code in prio:
            if code == rules.ORIGINAL:
                name_, want_codes = orig, codes
            else:
                name_ = next((n for n, k in LANGUAGE_CODES.items() if code in k), None)
                want_codes = LANGUAGE_CODES.get(name_) or set()
            target = pick_audio(streams, want_codes) if want_codes else None
            if target:
                spoken = name_ or orig
                break
    if target and (not cur_audio or cur_audio.get("id") != target.get("id")):
        changes["audioStreamID"] = target["id"]
        description.append("audio %s -> %s" % (
            _codec_label(cur_audio), _codec_label(target)))

    # Which subtitle languages this title should get, by the same rule the mkv
    # side uses: understood without text -> none, same-language -> that one,
    # otherwise the fallback list. Keyed on the language that will play, not on
    # the title's own language - with a dub picked, those are not the same.
    _subrules = os.environ.get("ALT_SUB_RULES" if alt else "SUB_RULES", "")
    want = [] if rules.subs_never(_subrules) else rules.want_sub_langs(rules.to_code(spoken),
                                [rules.to_code(n) for n in native_set],
                                [rules.to_code(n) for n in same_set],
                                [rules.to_code(n) for n in SUB_NAMES],
                                rules.sub_map(_subrules))
    want, _fmode = rules.sub_plan(rules.to_code(spoken), want,
                                  [rules.to_code(n) for n in native_set],
                                  rules.sub_forced(_subrules), SUB_FORCED,
                                  SUB_FORCED_MODE == "native" and not rules.subs_never(_subrules),
                                  [rules.to_code(n) for n in SUB_NAMES] if rules.subs_never(_subrules) and SUB_FORCED != "never" else None)

    if not want and cur_sub and not audio_only:
        changes["subtitleStreamID"] = 0
        description.append("subs %s -> none" % cur_sub.get("languageCode", "?"))

    if not audio_only and want and "subtitleStreamID" not in changes:
        val = pick_subtitle(streams, want, _fmode)
        if val and (not cur_sub or cur_sub.get("id") != val.get("id")):
            changes["subtitleStreamID"] = val["id"]
            description.append("text -> %s %s" % (
                "external" if val.get("key") else "internal",
                val.get("languageCode") or "-"))
        elif cur_sub is not None and _is_sdh(cur_sub) and SUB_SDH == "never":  # PRIO
            # An SDH track must not stay selected. When there is no acceptable
            # track to switch to we clear the selection instead of leaving it.
            changes["subtitleStreamID"] = 0
            description.append("subs SDH -> none")
        elif (cur_sub is not None and rules.subs_never(_subrules)
              and cur_sub.get("forced") not in (True, 1, "1")):
            # "Never" means no full subtitle. Forced lines are the one exception
            # to that, so a forced track may stay selected, but a full one must
            # not: with forced allowed, want is not empty, so the clear further
            # up does not fire and the old selection would otherwise survive.
            changes["subtitleStreamID"] = 0
            description.append("subs never -> none")

    if not changes:
        return 0

    log("  %-52s [%s] %s" % (title_name[:52], orig[:3], ", ".join(description)))
    if apply_changes:
        _journal(part, changes, cur_audio, cur_sub)
        changes["allParts"] = 1
        plex_put(base, token, "/library/parts/%s" % part["id"], **changes)
    return 1


def recently_added(env, key, n):
    """(name, part) for the N most recently added titles in a library."""
    data = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                "/library/sections/%s/recentlyAdded" % key,
                **{"X-Plex-Container-Start": 0, "X-Plex-Container-Size": n})
    ut = []
    for item in data["MediaContainer"].get("Metadata", []):
        full = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                    "/library/metadata/%s" % item["ratingKey"],
                    includeMediaStreams=1)
        m = full["MediaContainer"]["Metadata"][0]
        if m.get("type") == "episode":
            name_ = "%s %sx%s" % (m.get("grandparentTitle", "?"),
                                 m.get("parentIndex"), m.get("index"))
        else:
            name_ = m.get("title", "?")
        for media in m.get("Media", []):
            for part in media.get("Part", []):
                ut.append((name_, part))
    return ut


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="apply the changes (without the flag it is only a dry run)")
    p.add_argument("--show-unmatched", action="store_true",
                   help="list the files in Plex with no match in Radarr/Sonarr")
    p.add_argument("--audio-only", action="store_true",
                   help="only touch the audio tracks, leave saved subtitle "
                        "selections alone (safe to repeat)")
    p.add_argument("--subs-only", action="store_true",
                   help="only touch the subtitle selection, not the audio")
    p.add_argument("--recent", type=int, default=0, metavar="N",
                   help="only look at the N most recently added titles per library "
                        "instead of all of them. Fast - meant for calls from "
                        "Radarr/Sonarr on import.")
    p.add_argument("--wait", type=int, default=0, metavar="SEC",
                   help="wait this many seconds before starting, so Plex has time "
                        "to index a freshly imported file")
    args = p.parse_args()

    env = {k: os.environ.get(k, "").strip() for k in
           ("PLEX_URL", "PLEX_TOKEN", "RADARR_URL", "RADARR_APIKEY",
            "SONARR_URL", "SONARR_APIKEY")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        sys.exit("Missing environment variables: %s" % ", ".join(missing))

    log = print

    if args.wait:
        log("Waiting %d seconds for Plex to index..." % args.wait)
        time.sleep(args.wait)

    try:
        karta = language_map(env["RADARR_URL"], env["RADARR_APIKEY"], "radarr")
        movie_count = len(karta)
        karta.update(language_map(env["SONARR_URL"], env["SONARR_APIKEY"], "sonarr"))
    except Exception as exc:
        sys.exit("Could not read original languages from Radarr/Sonarr: %s" % exc)

    log("Original language known for %d files (%d movies, %d episodes)."
        % (len(karta), movie_count, len(karta) - movie_count))

    try:
        sections = plex(env["PLEX_URL"], env["PLEX_TOKEN"], "/library/sections")
    except Exception as exc:
        sys.exit("Could not read from Plex: %s" % exc)

    changed = 0
    unknown_langs = set()
    saknar_karta = []

    def section(heading):
        """The heading is written only when something is actually logged under
        it, so empty libraries do not leave headings without content."""
        written = []

        def emit(row):
            if not written:
                log("")
                log("%s" % heading)
                written.append(True)
            log(row)

        return emit

    for d in sections["MediaContainer"].get("Directory", []):
        if args.recent:
            slog = section("%s (%d most recently added)" % (d["title"], args.recent))
            for name_, part in recently_added(env, d["key"], args.recent):
                orig = karta.get(part.get("file"))
                if not orig:
                    saknar_karta.append(part.get("file") or "?")
                changed += handle(env["PLEX_URL"], env["PLEX_TOKEN"], name_, part,
                                  orig, args.apply, slog, unknown_langs, args.audio_only, args.subs_only)
            continue

        if d.get("type") == "movie":
            slog = section(d["title"])
            data = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                        "/library/sections/%s/all" % d["key"], includeMediaStreams=1)
            for item in data["MediaContainer"].get("Metadata", []):
                item = plex(env["PLEX_URL"], env["PLEX_TOKEN"], "/library/metadata/%s" % item["ratingKey"], includeMediaStreams=1)["MediaContainer"]["Metadata"][0]  # FULLMETA
                for media in item.get("Media", []):
                    for part in media.get("Part", []):
                        orig = karta.get(part.get("file"))
                        if not orig:
                            saknar_karta.append(part.get("file") or "?")
                        changed += handle(env["PLEX_URL"], env["PLEX_TOKEN"],
                                          item.get("title", "?"), part, orig,
                                          args.apply, slog, unknown_langs,
                                          args.audio_only, args.subs_only)

        elif d.get("type") == "show":
            slog = section(d["title"])
            shows = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                         "/library/sections/%s/all" % d["key"])
            for show in shows["MediaContainer"].get("Metadata", []):
                leaves = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                              "/library/metadata/%s/allLeaves" % show["ratingKey"])
                for ep in leaves["MediaContainer"].get("Metadata", []):
                    full = plex(env["PLEX_URL"], env["PLEX_TOKEN"],
                                "/library/metadata/%s" % ep["ratingKey"],
                                includeMediaStreams=1)
                    m = full["MediaContainer"]["Metadata"][0]
                    name_ = "%s %sx%s" % (show.get("title", "?"),
                                         m.get("parentIndex"), m.get("index"))
                    for media in m.get("Media", []):
                        for part in media.get("Part", []):
                            orig = karta.get(part.get("file"))
                            if not orig:
                                saknar_karta.append(part.get("file") or "?")
                            changed += handle(env["PLEX_URL"], env["PLEX_TOKEN"],
                                              name_, part, orig, args.apply,
                                              slog, unknown_langs, args.audio_only, args.subs_only)

    log("")
    if saknar_karta:
        log("%d files in Plex have no match in Radarr/Sonarr - they were left alone."
            % len(saknar_karta))
        if args.show_unmatched:
            for f in sorted(saknar_karta):
                log("    %s" % f)
        else:
            log("    Run with --show-unmatched to see which ones.")
    if unknown_langs:
        log("Unknown original languages (left alone): %s" % ", ".join(sorted(unknown_langs)))
        log("Add them to LANGUAGE_CODES at the top of the file to cover them.")

    if changed == 0:
        log("Nothing to change - every selection is already correct.")
    elif args.apply:
        log("Changed %d titles." % changed)
    else:
        log("DRY RUN - %d titles would change. Run again with --apply." % changed)


if __name__ == "__main__":
    main()
