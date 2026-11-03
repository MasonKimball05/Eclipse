"""Build Eclipse's native pages (native/Sources, in Swift) into native/build/libEclipseNative.dylib, the library
browser/native.py loads. Only on a Mac with Xcode's tools (swiftc); without it, Eclipse uses its Qt pages.

Run it with:   python tools/build_native.py          (only if a source changed since the last build)
               python tools/build_native.py --force

./run.sh builds it when needed, as do --build-app and --release (which puts it in the app's Frameworks).
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "native" / "Sources"
LIBRARY = ROOT / "native" / "build" / "libEclipseNative.dylib"
MINIMUM_MACOS = "13.0"  # (as Qt 6.11's own minimum: nothing here should raise what the app needs)


def sources() -> list[Path]:
    return sorted(SOURCES.glob("*.swift"))


def stale(library: Path = LIBRARY) -> bool:
    return not library.exists() or any(s.stat().st_mtime > library.stat().st_mtime for s in sources())


def build(library: Path = LIBRARY, force=False, quiet=False) -> Path | None:
    """The built library, or None where it can't be built (not a Mac, no swiftc): Eclipse then uses Qt's pages."""
    if sys.platform != "darwin" or shutil.which("swiftc") is None or not sources():
        return None
    if not force and not stale(library):
        return library
    library.parent.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print("Compiling the native pages (Swift)…")
    temporary = library.with_suffix(".building.dylib")
    result = subprocess.run(["swiftc", "-emit-library", "-module-name", "EclipseNative", "-O", "-swift-version", "5",
                             "-target", f"arm64-apple-macos{MINIMUM_MACOS}",
                             "-Xlinker", "-install_name", "-Xlinker", f"@rpath/{library.name}",
                             "-o", str(temporary), *map(str, sources())], capture_output=True, text=True)
    if result.returncode:
        temporary.unlink(missing_ok=True)
        print(f"The native pages didn't compile (Eclipse uses its Qt pages until they do):\n{result.stderr.strip()}",
              file=sys.stderr)
        return None
    temporary.replace(library)  # (in one step: a running Eclipse never sees half a library)
    return library


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Eclipse's native pages")
    parser.add_argument("--force", action="store_true", help="build even if nothing changed")
    built = build(force=parser.parse_args().force)
    sys.exit(0 if built else 1)
