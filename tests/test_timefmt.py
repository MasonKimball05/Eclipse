"""Date formats written the readable way ("%-d"), made to work on Windows too."""

import datetime
import sys

from browser.timefmt import portable


def test_formats_work_here_and_drop_the_leading_zero():
    day = datetime.datetime(2026, 10, 7, 9, 5)
    assert day.strftime(portable("%b %-d, %-I:%M %p")) == "Oct 7, 9:05 AM"
    assert portable("%-d") == ("%#d" if sys.platform == "win32" else "%-d")
