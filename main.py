# This my attempt at learning how to make a custom browser using Python
# I am using Python because at time point I'm not overly interested in making a full top-down browser
# Maybe one day, but for now I am contempt with first learning how to use existing tools to do so
#
# The browser is modelled on Arc (sidebar, Spaces, command bar). The code lives in the
# `browser/` package — see claude_docs.md for a tour.

import os
import sys

if sys.platform == "win32" and not sys.flags.utf8_mode:
    # Windows Python reads and writes text in the old Windows character set unless told otherwise, and an emoji
    # in a tab's title would then stop the session saving: start again in UTF-8 mode (run.ps1 starts in it)
    import subprocess
    os.environ["PYTHONUTF8"] = "1"
    sys.exit(subprocess.call([sys.executable, *sys.argv]))

from browser.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
