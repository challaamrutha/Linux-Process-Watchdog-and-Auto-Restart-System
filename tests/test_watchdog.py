"""Automated tests. Run from the project folder:  python3 -m unittest -v tests.test_watchdog"""
import os, signal, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import watchdog as W

SLEEP = '{python} -c "import time; time.sleep(60)"'


def make(tmp, procs):
    for p in procs:
        p.setdefault("backoff_base", 0.05); p.setdefault("backoff_max", 0.2)
    return W.Watchdog({"log_dir": tmp, "state_file": os.path.join(tmp, "state.json"), "processes": procs})


def spin(wd, cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        wd.tick()
        if cond():
            return True
        time.sleep(0.05)
    return False


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.wds = []

    def tearDown(self):
        for wd in self.wds:
            wd.shutdown()

    def mk(self, procs):
        wd = make(self.tmp, procs); self.wds.append(wd); return wd

    def test_restart_after_sigkill(self):
        wd = self.mk([{"name": "svc", "command": SLEEP}]); wd.start_all()
        m = wd.procs["svc"]; old = m.pid
        os.kill(old, signal.SIGKILL)
        self.assertTrue(spin(wd, lambda: m.status == "running" and m.pid != old))
        self.assertEqual(m.restarts, 1)

    def test_retry_limit_gives_up(self):
        wd = self.mk([{"name": "bad", "command": '{python} -c "import sys; sys.exit(3)"', "max_retries": 2}]); wd.start_all()
        m = wd.procs["bad"]
        self.assertTrue(spin(wd, lambda: m.status == "gaveup"))
        self.assertEqual(m.restarts, 2); self.assertEqual(m.last_exit, 3)

    def test_policy_never(self):
        wd = self.mk([{"name": "once", "command": '{python} -c "import sys; sys.exit(1)"', "policy": "never"}]); wd.start_all()
        m = wd.procs["once"]
        self.assertTrue(spin(wd, lambda: m.status == "stopped"))
        self.assertEqual(m.restarts, 0)

    def test_on_failure_clean_exit_not_restarted(self):
        wd = self.mk([{"name": "ok", "command": '{python} -c "pass"', "policy": "on-failure"}]); wd.start_all()
        m = wd.procs["ok"]
        self.assertTrue(spin(wd, lambda: m.status == "stopped"))
        self.assertEqual(m.restarts, 0)

    def test_manual_stop_is_not_restarted(self):
        wd = self.mk([{"name": "svc", "command": SLEEP}]); wd.start_all()
        wd.control("svc", "stop")
        for _ in range(10):
            wd.tick(); time.sleep(0.05)
        self.assertEqual(wd.procs["svc"].status, "stopped")
        self.assertEqual(wd.procs["svc"].restarts, 0)

    def test_memory_limit_kills_process(self):
        cmd = '{python} -c "x = b\'x\' * (80*1024*1024); import time; time.sleep(30)"'
        wd = self.mk([{"name": "hog", "command": cmd, "mem_limit_mb": 30, "max_retries": 0}]); wd.start_all()
        self.assertTrue(spin(wd, lambda: wd.procs["hog"].status == "gaveup", 15))
        self.assertTrue(any("exceeds limit" in e["message"] for e in wd.events))

    def test_state_persists_across_restarts_of_watchdog(self):
        wd = self.mk([{"name": "svc", "command": SLEEP}]); wd.start_all()
        m = wd.procs["svc"]; old = m.pid; os.kill(old, signal.SIGKILL)
        spin(wd, lambda: m.restarts == 1 and m.status == "running")
        wd2 = self.mk([{"name": "svc", "command": SLEEP}])
        self.assertEqual(wd2.procs["svc"].restarts, 1)

    def test_bad_config_rejected(self):
        with self.assertRaises(ValueError):
            W.validate({"processes": [{"name": "x"}]})
        with self.assertRaises(ValueError):
            W.validate({"processes": [{"name": "x", "command": "y", "policy": "sometimes"}]})


if __name__ == "__main__":
    unittest.main()
