#!/usr/bin/env python3
# settings_env.py - prints /config/settings.json as shell export lines, so the
# .sh scripts can read them:  eval "$(python3 /app/settings_env.py)"
# Gives the language order (AUDIO_PRIORITY ...), the per-language rules
# (SUB_RULES, AUDIO_MAP) and the alternative set behind a tag, paths and tags
# (MEDIA_ROOT, MEDIA_ROOTS, QUARANTINE, NATIVE_TAG, UNWANTED_LANGS) and the
# connections (PLEX_URL, PLEX_TOKEN, RADARR_URL, RADARR_APIKEY ...). The one
# thing only the page can supply is FLAGGARR_TAG_MAP, the folder-to-tag file,
# because it has to be written out first.
# A missing file or key falls back to the defaults below - the same values the
# scripts used to have hard-coded.
import json
import os
import shlex

LANGUAGES = [("english", "eng"), ("swedish", "swe"), ("norwegian", "nor"), ("danish", "dan"),
         ("finnish", "fin"), ("icelandic", "ice"), ("german", "ger"), ("french", "fre"),
         ("spanish", "spa"), ("italian", "ita"), ("dutch", "dut"), ("portuguese", "por"),
         ("polish", "pol"), ("russian", "rus"), ("ukrainian", "ukr"), ("japanese", "jpn"),
         ("korean", "kor"), ("chinese", "chi"), ("hindi", "hin")]
NAMES = {k: n for n, k in LANGUAGES}
DEFAULTS = {
    "native_langs": "eng",
    "same_sub_langs": "",
    "sub_langs": "eng",
    "audio_priority": "orig eng",
    "media_root": "/volume1/PlexMedia",
    "quarantine": "/volume1/PlexMedia/Removed Subs",
    "trash_categories": "orphaned",
    "quarantine_trash": "flaggarr_trashfiles",
    "quarantine_langs": "flaggarr_unwanted_language",
    "native_tag": "",
    "unwanted_langs": "eng",
    "sub_sdh": "never",
    "sub_forced": "last",
    "sub_forced_mode": "nofull",
    "sub_pgs": "never",
    "sub_vobsub": "never",
    "sub_ass": "never",
    "audio_codec_rules": "",
    "sub_track_order": "text forced",
    "sub_rules": "eng:none",
    "audio_map": "",
    "movies_root": "",
    "series_root": "",
    "alt_tag": "",
    "alt_audio_priority": "eng orig",
    "alt_sub_rules": "eng:none",
    "alt_audio_map": "",
}
BLANK_OK = ("native_langs", "same_sub_langs", "audio_map", "audio_codec_rules",
            "movies_root", "series_root", "alt_tag", "alt_audio_map", "native_tag")
ISO1 = {"eng": "en", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi", "ice": "is", "ger": "de", "fre": "fr",
        "spa": "es", "ita": "it", "dut": "nl", "por": "pt", "pol": "pl", "rus": "ru", "ukr": "uk", "jpn": "ja",
        "kor": "ko", "chi": "zh", "hin": "hi"}
SETTINGS = os.environ.get("FLAGGARR_CONFIG_DIR", "/config") + "/settings.json"
try:
    with open(SETTINGS) as f:
        d = json.load(f)
except (OSError, ValueError):
    d = {}
for k, std in DEFAULTS.items():
    v = str(d.get(k) or std).strip() or std
    if k in BLANK_OK and k in d:        # for these an empty value is a real answer, not a missing one
        v = " ".join(str(d[k]).split())
    if k == "unwanted_langs":                                        # the cleanup script wants 639-1 (en, sv ...)
        v = " ".join(ISO1.get(x, x) for x in v.split())
    print("export %s=%s" % (k.upper(), shlex.quote(v)))
    if k in ("native_langs", "same_sub_langs", "sub_langs"):       # the Plex script wants names
        print("export %s_NAMES=%s" % (k.upper(), shlex.quote(" ".join(NAMES.get(x, x) for x in v.split()))))
for _bk in ("mkv_flags_audio", "mkv_flags_subs"):
    print("export %s=%s" % (_bk.upper(), "0" if d.get(_bk) is False else "1"))
if d.get("users_no_image_subs"):                                  # the switch applies to everyone
    print("export SUB_PGS=never")
    print("export SUB_VOBSUB=never")
# The alternative set's derived pair, written by the page when alt_sub_rules is saved.
for _ak in ("alt_native_langs", "alt_same_sub_langs"):
    _av = " ".join(str(d.get(_ak) or "").split())
    print("export %s=%s" % (_ak.upper(), shlex.quote(_av)))
    print("export %s_NAMES=%s" % (_ak.upper(),
          shlex.quote(" ".join(NAMES.get(x, x) for x in _av.split()))))
for _tk in ("alt_tag_radarr", "alt_tag_sonarr"):
    print("export %s=%s" % (_tk.upper(), "0" if d.get(_tk) is False else "1"))

# The library roots the .sh scripts loop over. Both set means two trees; one or
# neither falls back to the single media root, as the page does.
_roots = [str(d.get(k) or "").strip().rstrip("/") for k in ("movies_root", "series_root")]
_roots = [r for r in _roots if r]
if not _roots:
    _roots = [str(d.get("media_root") or DEFAULTS["media_root"]).strip().rstrip("/")]
print("export MEDIA_ROOTS=%s" % shlex.quote(":".join(_roots)))

for k, v in (d.get("connections") or {}).items():
    if k.isidentifier() and str(v).strip():
        print("export %s=%s" % (k, shlex.quote(str(v).strip())))
