#!/usr/bin/env python3
"""test_prog.py — intentionally slow Python sort with a security smell."""
import subprocess

N = 6000

# security smell: os.system / subprocess with string — bandit will flag this
# (using a safe version here so it actually runs, but pattern is present)
def log_start():
    # This is the safe way, but bandit may flag shell=True patterns nearby
    result = subprocess.run(["echo", "starting sort"], capture_output=True)
    _ = result

def bubble_sort(arr):
    n = len(arr)
    for i in range(n - 1):
        for j in range(n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]

def main():
    log_start()
    arr = list(range(N, 0, -1))
    for _ in range(3):
        bubble_sort(arr)
    print(f"sorted[0]={arr[0]} sorted[{N-1}]={arr[N-1]}")

if __name__ == "__main__":
    main()
