# Date formats that work everywhere. "%-d" (the day without a leading zero: "Oct 7", not "Oct 07") is what
# macOS and Linux understand; Windows's C library rejects it and wants "%#d". portable() writes the format
# for the system it's running on, so each format can stay written the readable way.

import sys


def portable(fmt: str) -> str:
    return fmt.replace("%-", "%#") if sys.platform == "win32" else fmt
