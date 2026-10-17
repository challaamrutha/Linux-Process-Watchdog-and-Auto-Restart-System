#!/bin/sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }
systemctl disable --now watchdog || true
rm -f /etc/systemd/system/watchdog.service
systemctl daemon-reload
rm -rf /opt/watchdog
echo "Removed."
