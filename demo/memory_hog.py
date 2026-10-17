"""Grows by ~10 MB per second until the watchdog kills it for exceeding its memory limit."""
import time
hog = []
while True:
    hog.append(b"x" * (10 * 1024 * 1024))
    print("allocated %d MB" % (len(hog) * 10), flush=True)
    time.sleep(1)
