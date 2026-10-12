#!/bin/sh
# Starts as root, then drops to PUID:PGID. Without them it stays root.
set -e
PUID="${PUID:-0}"; PGID="${PGID:-0}"
if [ "$PUID" != "0" ]; then
  getent group "$PGID" >/dev/null 2>&1 || groupadd -g "$PGID" flaggarr
  getent passwd "$PUID" >/dev/null 2>&1 || useradd -u "$PUID" -g "$PGID" -M -d /config -s /usr/sbin/nologin flaggarr
  chown -R "$PUID:$PGID" /config 2>/dev/null || true   # settings, logs, backups: created by earlier root runs
  exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"
