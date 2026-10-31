"""Run the live tests in a few groups at once (most of their time is spent waiting on pages and animations, so they
share a Mac well), and report them as one run.

    python tools/live_tests.py            three groups (./run.sh --live-tests)
    python tools/live_tests.py -n 1       all in one, as plain `pytest tests/live --live` does
    python tools/live_tests.py -k panel   anything else goes to pytest

Each group is `pytest --shard I/N`: the tests are shared out by how long they took last time (pytest's cache), so the
groups finish together. The memory of all of them (their browser windows' page processes too) is watched, and the
run stops if it goes over ECLIPSE_TEST_MEMORY_GB (6 by default), so it can never take the whole Mac with it."""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY_CAP_GB = float(os.environ.get("ECLIPSE_TEST_MEMORY_GB", 6))
SUMMARY = re.compile(r"^=*\s*(\d+ \w+.*) in ([\d.]+)s")


def tree_gb(roots) -> float:
    """The memory held by these processes and everything they started."""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0.0
    children, rss = {}, {}
    for line in out.splitlines():
        pid, ppid, kb = (int(n) for n in line.split())
        children.setdefault(ppid, []).append(pid)
        rss[pid] = kb
    total, todo = 0, list(roots)
    while todo:
        pid = todo.pop()
        total += rss.get(pid, 0)
        todo += children.get(pid, [])
    return total / 2 ** 20


def counts(summary: str) -> dict:
    return {word: int(n) for n, word in re.findall(r"(\d+) (\w+)", summary)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=3, help="how many groups to run at once (3)")
    ours, rest = parser.parse_known_args()
    groups = max(ours.n, 1)
    logs, runs = [], []
    started = time.monotonic()
    for i in range(1, groups + 1):
        log = tempfile.NamedTemporaryFile("w+", prefix=f"eclipse-live-{i}-", suffix=".txt", delete=False)
        command = [sys.executable, "-m", "pytest", "tests/live", "--live", "-q", *rest]
        if groups > 1:
            command += ["--shard", f"{i}/{groups}"]
        # (each group's own cap, on its main process, stays as a backstop: its share of the whole)
        env = dict(os.environ, ECLIPSE_TEST_MEMORY_GB=str(MEMORY_CAP_GB / groups))
        runs.append(subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env))
        logs.append(log)
    print(f"Running the live tests in {groups} group{'s' * (groups > 1)} …", flush=True)

    peak, stopped, finished = 0.0, None, set()
    while len(finished) < groups:
        time.sleep(1)
        for i, run in enumerate(runs):
            if i not in finished and run.poll() is not None:
                finished.add(i)
                logs[i].seek(0)
                line = next((SUMMARY.match(l) for l in reversed(logs[i].read().splitlines()) if SUMMARY.match(l)), None)
                print(f"  group {i + 1}: {line.group(1) if line else f'stopped (exit {run.returncode})'}"
                      f" in {time.monotonic() - started:.0f}s", flush=True)
        used = tree_gb([run.pid for run in runs if run.poll() is None])
        peak = max(peak, used)
        if used > MEMORY_CAP_GB and stopped is None:
            stopped = used
            for run in runs:
                if run.poll() is None:
                    run.terminate()

    total, failed, crashed = {}, False, []
    for i, (run, log) in enumerate(zip(runs, logs)):
        log.seek(0)
        text = log.read()
        log.close()
        line = next((SUMMARY.match(l) for l in reversed(text.splitlines()) if SUMMARY.match(l)), None)
        for word, n in counts(line.group(1) if line else "").items():
            if word != "deselected":
                total[word] = total.get(word, 0) + n
        if run.returncode in (0, 5):  # (5: nothing to run in this group, as with -k)
            os.unlink(log.name)
            continue
        failed = True
        if line is None:  # it never finished: a crash (or the cap) took it, and its tests after that didn't run
            crashed.append(f"group {i + 1} (exit {run.returncode}): its whole output is in {log.name}")
        print(f"\n----- group {i + 1} -----\n" + text[-12000:])
    took = time.monotonic() - started
    print(f"\n{', '.join(f'{n} {word}' for word, n in total.items()) or 'nothing ran'} in {took:.0f}s"
          f" (at most {peak:.1f} GB)")
    for crash in crashed:
        print(f"Stopped partway: {crash}")
    if stopped is not None:
        print(f"Stopped: the tests were using {stopped:.1f} GB, over the {MEMORY_CAP_GB:g} GB cap "
              f"(ECLIPSE_TEST_MEMORY_GB=8, say, raises it; -n 2 runs fewer at once)")
    sys.exit(1 if failed or stopped is not None else 0)


if __name__ == "__main__":
    main()
