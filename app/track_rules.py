#!/usr/bin/env python3
"""The track-selection rules, in one place.

Both ends of the chain have to agree on which audio track and which subtitle
track a title should end up with:

  auto_set_mkv_defaultflags.sh   writes the default flags inside the mkv file
  auto_set_plex_defaults.py      writes the saved choice in Plex

They used to carry their own copy of the rules - one in jq, one in Python -
so every change had to be made twice, in two languages, and the two could
drift apart without anything failing. Now both feed their tracks into the
same pickers below.

Each side normalises its own tracks into a Track and calls pick_audio() and
pick_subtitle(). Run as a script this module is the mkv side: it reads
`mkvmerge -J` JSON on stdin and prints what the shell script needs.

The settings come from the environment, the same ones settings_env.py writes:
AUDIO_PRIORITY, NATIVE_LANGS, SAME_SUB_LANGS, SUB_LANGS, SUB_SDH, SUB_FORCED,
SUB_PGS, SUB_VOBSUB, SUB_ASS, SUB_CODECS.
"""

import json
import os
import re
import sys

# Plex, mkvmerge and Radarr/Sonarr disagree about language codes - Plex says
# "fra" and "deu" where mkvmerge says "fre" and "ger". So each language has one
# canonical code, the one the rest of flaggarr uses (ISO 639-2/B, the same list
# as LANGUAGES in ui.py), plus every spelling that has to be accepted.
LANGUAGES = {
    "english":    ("eng", ("en",)),
    "swedish":    ("swe", ("sv", "sve")),
    "danish":     ("dan", ("da",)),
    "norwegian":  ("nor", ("no", "nb", "nob")),
    "finnish":    ("fin", ("fi",)),
    "icelandic":  ("ice", ("is", "isl")),
    "german":     ("ger", ("de", "deu")),
    "french":     ("fre", ("fr", "fra")),
    "spanish":    ("spa", ("es",)),
    "italian":    ("ita", ("it",)),
    "dutch":      ("dut", ("nl", "nld")),
    "portuguese": ("por", ("pt",)),
    "polish":     ("pol", ("pl",)),
    "russian":    ("rus", ("ru",)),
    "ukrainian":  ("ukr", ("uk",)),
    "japanese":   ("jpn", ("ja",)),
    "korean":     ("kor", ("ko",)),
    "chinese":    ("chi", ("zh", "zho")),
    "hindi":      ("hin", ("hi",)),
}

# every accepted spelling -> the canonical code
CANON = {}
for _name, (_code, _aliases) in LANGUAGES.items():
    CANON[_name] = _code
    CANON[_code] = _code
    for _a in _aliases:
        CANON[_a] = _code

TEXT_CODECS = ("srt", "subrip", "mov_text")

COMMENTARY_WORDS = ("commentary", "kommentar")


def is_commentary(*titles):
    """True if any of the track titles names it a commentary track.

    One definition for both ends of the chain. They used to carry their own:
    the mkv side matched two languages, the Plex side only English and only
    one of the two title fields, so a Swedish-titled commentary track was
    passed over in the file and still selectable in Plex.
    """
    text = " ".join(t for t in titles if t).lower()
    return any(w in text for w in COMMENTARY_WORDS)

def norm_codec(x):
    """A codec name reduced to letters and digits, so the many spellings of one codec
    match: mkvmerge writes "E-AC-3" and "DTS-HD Master Audio", Plex writes "eac3".
    Plex also calls DTS "dca", which is folded into dts here so one name covers both.
    Matching is by substring, so "dts" catches every DTS flavour while "dtshd" catches
    only the lossless ones."""
    s = re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    if s.startswith("dca"):
        s = "dts" + s[3:]
    return s


def canon(code):
    """'sv', 'swe' -> 'swe'. An unknown code is passed through lowercased."""
    c = (code or "").strip().lower()
    return CANON.get(c, c or "und")


