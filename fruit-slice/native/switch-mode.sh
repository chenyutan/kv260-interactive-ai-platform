#!/bin/sh
set -eu
case "${1:-}" in
 fruit)
  sudo -n systemctl stop bodyink-live.service
  if ! sudo -n systemctl start fruit-pose.service; then
   sudo -n systemctl start bodyink-live.service
   exit 1
  fi
  ;;
 ink)
  sudo -n systemctl stop fruit-pose.service
  sudo -n systemctl start bodyink-live.service
  ;;
 *) echo 'Usage: sh switch-mode.sh fruit|ink' >&2; exit 2;;
esac
