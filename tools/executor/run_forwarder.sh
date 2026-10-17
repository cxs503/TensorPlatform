#!/bin/bash
# Keep-alive wrapper for the S3 forwarder (crontab @reboot + */5).
# flock guarantees a single instance; cron invocations exit 0 while alive.
LOCK=/srv/tensor/shared/tensor_eco/s3-forwarder.lock
exec 9>"$LOCK"
flock -n 9 || exit 0
cd "$(dirname "$0")"
exec /usr/bin/python3 forwarder.py