def canon_list(codes):
    """A space-separated or list of codes -> canonical codes, order kept, no repeats."""
    if isinstance(codes, str):
        codes = codes.split()
    out = []
    for c in codes:
        k = canon(c)
        if k and k not in out:
            out.append(k)
    return out


def to_code(x):
    """'Swedish', 'swe' and 'sv' all -> 'swe'. Radarr/Sonarr send names,
    mkvmerge and Plex send codes, so accept either."""
    k = (x or "").strip().lower()
    return CANON.get(k, k)


class Track(object):
    """One audio or subtitle track, as the rules see it.

    kind      "audio" or "subtitles"
    ref       whatever the caller needs to act on the track (a Plex stream, a
              track number) - the rules never look inside it
    lang      canonical ISO 639-2 code
    order     position in the file, used as the last tie-break so the same
              file always gives the same answer
    """

    __slots__ = ("kind", "ref", "lang", "order", "channels", "commentary",
                 "text", "sdh", "forced", "default", "external", "codec")

    def __init__(self, kind, ref, lang, order, channels=0, commentary=False,
                 text=True, sdh=False, forced=False, default=False, external=False,
                 codec=""):
        self.kind = kind
        self.ref = ref
        self.lang = canon(lang)
        self.order = order
        self.channels = channels or 0
        self.commentary = commentary
        self.text = text
        self.sdh = sdh
        self.forced = forced
        self.default = default
        self.external = external
        self.codec = norm_codec(codec)


CODEC_RULES = ("prefer", "avoid")


def parse_codec_rules(spec):
    """"truehd:prefer dtshd:avoid" -> [("truehd", "prefer"), ("dtshd", "avoid")].
    A bare name without a rule means avoid, which is all this setting used to be.
    Names are normalised the same way as the codec on a track, so matching is by
    substring: "dts" covers every DTS flavour, "dtshd" only the lossless ones."""
    if isinstance(spec, str):
        spec = spec.replace(",", " ").split()
    out = []
    for item in spec or ():
        name, _, rule = str(item).partition(":")
        name = norm_codec(name)
        rule = (rule or "avoid").strip().lower()
        if name and rule in CODEC_RULES:
            out.append((name, rule))
    return out


def _codec_rank(rules):
    """Scores a track -1 when its codec is preferred, 1 when it is avoided and 0 when no
    rule mentions it, so it can go straight into a sort key. The first rule in the list
    that matches a track wins."""
    parsed = parse_codec_rules(rules)
    score = {"prefer": -1, "avoid": 1}

    def rank(t):
        for name, rule in parsed:
            if name in t.codec:
                return score[rule]
        return 0

    return rank


def pick_audio(tracks, prefer, commentary_fallback=False, any_fallback=False, codecs=()):
    """The audio track that should be default.

    First hit in `prefer` wins; among several tracks in the same language the
    one with most channels. Commentary tracks are dropped.

    commentary_fallback  take a commentary track when it is the only one left
    any_fallback         when no language matches, keep the track that is
                         already default if there is exactly one, otherwise
                         take the one with most channels

    The mkv side sets both: the flag has to point somewhere. The Plex side
    sets neither, so an unclear file keeps whatever Plex already chose.
    codecs               codec rules, "truehd:prefer dts:avoid", matched as substrings of the
                         codec name with its punctuation stripped. Within one language
                         a preferred codec wins and an avoided one loses whatever the channel
                         count - the point is to land on something the playback chain
                         can actually decode. An avoided codec is still taken when
                         nothing else is left.
    """
    audio = [t for t in tracks if t.kind == "audio"]
    if not audio:
        return None
    pool = [t for t in audio if not t.commentary]
    if not pool:
        pool = audio if commentary_fallback else []
    if not pool:
        return None

    rank = _codec_rank(codecs)
    matching = [t for t in pool if t.lang in prefer]
    if matching:
        return sorted(matching, key=lambda t: (prefer.index(t.lang), rank(t), -t.channels, t.order))[0]
    if not any_fallback:
        return None
    already = [t for t in pool if t.default and rank(t) <= 0]
    if len(already) == 1:
        return already[0]
    return sorted(pool, key=lambda t: (rank(t), -t.channels, t.order))[0]


