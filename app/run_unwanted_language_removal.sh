#!/bin/sh
# Run from the Run page, job "Delete unwanted subs". Argument: dry | apply | delete
# Output goes to flaggarr/log/manual_unwanted_language_removal.log - open it in
# File Station's text editor to see what happened.
export LC_ALL=en_US.UTF-8
PATH="/usr/local/bin:/opt/bin:$PATH"; export PATH

APP_DIR="/app"          # the program, from the image
CONFIG_DIR="/config"    # this installation: settings, logs
LOGFILE="$CONFIG_DIR/log/manual_unwanted_language_removal.log"
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
  python3 "$APP_DIR/manual_unwanted_language_removal.py" --lang "$(echo "${UNWANTED_LANGS:-en}" | tr ' ' ',')" --tags "$(echo "${NATIVE_TAG}" | tr ' ' ',')" --root "${MEDIA_ROOT:-/volume1/PlexMedia}" --quarantine "${QUARANTINE:-/volume1/PlexMedia/Removed Subs}/${QUARANTINE_LANGS:-flaggarr_unwanted_language}" $EXTRA
  echo
} >> "$LOGFILE" 2>&1
