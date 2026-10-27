# The core on its own (docs/open-core-plan.md): Eclipse must run, and its everyday paths work, with no
# features at all and their modules unimportable. Run in a process of its own (core_only.py), since this
# one has every feature loaded already.
import subprocess
import sys
from pathlib import Path


def test_the_core_runs_without_any_feature(site):
    script = Path(__file__).with_name("core_only.py")
    done = subprocess.run([sys.executable, str(script), f"{site}/index.html"], capture_output=True, text=True,
                          timeout=120)
    noise = ("PAC script", "pbext", "Release of profile", "js:")
    err = "\n".join(line for line in done.stderr.splitlines() if not any(n in line for n in noise))
    assert done.returncode == 0 and "core ok" in done.stdout, (done.stdout[-3000:], err[-4000:])
