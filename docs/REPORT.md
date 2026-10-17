# Project Report: Linux Process Watchdog and Auto-Restart System

**Team members and roles:** 
**Team members and roles**

| Member | Role | Parts of the project | Should be able to explain |
|---|---|---|---|
| Amrutha | Core supervisor and systemd | Process launching, crash detection, restart policies, exponential backoff, retry limit (`Watchdog`, `_spawn`, `_exited`, `tick`), systemd service files and install script | How a crash is detected, how the restart decision is made, why backoff is used, how systemd restarts the watchdog |
| Tanvi | Monitoring, alerts and storage | CPU and memory sampling from `/proc` (`_sample`, `_health`), alerts by webhook and email, logging, config and state files, automated tests | How CPU % and memory are calculated, when alerts fire, how settings and counters are saved, what each test checks |
| Tanvitha | Interfaces and documentation | Web dashboard (`web/index.html`), REST API, CLI commands, demo programs, README, report and PPT | How the dashboard gets live data from the API, how the CLI talks to the daemon, how to run the demo scenarios |

## 1. Problem statement
Services on Linux sometimes die because of bugs, memory exhaustion or accidental kills. Without supervision, they stay down until someone notices. The goal is a system that monitors chosen processes, restarts them automatically under controlled rules, records what happened, alerts people, and starts itself at boot.

## 2. Objectives
Detect unexpected termination, restart automatically, support restart policies and retry limits, log events, send alerts, track health and resource use, provide a CLI and web dashboard, store configuration persistently, integrate with systemd.

## 3. System design
**Components**
1. *Supervisor core* (`Watchdog`, `Managed` in `watchdog.py`): launches each process in its own session, polls it every `check_interval` seconds, reaps exits.
2. *Policy engine* (`_exited`): decides, from the exit code and the process's policy, whether to restart, wait, or give up.
3. *Health monitor* (`_sample`, `_health`): reads `/proc/<pid>/stat` and `/proc/<pid>/statm` for CPU and memory.
4. *Alerting* (`alert`): log, webhook (HTTP POST) and optional SMTP email.
5. *Interfaces*: REST API and dashboard (HTTP server thread), CLI (`status/start/stop/restart`).
6. *Persistence*: JSON config, JSON state file (restart and crash counters), rotating log.
7. *systemd integration*: `Type=notify` unit, `WatchdogSec`, `sd_notify` keep-alives.

**Restart logic.** On unexpected exit: if policy is `never`, or `on-failure` with exit code 0, do not restart. Otherwise, if consecutive failures have reached `max_retries`, mark `gaveup` and send a critical alert. Else wait `min(backoff_max, backoff_base * 2^(n-1))` seconds and restart. If a process stays up for `stable_after` seconds, the consecutive-failure counter resets. Backoff prevents a crash loop from consuming the CPU.

**Manual stops** are flagged so the watchdog never mistakes an operator's stop for a crash.

## 4. Technologies
Python 3 standard library (`subprocess`, `threading`, `http.server`, `json`, `logging`, `smtplib`), Linux `/proc` filesystem, POSIX signals and process groups, systemd, HTML/CSS/JavaScript for the dashboard.

## 5. Implementation notes
- Each child starts with `start_new_session=True`, so the whole process group can be signalled (SIGTERM, then SIGKILL after 5 s).
- CPU % = change in (utime + stime) ticks divided by elapsed time and clock ticks per second.
- Memory = resident set size from `statm` times the page size.
- A single re-entrant lock protects state shared by the monitor loop and the API thread.
- State is written to a temporary file and renamed, so a crash cannot leave a half-written file.

## 6. Testing and validation
Automated: `python3 -m unittest -v tests.test_watchdog`.

| # | Test | Input | Expected result | Observed |
|---|---|---|---|---|
| 1 | Restart after SIGKILL | `kill -9` on a running service | New PID, restarts = 1 | Pass |
| 2 | Retry limit | Process always exits with code 3, max 2 retries | 2 restarts then `gaveup` | Pass |
| 3 | Policy `never` | Process exits with 1 | Stays stopped | Pass |
| 4 | Policy `on-failure` | Process exits with 0 | Not restarted | Pass |
| 5 | Manual stop | `control(stop)` | Not restarted | Pass |
| 6 | Memory limit | 80 MB allocation, 30 MB limit | Killed, alert raised | Pass |
| 7 | Persistence | New watchdog instance | Counters restored | Pass |
| 8 | Config validation | Missing command, bad policy | `ValueError` | Pass |

Manual: CLI and dashboard checked against the demo services (see README, demo scenarios); SIGTERM to the daemon stops every child with none left behind. Add your own screenshots here.

## 7. Results
All automated tests pass. Crashes are detected within one check interval (default 1 s). Restart delays follow the exponential schedule. (Add screenshots of the dashboard and log.)

## 8. Impact
Reduces downtime of small services, removes the need for manual checking, keeps an audit trail of failures, and shows how supervisors like systemd and supervisord work internally.

## 9. Limitations and future work
- Supervises only processes it launches itself (no attaching to arbitrary existing PIDs).
- Dashboard has no authentication (localhost only).
- Future: attach by PID or pidfile, HTTP/TCP health probes, per-process dependencies, authentication, Prometheus metrics.

## 10. Originality and responsible use
Written from scratch using only the Python standard library. It supervises only its own child processes, binds to localhost, and the demo programs are harmless and stop with the daemon. (List any references you used here.)