def want_sub_langs(lang, native, same, fallback, mapped=None):
    """Which subtitle languages to look for, given the language being spoken.

    In `native` -> none at all, it is understood without text.
    In `same`   -> text in that same language.
    In `mapped` -> text in the language its row names, then the fallback list.
    Otherwise   -> the fallback list, in order.
    """
    lang = canon(lang)
    if lang in native:
        return []
    if lang in same:
        return [lang]
    want = (mapped or {}).get(lang)
    if want:
        return [want] + [x for x in fallback if x != want]
    return list(fallback)


def sub_plan(spoken, want, native, forced_langs, mode, native_forced=False, never_only=None):
    """The languages to look for and the forced mode, once the forced rule is laid
    over what want_sub_langs() gave.

    Two things can ask for forced subtitles and nothing else: a row in the subtitle
    table that names lang+forced, and the "for languages you understand" answer - a
    language you understand needs no full subtitle, but the forced track that covers
    the few lines spoken in another language is still worth having.
    """
    lang = canon(spoken)
    if never_only is not None:                    # the table says never, but forced lines are still wanted
        return list(never_only), "only"
    if lang in (forced_langs or set()):
        return want, "only"
    if native_forced and not want and lang in (native or []):
        return [lang], "only"
    return want, mode


def sub_type(t):
    """"pgs", "vobsub" or "ass" for a subtitle track that is not plain text, "" for one
    that is. Judged by the codec name; a non-text track whose codec is not recognised
    counts as pgs, so nothing slips past the gate just for being exotic."""
    if t.text:
        return ""
    c = t.codec
    if "vobsub" in c or "dvdsub" in c:
        return "vobsub"
    if "substation" in c or c in ("ass", "ssa"):
        return "ass"
    return "pgs"


def _type_rank(is_type, mode):
    """Rank for a track type (SDH/forced/image): first=-1 (preferred),
    allowed=0, last=1. A track that is not of that type ranks 0."""
    if not is_type:
        return 0
    return -1 if mode in ("first", "only") else (1 if mode == "last" else 0)


def pick_subtitle(tracks, want, sdh="never", forced="last", pgs="never", vobsub="never", ass="never"):
    """The subtitle track that should be default, or None.

    Ranked, most important first:
      1. track type - SDH, forced, PGS, VobSub and ASS/SSA are ranked by their settings under
         Subtitles -> Subtitle types, and that comes before language, so a plain
         text track in your second language beats a forced track in your first
      2. the language order in `want`
      3. an internal track before an external .srt
      4. the order in the file
    """
    if not want:
        return None
    candidates = []
    for t in tracks:
        if t.kind != "subtitles":
            continue
        stype = sub_type(t)
        mode = {"pgs": pgs, "vobsub": vobsub, "ass": ass}.get(stype, "allowed")
        if stype and mode == "never":
            continue
        if t.sdh and sdh == "never":
            continue
        if t.forced and forced == "never":
            continue
        if forced == "only" and not t.forced:      # the row asks for forced subtitles only
            continue
        if t.lang not in want:
            continue
        candidates.append((_type_rank(t.sdh, sdh),
                           _type_rank(t.forced, forced),
                           _type_rank(bool(stype), mode),
                           want.index(t.lang),
                           1 if t.external else 0,
                           t.order, t))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[:6])
    return candidates[0][6]


# ---------- the mkv side ----------

