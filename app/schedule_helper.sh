#!/bin/sh
# schedule_helper.sh - the safety net. Runs once a week.
#
# Sets default flags on the mkv files Radarr/Sonarr know about. On import that
# is handled by webhook_listener.py directly; this run walks the whole library
# and catches what was missed: failures during import, or a language corrected
# after the fact.
#
# Saved Plex audio and subtitle selections are not touched from here.

APP_DIR="/app"          # the program, from the image
CONFIG_DIR="/config"    # this installation: settings, logs

eval "$(python3 /app/settings_env.py)"   # language order from the UI

PATH="/usr/local/bin:/opt/bin:$PATH"
export PATH

log() { echo "[scheduled] $*"; }

log "start $(date '+%Y-%m-%d %H:%M')"

python3 "$APP_DIR/auto_mkv_defaultflags_batch.py" --apply || log "mkv flags: failed"

log "done"
