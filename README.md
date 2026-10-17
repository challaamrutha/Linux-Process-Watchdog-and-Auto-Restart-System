# Linux Process Watchdog and Auto-Restart System

A watchdog daemon for Linux that starts selected processes, detects when they terminate unexpectedly, restarts them according to a policy, and reports everything through logs, alerts, a CLI and a web dashboard. It integrates with systemd so it starts at boot and is itself restarted if it fails.

Pure Python 3 standard library. No pip install, no compilation step.

## Requirements
- Linux (uses `/proc` for CPU and memory), Python 3.8 or newer
- systemd only for the optional service install

## Quick start (about 2 minutes)
```bash
unzip linux-process-watchdog.zip && cd linux-watchdog
python3 watchdog.py validate          # checks config/watchdog.json
python3 watchdog.py run               # starts the daemon, keep this terminal open
```
Open **http://127.0.0.1:8080** in a browser for the dashboard.

In a second terminal:
```bash
python3 watchdog.py status            # table of all processes
python3 watchdog.py stop flaky-service
python3 watchdog.py start flaky-service
python3 watchdog.py restart stable-service
```
Press Ctrl+C in the first terminal to stop. The watchdog stops all processes it started.

## Demo scenarios (for the live demonstration)
| Scenario | What to do | What you should see |
|---|---|---|
| Unexpected crash | `kill -9 <PID of stable-service>` (PID from `status`) | Crash logged, process restarted with a new PID |
| Random crashes | Watch `flaky-service` | It exits after 6-15 s and is restarted with exponential backoff |
| Retry limit | Watch `always-fails` | Restarts 3 times, then status becomes `gaveup` and a critical alert is logged |
| Restart policy | `on-failure` vs `never` vs `always` in the config | Clean exit (code 0) is not restarted under `on-failure` |
| CPU alert | Watch `cpu-burner` | Alert when CPU stays above its 40% limit for 3 checks |
| Memory limit | `python3 watchdog.py start memory-hog` | Killed when above 120 MB, then restarted until retries run out |
| Manual stop | Click Stop on the dashboard | Process stays stopped (not treated as a crash) |
| Persistence | Stop the daemon, run it again | Restart and crash counters continue from `state/state.json` |
| Config reload | Edit `config/watchdog.json`, then `kill -HUP <watchdog PID>` | New processes start without restarting the daemon |

## Configuration (`config/watchdog.json`)
Per process: `name`, `command` (`{python}` means the current Python), `policy` (`always`, `on-failure`, `never`), `max_retries`, `backoff_base` and `backoff_max` (seconds, doubles each retry), `stable_after` (seconds of uptime before the retry counter resets), `cpu_limit` (%), `mem_limit_mb`, `cwd`, `env`, `autostart`.
Global: `check_interval`, `log_dir`, `state_file`, `dashboard.host/port`, `alerts.webhook`, `alerts.email`.

To watch your own program, add an entry such as:
```json
{ "name": "my-app", "command": "/usr/bin/python3 /home/me/app.py", "policy": "always", "max_retries": 5 }
```
Commands are run directly (no shell). To use shell features, write `"command": "sh -c 'your command | here'"`.

## Install as a systemd service
```bash
sudo sh systemd/install.sh
systemctl status watchdog
journalctl -u watchdog -f
sudo systemctl reload watchdog        # reload config
sudo sh systemd/uninstall.sh          # remove
```
The unit uses `Type=notify` with `WatchdogSec=30` (the daemon sends keep-alives, so systemd restarts it if it hangs) and `Restart=always` (restarts it if it crashes).

## Run the tests
```bash
python3 -m unittest -v tests.test_watchdog
```
8 automated tests cover restart after SIGKILL, retry limit, all policies, manual stop, memory limit, persistence and config validation.

## Project layout
```
watchdog.py            daemon, API server and CLI
config/watchdog.json   persistent configuration
web/index.html         dashboard
demo/                  sample services used for testing and the demo
tests/                 automated tests
systemd/               service unit, install and uninstall scripts
docs/REPORT.md         project report
logs/  state/          runtime output (created automatically)
```

## Safety notes
- The dashboard listens on 127.0.0.1 only. Do not expose it publicly: it has no login.
- State-changing API calls require an `X-Requested-With` header, which blocks cross-site requests from web pages.
- Run the demo on your own machine or a VM. The watchdog only touches processes it started itself.
- Output of each managed process goes to `logs/<name>.out`; the watchdog's own log is `logs/watchdog.log` (rotated).
