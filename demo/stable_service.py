"""Well-behaved demo service: runs forever, exits cleanly on SIGTERM."""
import signal, sys, time
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
while True:
    print("stable-service heartbeat", flush=True)
    time.sleep(2)
