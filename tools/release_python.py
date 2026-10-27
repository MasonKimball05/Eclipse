"""Set up the Python the release app is built with: python.org's, so the release runs on older Macs.

Run it once with:   tools/release_python.py      (./run.sh --release then uses it by itself)

Homebrew's Python is built for the macOS it's installed on (macOS 27 today), so a release made from it needs
that macOS too. python.org's is built for macOS 11 and up, and its OpenSSL comes with it, so a release made
from it runs wherever Qt does (macOS 13).

The installer isn't installed (that would need an admin password and change the Mac's Python): its package
is unpacked into the folder below, and the libraries that expect to be in /Library/Frameworks are pointed at
each other where they are instead. Then a virtualenv on it gets Eclipse's requirements, from PyPI.
"""

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "3.14.7"  # (the same Python as the development venv; change both together)
SHORT = ".".join(VERSION.split(".")[:2])
INSTALLER = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-macos11.pkg"
SIGNER = "Developer ID Installer: Python Software Foundation (BMM5U3QVKW)"
HOME = Path.home() / "Library" / "Application Support" / "Eclipse Build" / "release-python"
FRAMEWORK = HOME / "Python.framework"
VENV = HOME / "venv"
EXPECTED = f"/Library/Frameworks/Python.framework/Versions/{SHORT}/"
ROOT = Path(__file__).resolve().parents[1]


def run(*args) -> str:
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True).stdout


def download() -> Path:
    package = HOME / "download" / f"python-{VERSION}.pkg"
    if not package.exists():
        package.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {INSTALLER}…")
        with urllib.request.urlopen(INSTALLER, timeout=60) as response, open(package.with_suffix(".part"), "wb") as out:
            shutil.copyfileobj(response, out)
        package.with_suffix(".part").rename(package)
    checked = run("pkgutil", "--check-signature", package)
    if SIGNER not in checked or "trusted by the Apple notary service" not in checked:
        raise SystemExit(f"{package} isn't signed by the Python Software Foundation and notarized:\n{checked}")
    return package


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")


def unpack(package: Path):
    expanded = HOME / "expanded"
    shutil.rmtree(expanded, ignore_errors=True)
    shutil.rmtree(FRAMEWORK, ignore_errors=True)
    run("pkgutil", "--expand-full", package, expanded)
    shutil.copytree(expanded / "Python_Framework.pkg" / "Payload", FRAMEWORK, symlinks=True)
    shutil.rmtree(expanded)
    version = FRAMEWORK / "Versions" / SHORT
    shutil.rmtree(version / "Frameworks")  # (Tcl and Tk, for tkinter: never part of a browser)
    # Every reference to /Library/Frameworks/… pointed at the file it means, relative to the one referring
    # (shorter than the old path, so it always fits; and the release copies it in the same layout)
    for path in [p for p in version.rglob("*") if is_macho(p)]:
        changes = []
        for line in run("otool", "-L", path).splitlines()[1:]:
            dep = line.strip().split(" (compatibility")[0]
            if dep.startswith(EXPECTED):
                target = version / dep[len(EXPECTED):]
                changes += ["-change", dep, "@loader_path/" + os.path.relpath(target, path.parent)]
        if changes:
            run("install_name_tool", *changes, path)
            run("codesign", "--force", "--sign", "-", path)  # (changed, so signed again: just for this Mac)


def make_venv():
    shutil.rmtree(VENV, ignore_errors=True)
    python = FRAMEWORK / "Versions" / SHORT / "bin" / f"python{SHORT}"
    run(python, "-m", "venv", VENV)
    print("Installing Eclipse's requirements from PyPI…")
    subprocess.run([VENV / "bin" / "python", "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r",
                    ROOT / "requirements.txt"], check=True)
    print(run(VENV / "bin" / "python", "-c", "import ssl, PyQt6.QtWebEngineWidgets; print('Ready:', ssl.OPENSSL_VERSION)"))


def main():
    if sys.platform != "darwin":
        raise SystemExit("This is for macOS")
    unpack(download())
    make_venv()
    print(f"./run.sh --release now builds with {VENV}")


if __name__ == "__main__":
    main()