def tracks_from_mkvmerge(data, sub_codecs="SubRip|SRT"):
    """mkvmerge -J output -> Tracks."""
    out = []
    for i, t in enumerate(data.get("tracks") or []):
        p = t.get("properties") or {}
        kind = t.get("type")
        if kind not in ("audio", "subtitles"):
            continue
        title = (p.get("track_name") or "").lower()
        codec = t.get("codec") or ""
        out.append(Track(
            kind=kind,
            ref=p.get("number"),
            lang=p.get("language") or "und",
            order=i,
            channels=p.get("audio_channels") or 0,
            commentary=is_commentary(title),
            codec=codec,
            text=bool(re.search(sub_codecs, codec, re.I)),
            sdh=bool(p.get("flag_hearing_impaired")) or bool(re.search(r"sdh|hearing", title)),
            forced=bool(p.get("forced_track")),
            default=bool(p.get("default_track")),
        ))
    return out


def has_skip(rules):
    """True when a subtitle table carries the reserved "skip" row: hands off."""
    return any(bit.partition(":")[0] == SKIP for bit in str(rules).split())


def sub_target(bit):
    """The right-hand side of a row: ("swe", True) for "swe+forced", ("swe", False) for "swe"."""
    b = str(bit).strip()
    if b.endswith("+forced"):
        return b[:-7], True
    return b, False


def sub_forced(rules):
    """Spoken languages whose row asks for forced subtitles only."""
    out = set()
    for bit in str(rules).split():
        a, _, raw = bit.partition(":")
        code, only = sub_target(raw)
        if a and only and len(code) == 3 and code.isalpha():
            out.add(canon(a))
    return out


def subs_never(rules):
    """True when the whole table says: never any subtitle."""
    return any(b.partition(":")[0] == "never" for b in str(rules).split())


def sub_map(rules):
    """"dan:swe fre:eng" rows of a subtitle table -> {"dan": "swe", "fre": "eng"}: which
    language to read for a given spoken one. Empty when the table follows the audio."""
    bits = [b.partition(":") for b in str(rules).split()]
    if any(a == "follow" for a, _, _ in bits):
        return {}
    out = {}
    for a, _, raw in bits:
        b = sub_target(raw)[0]
        if a and a not in (SKIP, "follow", "never", ORIGINAL) and len(b) == 3 and b.isalpha() \
                and b not in ("none", "same"):
            out[canon(a)] = canon(b)
    return out


def env_settings():
    return {
        "audio_priority": canon_list(os.environ.get("AUDIO_PRIORITY", "eng swe")),
        "native": canon_list(os.environ.get("NATIVE_LANGS", "swe")),
        "same": canon_list(os.environ.get("SAME_SUB_LANGS", "eng")),
        "fallback": canon_list(os.environ.get("SUB_LANGS", "swe eng")),
        "sdh": os.environ.get("SUB_SDH", "never"),
        "forced": os.environ.get("SUB_FORCED", "last"),
        "pgs": os.environ.get("SUB_PGS", "never"),
        "vobsub": os.environ.get("SUB_VOBSUB", "never"),
        "ass": os.environ.get("SUB_ASS", "never"),
        "codecs": os.environ.get("SUB_CODECS", "SubRip|SRT"),
        "codec_rules": os.environ.get("AUDIO_CODEC_RULES", "").replace(",", " ").split(),
        "subs_off": has_skip(os.environ.get("SUB_RULES", "")),
        "sub_map": sub_map(os.environ.get("SUB_RULES", "")),
        "sub_forced": sub_forced(os.environ.get("SUB_RULES", "")),
        "subs_never": subs_never(os.environ.get("SUB_RULES", "")),
        "forced_native": os.environ.get("SUB_FORCED_MODE", "nofull") == "native",
    }


_TAG_MAP = {"path": "", "mtime": 0.0, "data": {}}


def _tag_map():
    """folder -> tags, written by ui.py before a run. Re-read when the file changes."""
    p = os.environ.get("FLAGGARR_TAG_MAP", "")
    if not p:
        return {}
    try:
        m = os.stat(p).st_mtime
    except OSError:
        return {}
    if _TAG_MAP["path"] != p or _TAG_MAP["mtime"] != m:
        try:
            with open(p, encoding="utf-8") as f:
                _TAG_MAP.update({"path": p, "mtime": m, "data": json.load(f)})
        except (OSError, ValueError):
            return {}
    return _TAG_MAP["data"]


