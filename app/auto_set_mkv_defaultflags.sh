#!/bin/sh
export LC_ALL=en_US.UTF-8
# auto_set_mkv_defaultflags.sh - sets default flags on audio and subtitle tracks in mkv files.
#
# mkvpropedit can only write Matroska. Mp4/m4v carry their subtitles as
# mov_text and are never touched here - auto_set_plex_defaults.py sets the
# default for those.
#
# Two things happen:
#
#   AUDIO     Clears default on every audio track and sets it on exactly one,
#             picked by the priority list below. Even if the file arrives with
#             several default flags, the result is always unambiguous.
#
#   SUBTITLE  Follows from whichever audio track was picked:
#               English speech             -> English text-based subtitle
#               original in NATIVE_LANGS   -> no subtitle default at all
#               original in SAME_SUB_LANGS -> subtitle in the same language
#               other original             -> first hit in SUB_LANGS
#
# Only SubRip/SRT can become default (see SUB_CODECS). ASS/SSA does not count
# as a subtitle - it is usually a signs track. PGS and VobSub are images and
# are cleared. VobSub is always cleared. The forced flag is left alone.
#
# No content is rewritten - mkvpropedit only changes the file header. Reversible.
#
# Called with a file path, or as a Custom Script from Sonarr/Radarr
# (the path is then taken from their environment variables).
#
#   sh auto_set_mkv_defaultflags.sh --dry-run "/path/to/movie.mkv"      # show only
#   sh auto_set_mkv_defaultflags.sh --dry-run --verbose "/path/..."     # + the command
#   sh auto_set_mkv_defaultflags.sh "/path/to/movie.mkv"                # do it
#
# Needs: mkvtoolnix (mkvmerge, mkvpropedit) and python3.

# Audio tracks in priority order. The first hit becomes default.
#
# When the script knows the film's original language it is put first in the
# list automatically, so a Swedish film with both Swedish and English audio
# gets Swedish. The original language comes from --original, or from
# Radarr/Sonarr's environment variables when the script runs as a Custom
# Script. Without it the list below applies as-is.
#
# Note: Plex goes primarily by your preferred audio language and only cares
# about the default flag when that language is missing from the file. Other
# players (VLC, Infuse, TV apps) follow the flag directly.
AUDIO_PRIORITY="${AUDIO_PRIORITY:-eng swe}"

# Subtitle languages for foreign-language films, in priority order.
NATIVE_LANGS="${NATIVE_LANGS-swe}"                      # understood without text - "-" not ":-": an empty list is a real answer
SAME_SUB_LANGS="${SAME_SUB_LANGS-eng}"                 # text in the same language as the original
SUB_LANGS="${SUB_LANGS:-swe eng}"    # otherwise, in order

# Which subtitle codecs may become default. SRT only - ASS/SSA is text-based,
# but carries styling and its own fonts that many Plex clients (Chromecast,
# Roku, some smart TVs) cannot render. The text is then burned in, which forces
# transcoding. PGS and VobSub are bitmaps and are always cleared.
# To allow ASS anyway: add |SubStationAlpha below.
SUB_CODECS="SubRip|SRT"
SUB_SDH="${SUB_SDH:-never}"          # never | last | allowed | first   (UI: Subtitles -> Subtitle types)
SUB_FORCED="${SUB_FORCED:-last}"    # never | last | allowed | first
SUB_PGS="${SUB_PGS:-never}"         # never | last | allowed | first  (Blu-ray picture subs)
SUB_VOBSUB="${SUB_VOBSUB:-never}"   # never | last | allowed | first  (DVD picture subs)
SUB_ASS="${SUB_ASS:-never}"         # never | last | allowed | first  (ASS/SSA styled subs)
# PRIORITY RULE: a full subtitle beats a forced one, even across the language
# boundary - a full English subtitle wins over a Swedish forced track. Within
# the same rank the SUB_LANGS order applies. The flags on the tracks themselves
# are left untouched.

# Synology: MKVToolNix lives in /usr/local/bin, which is not always in PATH
# for the service user (sc-sonarr / sc-radarr).
PATH="/usr/local/bin:/opt/bin:$PATH"
export PATH

log() { echo "[defaultflags] $*"; }

dry=0
verbose=0
original=""
do_audio="${MKV_FLAGS_AUDIO:-1}"
do_subs="${MKV_FLAGS_SUBS:-1}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|-n)  dry=1; shift ;;
    --verbose)     verbose=1; shift ;;
    --audio-only)  do_subs=0; shift ;;
    --subs-only)   do_audio=0; shift ;;
    --original)    original="$2"; shift 2 ;;
    --original=*)  original="${1#--original=}"; shift ;;
    *) break ;;
  esac
done

# Original language from Radarr/Sonarr when the script runs as a Custom Script.
[ -z "$original" ] && original="$radarr_movie_originallanguage"
[ -z "$original" ] && original="$sonarr_series_originallanguage"

