# This my attempt at learning how to make a custom browser using Python
# I am using Python because at time point I'm not overly interested in making a full top-down browser
# Maybe one day, but for now I am contempt with first learning how to use existing tools to do so
#
# The browser is modelled on Arc (sidebar, Spaces, command bar). The code lives in the
# `browser/` package — see claude_docs.md for a tour.

import sys

from browser.app import run

if __name__ == "__main__":
    sys.exit(run())
