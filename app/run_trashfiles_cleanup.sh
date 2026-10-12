#!/bin/sh
# Run from the Run page, job "Clean trash files". Argument: dry | apply | delete
# Output goes to flaggarr/log/manual_trashfiles_cleanup.log - open it in File
# Station's text editor to see what happened.
export LC_ALL=en_US.UTF-8
PATH="/usr/local/bin:/opt/bin:$PATH"; export PATH

APP_DIR="/app"          # the program, from the image
CONFIG_DIR="/config"    # this installation: settings, logs
LOGFILE="$CONFIG_DIR/log/manual_trashfiles_cleanup.log"
eval "$(python3 /app/settings_env.py)"   # languages, paths, tags and keys from the UI

case "$1" in
  apply)  EXTRA="--apply" ;;
  delete) EXTRA="--apply --permanent" ;;
  *)     EXTRA="" ;;
esac

{
  echo "==================================================================="
  echo "$(date '+%Y-%m-%d %H:%M:%S')  mode: ${1:-dry}"
  echo "==================================================================="
  # One library path per line: Radarr's and Sonarr's when they are set, otherwise the single Media root.
  echo "${MEDIA_ROOTS:-${MEDIA_ROOT:-/volume1/PlexMedia}}" | tr ':' '\n' | while read -r ROOT; do
    [ -n "$ROOT" ] || continue
    echo "root: $ROOT"
    python3 "$APP_DIR/manual_trashfiles_cleanup.py" --category "${TRASH_CATEGORIES:-orphaned}" --root "$ROOT" --quarantine "${QUARANTINE:-/volume1/PlexMedia/Removed Subs}/${QUARANTINE_TRASH:-flaggarr_trashfiles}" $EXTRA
  done
  echo
} >> "$LOGFILE" 2>&1
