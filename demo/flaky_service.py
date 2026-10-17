"""Crashes with a non-zero exit code after a random 6-15 seconds."""
import random, sys, time
t = random.uniform(6, 15)
print("flaky-service up, will crash in %.1fs" % t, flush=True)
time.sleep(t)
print("flaky-service crashing", flush=True)
sys.exit(1)