def tags_for(path):
    """The Radarr/Sonarr tags on the title this file belongs to.

    The map is keyed on the folder the arr knows the title by, so every directory on
    the way up is tried: the movie folder, and the series folder above a season folder.
    Keying on the folder name rather than the whole path means the container may see
    the library under a different mount point than Radarr and Sonarr do."""
    m = _tag_map()
    if not m or not path:
        return []
    p = os.path.dirname(str(path))
    while p and p not in ("/", "."):
        hit = m.get(os.path.basename(p))
        if hit:
            return hit
        p = os.path.dirname(p)
    return []


def alt_tag_hit(path):
    """True when this file carries the tag the alternative rule is bound to."""
    tag = os.environ.get("ALT_TAG", "").strip()
    return bool(tag) and tag in tags_for(path)


def settings_for(path):
    """The settings, with the tag rule laid over them for a title carrying the tag.
    Only the audio order and the subtitle table change - the fallback list is shared."""
    s = env_settings()
    s["audio_map"] = language_map("AUDIO_MAP")
    if alt_tag_hit(path):
        s["audio_priority"] = canon_list(os.environ.get("ALT_AUDIO_PRIORITY", ""))
        s["native"] = canon_list(os.environ.get("ALT_NATIVE_LANGS", ""))
        s["same"] = canon_list(os.environ.get("ALT_SAME_SUB_LANGS", ""))
        s["audio_map"] = language_map("ALT_AUDIO_MAP")
        s["subs_off"] = has_skip(os.environ.get("ALT_SUB_RULES", ""))
        s["sub_map"] = sub_map(os.environ.get("ALT_SUB_RULES", ""))
        s["sub_forced"] = sub_forced(os.environ.get("ALT_SUB_RULES", ""))
        s["subs_never"] = subs_never(os.environ.get("ALT_SUB_RULES", ""))
    return s


ORIGINAL = "orig"
SKIP = "skip"        # reserved, not a language: a row that is not ours to touch,
                     # or, first in an audio order, a whole set that is not          # the row in the language list that stands for the
                           # title's own language, whatever that turns out to be


def language_map(name):
    """"jpn:swe fre:swe" -> {"jpn": "swe", "fre": "swe"}: which language to hear
    instead of a given original one."""
    out = {}
    for bit in os.environ.get(name, "").split():
        a, _, b = bit.partition(":")
        if a and b and b != "none":   # a row set to None is no row at all
            out[canon(a)] = canon(b)
    return out


def leave_alone(prio, orig, per_language=None):
    """True when the per-language table says this title is not ours to touch.

    Only the third audio shape can say it (the placeholder has to be out of the
    list), and only for an original language that has no row of its own. The
    callers write nothing at all then: no default flag, no Plex selection.
    """
    if ORIGINAL in list(prio):
        return False
    table = per_language or {}
    return not table.get(orig) and table.get("other") == canon(SKIP)


def expand_original(prio, orig, per_language=None):
    """The language order, with the ORIGINAL placeholder standing for the title's own
    language wherever it sits: first means prefer the original, last means prefer a
    dub and keep the original as the fallback.

    With no placeholder in the list at all, the per-language table decides: an
    original language named there is heard in the language it points to, and the
    list is the fallback, then the original. An original that is not named follows the table's own
    "other" row - the original language first, or the list and then the original. That row
    can also say SKIP, which means the title is left alone entirely; see
    leave_alone(), which the callers check before they get here.
    """
    codes = list(prio)
    if ORIGINAL not in codes:
        table = per_language or {}
        want = table.get(orig)
        if not want and (table.get("other") or ORIGINAL) == ORIGINAL:
            want = ORIGINAL          # no row of its own, and the table says keep the original
        if want:
            codes = [want] + codes
        codes = codes + [ORIGINAL]   # and the title's own language when none of the others is in the file
    out = []
    for c in codes:
        c = orig if c == ORIGINAL else c
        if c and c not in out:
            out.append(c)
    return out


