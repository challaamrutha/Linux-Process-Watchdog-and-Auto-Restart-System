#!/bin/sh
# Installs the watchdog as a systemd service. Run from the project folder: sudo sh systemd/install.sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)"; exit 1; }
id watchdog >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin watchdog
mkdir -p /opt/watchdog
cp -r watchdog.py config demo web /opt/watchdog/
mkdir -p /opt/watchdog/logs /opt/watchdog/state
chown -R watchdog:watchdog /opt/watchdog
cp systemd/watchdog.service /etc/systemd/system/watchdog.service
systemctl daemon-reload
systemctl enable --now watchdog
echo "Installed. Check with: systemctl status watchdog   and   journalctl -u watchdog -f"
