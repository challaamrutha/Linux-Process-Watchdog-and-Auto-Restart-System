"""Fails immediately every time, to demonstrate the retry limit."""
import sys
print("always-fails: cannot start", flush=True)
sys.exit(2)
