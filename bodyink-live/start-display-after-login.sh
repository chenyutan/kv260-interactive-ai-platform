#!/bin/sh
set -eu
for attempt in $(seq 1 240); do
 if { [ -e /run/bodyink-session-ready ] || [ -e /run/bodyink-headless-allow ]; } && curl -fsS --max-time 1 -o /dev/null http://127.0.0.1:8088/; then
  exec /home/ubuntu/bodyink-live/start-display.sh
 fi
 sleep 1
done
exit 1
