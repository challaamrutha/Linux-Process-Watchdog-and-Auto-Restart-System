"""Uses roughly 70% of one CPU core, to trigger the CPU alert."""
import time
while True:
    end = time.time() + 0.07
    while time.time() < end:
        pass
    time.sleep(0.03)