# The rules module lives next to this script.
RULES="$(dirname "$0")/track_rules.py"
[ -f "$RULES" ] || RULES="/app/track_rules.py"

only=""
[ "$do_subs" = "0" ] && only="--audio-only"
[ "$do_audio" = "0" ] && only="--subs-only"

file="$1"
[ -z "$file" ] && file="$sonarr_episodefile_path"
[ -z "$file" ] && file="$radarr_moviefile_path"

# Sonarr/Radarr also run the script on Test - then there is no file.
if [ -z "$file" ]; then
  log "No file path given (probably a test call). Exiting without action."
  exit 0
fi

if [ ! -f "$file" ]; then
  log "ERROR: cannot find the file: $file"
  exit 1
fi

case "$file" in
  *.mkv|*.MKV) ;;
  *) log "Not a .mkv, skipping: $(basename "$file")"; exit 0 ;;
esac

for bin in mkvmerge mkvpropedit python3; do
  command -v "$bin" >/dev/null 2>&1 || { log "ERROR: $bin is missing from PATH."; exit 1; }
done

json=$(mkvmerge --command-line-charset UTF-8 --output-charset UTF-8 -J "$file" 2>/dev/null) || { log "ERROR: mkvmerge could not read $file"; exit 1; }

# --- the decision ----------------------------------------------------------
# Which audio track and which subtitle track is decided by track_rules.py, the
# same module auto_set_plex_defaults.py uses against Plex, so the flag inside
# the file and the choice saved in Plex can never drift apart. It reads the
# mkvmerge JSON on stdin and answers with four lines:
#   track number of the audio track that becomes default (empty if none)
#   its language
#   a one-line note about the subtitles, for the log
#   the arguments for mkvpropedit
picked=$(printf '%s' "$json" | python3 "$RULES" --original "$original" $only) || {
  log "ERROR: track_rules.py could not read $(basename "$file")"; exit 1; }

audio_num=$(printf '%s\n' "$picked" | sed -n 1p)
audio_lang=$(printf '%s\n' "$picked" | sed -n 2p)
sub_note=$(printf '%s\n' "$picked" | sed -n 3p)
args=$(printf '%s\n' "$picked" | sed -n 4p)
undo_args=$(printf '%s\n' "$picked" | sed -n 5p)

if [ -z "$audio_num" ] && [ "$do_audio" = "1" ]; then
  log "No audio tracks in $(basename "$file")."
fi

if [ -z "$args" ]; then
  case "$sub_note" in
    "left untouched"*) log "SKIP $(basename "$file"): $sub_note" ;;
    *) log "No audio or subtitle tracks to touch in $(basename "$file")." ;;
  esac
  exit 0
fi

if [ "$dry" = "1" ]; then
  log "DRY $(basename "$file")"
  log "     audio $audio_lang (track $audio_num) - $sub_note"
  if [ "$verbose" = "1" ]; then
    log "     command: mkvpropedit <file> $args"
  fi
  exit 0
fi

# shellcheck disable=SC2086
mkv_out=$(mkvpropedit --command-line-charset UTF-8 "$file" $args 2>&1); rc=$?
if [ "$rc" -le 1 ]; then   # mkvpropedit exits 1 when it wrote the file but had something to warn about - that is not an error
  log "OK $(basename "$file"): audio=$audio_lang, $sub_note"
  if [ "$rc" = "1" ]; then
    log "  warning: $(printf '%s\n' "$mkv_out" | grep -i warning | tr '\n' ' ' | cut -c1-300)"
  fi
  # What the flags looked like before, so a real run can be undone from the UI.
  if [ -n "${FLAGGARR_UNDO:-}" ] && [ -n "$undo_args" ] && [ "$undo_args" != "$args" ]; then
    printf '%s\t%s\n' "$file" "$undo_args" >> "$FLAGGARR_UNDO" 2>/dev/null || true
  fi
else
  mkv_msg=$(printf '%s\n' "$mkv_out" | grep -i error | tr '\n' ' ' | cut -c1-400)
  [ -z "$mkv_msg" ] && mkv_msg=$(printf '%s\n' "$mkv_out" | tr '\n' ' ' | cut -c1-400)
  log "ERROR: mkvpropedit: ${mkv_msg:-no output}"
  # The usual two causes read the same in mkvpropedit's message, so say which it was.
  if [ ! -w "$file" ]; then
    log "  cause: no write access for $(id -un) - owner $(stat -c '%U:%G, mode %a' "$file" 2>/dev/null). Give the account write access on the NAS."
  elif [ ! -r "$file" ]; then
    log "  cause: no read access for $(id -un) - owner $(stat -c '%U:%G, mode %a' "$file" 2>/dev/null)."
  else
    log "  cause: the file is readable and writable - damaged, cut short, or held by another program. Check file on the Run page tells more."
  fi
  exit 1
fi