def mkv_decision(data, original="", do_audio=True, do_subs=True):
    """Everything auto_set_mkv_defaultflags.sh needs, from mkvmerge JSON.

    Returns (audio_number, audio_lang, sub_note, mkvpropedit arguments).
    """
    s = settings_for((data.get("file_name") if isinstance(data, dict) else "") or "")
    if SKIP in s["audio_priority"]:
        do_audio = False                 # Let Plex decide: no audio flag from us
    if s.get("subs_off"):
        do_subs = False
    if not do_audio and not do_subs:
        return "", "und", "left to Plex - no flags from us", "", ""
    if leave_alone(s["audio_priority"], to_code(original), s.get("audio_map")):
        # No arguments at all, so the shell script writes nothing and logs the note.
        return "", "und", "left untouched - no row for this original language", "", ""
    tracks = tracks_from_mkvmerge(data, s["codecs"])

    prefer = expand_original(s["audio_priority"], to_code(original), s.get("audio_map"))

    audio = pick_audio(tracks, prefer, commentary_fallback=True, any_fallback=True,
                       codecs=s["codec_rules"])
    audio_lang = audio.lang if audio else "und"
    audio_num = audio.ref if audio else ""
    if not do_audio:
        # Our pick is not being written, so the subtitles must follow the track
        # that is already default in the file - that is what will play.
        cur = ([t for t in tracks if t.kind == "audio" and t.default]
               or [t for t in tracks if t.kind == "audio"] or [None])[0]
        audio, audio_num = cur, ""
        audio_lang = cur.lang if cur else "und"

    want = [] if s.get("subs_never") else want_sub_langs(
        audio_lang, s["native"], s["same"], s["fallback"], s.get("sub_map"))
    want, forced_mode = sub_plan(audio_lang, want, s["native"], s.get("sub_forced"), s["forced"],
                                 s.get("forced_native") and not s.get("subs_never"),
                                 s["fallback"] if s.get("subs_never") and s["forced"] != "never" else None)
    sub = pick_subtitle(tracks, want, s["sdh"], forced_mode, s["pgs"], s["vobsub"], s["ass"])

    if not do_subs:
        note = "subtitles left to Plex"
    elif not want:
        note = "native-language speech - no subtitle as default"
    elif forced_mode == "only":
        note = "forced text only: %s (if a suitable track exists)" % " ".join(want)
    else:
        note = "text: %s (if a suitable track exists)" % " ".join(want)

    args, undo = [], []
    if do_audio:
        for t in tracks:
            if t.kind == "audio":
                args.append("--edit track:@%s --set flag-default=%s"
                            % (t.ref, 1 if audio and t.ref == audio.ref else 0))
                undo.append("--edit track:@%s --set flag-default=%s" % (t.ref, 1 if t.default else 0))
    if do_subs:
        for t in tracks:
            if t.kind == "subtitles":
                args.append("--edit track:@%s --set flag-default=%s"
                            % (t.ref, 1 if sub and t.ref == sub.ref else 0))
                undo.append("--edit track:@%s --set flag-default=%s" % (t.ref, 1 if t.default else 0))
    # The undo line describes the flags as they are right now, so writing it back
    # puts the file exactly where it was before this run (see undo in ui.py).
    return audio_num, audio_lang, note, " ".join(args), " ".join(undo)


def main():
    original, do_audio, do_subs = "", True, True
    argv = sys.argv[1:]
    while argv:
        a = argv.pop(0)
        if a == "--original" and argv:
            original = argv.pop(0)
        elif a == "--audio-only":
            do_subs = False
        elif a == "--subs-only":
            do_audio = False
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 1
    num, lang, note, args, undo = mkv_decision(data, original, do_audio, do_subs)
    print(num)
    print(lang)
    print(note)
    print(args)
    print(undo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
