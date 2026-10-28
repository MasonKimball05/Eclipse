"""Build the release Eclipse.app: everything inside it, signed with the Developer ID, ready to notarize.

Run it with:   ./run.sh --release                     (dist/Eclipse.app and dist/Eclipse.zip)
               ./run.sh --release --notarize PROFILE  (…sent to Apple, and the ticket stapled)
               ./run.sh --release --install           (…and put in /Applications, in place of the dev app)
               ./run.sh --release --notarize PROFILE --publish
                                                      (…and put on GitHub as a release, which every
                                                      release copy offers to update to: release_update.py)
               (each makes dist/Eclipse.dmg too: the drag-to-Applications window people download)
               ./run.sh --release --publish-built     (put the release already in dist/ on GitHub, without
                                                      building or notarizing again, once its code is committed
                                                      and pushed exactly as it was built)

Unlike ./run.sh --build-app (a launcher that runs this checkout), the release carries its own Python, Qt
and Eclipse, so it runs on any Mac and doesn't change when the checkout does. That's what lets macOS check
it: every file is sealed by the signature, so a copy that's been changed isn't Eclipse's any more, and
Settings → General can say "Verified" (browser/verified.py).

Inside it:
    Contents/MacOS/Eclipse                  the launcher: finds the rest relative to itself
    Contents/Frameworks/                    libpython (and, built with Homebrew's Python, its OpenSSL…),
                                            their paths rewritten to point here
    Contents/Resources/python/lib/python3.x the standard library (compiled ahead: nothing's ever written here)
    Contents/Resources/lib/python           PyQt6 (with Qt and Qt WebEngine), qtawesome, cryptography…
    Contents/Resources/app                  main.py and browser/

Every piece of code is signed from the inside out (Qt WebEngine's helper app, the Qt frameworks, each
library), then the app, all with the hardened runtime and a secure timestamp, as notarization requires.
"""

import argparse
import compileall
import os
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_app  # noqa: E402
from build_app import APP, NAME, ROOT  # noqa: E402

DIST = ROOT / "dist"
PY = f"python{sys.version_info.major}.{sys.version_info.minor}"
LIBPYTHON = f"lib{PY}.dylib"
# What Eclipse imports (requirements.txt and what those need), from the virtualenv
PACKAGES = ["PyQt6", "qtawesome", "qtpy", "cryptography", "cffi", "pycparser", "packaging"]
PACKAGE_FILES = ["_cffi_backend.*.so"]
# The parts of Qt Eclipse uses: the Python modules it (and qtawesome) imports, and the plugins they load. Of
# Qt's own libraries, only the ones these link to are kept (worked out from the links themselves, so a new
# need is picked up). The rest (Qt Quick 3D, Designer, multimedia and its FFmpeg, QML…) is about a third of
# Qt, and never loaded by a browser built on widgets.
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtPdf", "QtPrintSupport", "QtSvg", "QtWebChannel",
              "QtWebEngineCore", "QtWebEngineWidgets"]
QT_PLUGINS = ["platforms", "imageformats", "iconengines", "styles", "tls", "networkinformation", "permissions",
              "position"]  # (permissions: camera and microphone; position: a site's location)
LEAVE_OUT_PYQT = ["bindings", "uic", "lupdate", "qsci"]  # (for building with PyQt, not running it)
LEAVE_OUT_STDLIB = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "site-packages", "__pycache__",
                    "lib2to3", "pydoc_data", f"config-{sys.version_info.major}.{sys.version_info.minor}-darwin"}
                    # (that last: for building C extensions, never used by a running app)
SYSTEM = ("/usr/lib/", "/System/")

LAUNCHER = r"""
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>

// Everything is inside the app: found relative to this program, wherever the app is
int main(int argc, char *argv[]) {
    char exe[PATH_MAX], real[PATH_MAX], contents[PATH_MAX], home[PATH_MAX], path[PATH_MAX * 2], main_py[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, real) == NULL) {
        return 1;
    }
    char *slash = strrchr(real, '/');  // .../Contents/MacOS/Eclipse → .../Contents
    *slash = 0;
    slash = strrchr(real, '/');
    *slash = 0;
    snprintf(contents, sizeof contents, "%s", real);
    snprintf(home, sizeof home, "%s/Resources/python", contents);
    snprintf(path, sizeof path, "%s/Resources/lib/python:%s/Resources/app", contents, contents);
    snprintf(main_py, sizeof main_py, "%s/Resources/app/main.py", contents);
    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONPATH", path, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  // (the app is sealed: compiled ahead, never written to)
    char **args = malloc(sizeof(char *) * (argc + 2));
    args[0] = argv[0];
    args[1] = main_py;
    for (int i = 1; i < argc; i++) {
        args[i + 1] = argv[i];
    }
    args[argc + 1] = NULL;
    return Py_BytesMain(argc + 1, args);
}
"""

# The app: everything it loads is signed by the team now, so library validation stays on
ENTITLEMENTS = {k: v for k, v in build_app.ENTITLEMENTS.items()
                if k != "com.apple.security.cs.disable-library-validation"}
# Qt WebEngine's helper (Chromium's renderer): its JavaScript engine compiles code as it runs
HELPER_ENTITLEMENTS = {"com.apple.security.cs.allow-jit": True,
                       "com.apple.security.cs.allow-unsigned-executable-memory": True}


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True, **kwargs).stdout


# --- Copying it in ----------------------------------------------------------------------------------------

def _ignore(names_to_skip):
    def ignore(folder, names):
        return {n for n in names if n in names_to_skip or n.endswith((".pyc", ".a")) or n == "__pycache__"}
    return ignore


def copy_python(contents: Path):
    base = Path(sys.base_prefix)
    shutil.copy2(base / "Python", contents / "Frameworks" / LIBPYTHON)
    lib = contents / "Resources" / "python" / "lib"
    lib.mkdir(parents=True)
    shutil.copytree(base / "lib" / PY, lib / PY, ignore=_ignore(LEAVE_OUT_STDLIB), symlinks=True)
    # python.org's Python brings its own OpenSSL (and zstd…) beside the standard library, where its modules look
    # for them (tools/release_python.py points them there); Homebrew's has none here
    for library in (base / "lib").glob("*.dylib"):
        if not library.is_symlink() and library.name != LIBPYTHON and not library.name.startswith(
                ("libncurses", "libform", "libmenu", "libpanel")):  # (curses: never used by a browser)
            shutil.copy2(library, lib / library.name)


def thin(contents: Path):
    """Apple-silicon code only: python.org's Python is built for Intel Macs too, which Qt here isn't."""
    for path in [p for p in contents.rglob("*") if is_macho(p)]:
        archs = run("lipo", "-archs", path).split()
        if len(archs) > 1 and "arm64" in archs:
            run("lipo", path, "-thin", "arm64", "-output", path)
        elif archs and "arm64" not in archs:
            raise SystemExit(f"{path.name} has no Apple-silicon code ({', '.join(archs)})")


def copy_packages(contents: Path):
    source = Path(sysconfig.get_paths()["purelib"])
    dest = contents / "Resources" / "lib" / "python"
    dest.mkdir(parents=True)
    for name in PACKAGES:
        shutil.copytree(source / name, dest / name, ignore=_ignore(set()), symlinks=True)
    for pattern in PACKAGE_FILES:
        for found in source.glob(pattern):
            shutil.copy2(found, dest / found.name)
    trim_qt(dest / "PyQt6")


def trim_qt(pyqt: Path):
    qt = pyqt / "Qt6"
    for path in pyqt.iterdir():
        module = path.name.split(".")[0]
        if path.suffix == ".so" and module.startswith("Qt") and module not in QT_MODULES:
            path.unlink()
        elif path.suffix == ".pyi" or path.name in LEAVE_OUT_PYQT:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    shutil.rmtree(qt / "qml", ignore_errors=True)
    shutil.rmtree(qt / "qsci", ignore_errors=True)
    for plugins in (qt / "plugins").iterdir():
        if plugins.name not in QT_PLUGINS:
            shutil.rmtree(plugins)
    # What's loaded: the modules and plugins kept, and (over and over) whatever they link to in Qt6/lib
    # (a framework counts with everything in it, Qt WebEngine's helper app included)
    needed, pending = set(), [p for p in [*pyqt.glob("*.so"), *(qt / "plugins").rglob("*")] if is_macho(p)]
    while pending:
        for dep in linked(pending.pop()):
            name = dep.removeprefix("@rpath/").split("/")[0]
            if dep.startswith("@rpath/") and name not in needed and (qt / "lib" / name).exists():
                needed.add(name)
                library = qt / "lib" / name
                pending += [library] if library.is_file() else [f for f in library.rglob("*") if is_macho(f)]
    for library in (qt / "lib").iterdir():
        if library.name not in needed:
            shutil.rmtree(library) if library.is_dir() and not library.is_symlink() else library.unlink()


def copy_app(contents: Path):
    dest = contents / "Resources" / "app"
    dest.mkdir(parents=True)
    shutil.copy2(ROOT / "main.py", dest / "main.py")
    shutil.copytree(ROOT / "browser", dest / "browser", ignore=_ignore(set()))


# --- Libraries: copied in, and pointed at -------------------------------------------------------------------

def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with open(path, "rb") as f:
        return f.read(4) in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xbe\xba\xfe\xca")


def linked(path: Path) -> list[str]:
    """The libraries a Mach-O file loads (every architecture's, once each), its own name left out."""
    found = []
    for line in run("otool", "-L", path).splitlines():
        if line.startswith("\t"):
            name = line.strip().split(" (compatibility")[0]
            if name not in found:
                found.append(name)
    own = run("otool", "-D", path).splitlines()[1:]
    return [n for n in found if n not in [o.strip() for o in own]]


def bring_in_libraries(contents: Path):
    """Every library from outside the system that anything here loads (Homebrew's OpenSSL, SQLite…), copied
    into Frameworks, and every reference to it, or to the Homebrew Python, pointed at the copy."""
    frameworks = contents / "Frameworks"
    python_framework = str(Path(sys.base_prefix).resolve() / "Python")
    renamed = {}  # original path → name in Frameworks
    pending = [p for p in contents.rglob("*") if is_macho(p)]
    while pending:
        path = pending.pop()
        changes = []
        for dep in linked(path):
            real = str(Path(dep).resolve()) if dep.startswith("/") else dep
            if dep.startswith("@") or dep.startswith(SYSTEM):
                continue
            if real == python_framework or dep == python_framework or "Python.framework" in dep:
                changes.append((dep, f"@rpath/{LIBPYTHON}"))
                continue
            name = Path(real).name
            if real not in renamed:
                if not Path(real).exists():
                    raise SystemExit(f"{path.name} needs {dep}, which isn't on this Mac")
                renamed[real] = name
                copy = frameworks / name
                shutil.copy2(real, copy)
                os.chmod(copy, 0o755)
                run("install_name_tool", "-id", f"@rpath/{name}", copy)
                pending.append(copy)
            changes.append((dep, f"@rpath/{renamed[real]}"))
        if changes:
            args = []
            for old, new in changes:
                args += ["-change", old, new]
            run("install_name_tool", *args, path)
            _add_rpath(path)
    run("install_name_tool", "-id", f"@rpath/{LIBPYTHON}", frameworks / LIBPYTHON)


def _add_rpath(path: Path):
    if "@executable_path/../Frameworks" not in run("otool", "-l", path):
        run("install_name_tool", "-add_rpath", "@executable_path/../Frameworks", path)


def compile_launcher(contents: Path):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "launcher.c"
        source.write_text(LAUNCHER)
        # (for the oldest macOS Qt runs on: clang would otherwise build it for this Mac's)
        run("clang", "-O2", "-arch", "arm64", "-mmacosx-version-min=13.0", "-o", contents / "MacOS" / NAME, source, f"-I{sysconfig.get_paths()['include']}",
            f"-L{contents / 'Frameworks'}", f"-l{PY}", "-Wl,-rpath,@executable_path/../Frameworks")


# Run against the finished app's own Qt and Eclipse (by this Python: the same version as the app's), offscreen:
# every module of Eclipse imports, and Qt WebEngine loads a page. So trimming Qt too far fails the build.
CHECK = r"""
import importlib, pkgutil, sys
sys.path[:0] = sys.argv[1:3]
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWebEngineWidgets import QWebEngineView
app = QApplication(["eclipse-check"])
import browser, qtawesome
for found in pkgutil.walk_packages(browser.__path__, "browser."):
    importlib.import_module(found.name)
qtawesome.icon("ph.key")
assert sys.modules["PyQt6.QtCore"].__file__.startswith(sys.argv[1]), "not the app's own Qt"
from browser import net  # (its Python trusts no certificate on its own: updates would fail to connect)
assert net.context().cert_store_stats()["x509_ca"] > 50, "no trusted certificates for HTTPS"
view = QWebEngineView()
view.loadFinished.connect(lambda ok: (print("loaded" if ok else "failed"), app.quit()))
view.setHtml("<p>Eclipse</p>")
QTimer.singleShot(60_000, app.quit)
app.exec()
"""


def check_runs(contents: Path):
    with tempfile.TemporaryDirectory() as data:
        done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", CHECK, contents / "Resources" / "lib" / "python",
                               contents / "Resources" / "app"], capture_output=True, text=True, timeout=300,
                              env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin", "QT_QPA_PLATFORM": "offscreen",
                                   "ECLIPSE_DATA_DIR": data})
    if done.returncode != 0 or done.stdout.strip() != "loaded":
        raise SystemExit(f"The app doesn't run as built:\n{(done.stderr or done.stdout)[-2000:]}")


def minimum_macos(contents: Path) -> str:
    """The newest macOS any of its code was built for: the oldest it can run on. Built with python.org's Python
    (tools/release_python.py) that's Qt's macOS 13; with Homebrew's, the macOS of the Mac it was built on."""
    newest = (13, 0)
    for path in contents.rglob("*"):
        if is_macho(path):
            loads = run("otool", "-l", path).splitlines()
            for i, line in enumerate(loads):
                # (minos in newer libraries; older ones say "version" under LC_VERSION_MIN_MACOSX)
                old_style = line.strip() == "cmd LC_VERSION_MIN_MACOSX"
                for candidate in (loads[i + 2:i + 3] if old_style else [line]):
                    words = candidate.split()
                    if words and words[0] in ("minos", "version") and (old_style or words[0] == "minos"):
                        newest = max(newest, tuple(int(n) for n in words[1].split(".")[:2]))
    return ".".join(map(str, newest))


def precompile(contents: Path):
    """Every .py compiled now, checked by hash rather than time: nothing's ever written inside the app."""
    for folder in ("Resources/python", "Resources/lib", "Resources/app"):
        compileall.compile_dir(str(contents / folder), quiet=2, invalidation_mode=py_compile_mode())


def py_compile_mode():
    import py_compile
    return py_compile.PycInvalidationMode.UNCHECKED_HASH


# --- Signing -----------------------------------------------------------------------------------------------

def _sign(path: Path, identity: str, entitlements: dict | None = None):
    args = ["codesign", "--force", "--options", "runtime", "--timestamp", "--sign", identity]
    with tempfile.NamedTemporaryFile(suffix=".plist") as plist:
        if entitlements:
            plist.write(plistlib.dumps(entitlements))
            plist.flush()
            args += ["--entitlements", plist.name]
        done = subprocess.run(args + [str(path)], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"Couldn't sign {path}:\n{done.stderr.strip()}")


def sign_all(app: Path, identity: str):
    """From the inside out: helper apps (Qt WebEngine's), then every other piece of code, each on its own, then
    the app. Qt's frameworks come from a Python wheel, which can't hold the links a framework's layout needs
    (no Versions/Current, no top-level binary), so signing one as a bundle seals its resources and never its
    binary: each binary is signed by itself instead, and the app's own seal covers the rest."""
    contents = app / "Contents"
    helpers = sorted((p for p in contents.rglob("*.app") if p != app), key=lambda p: -len(p.parts))
    for helper in helpers:
        for code in helper.rglob("*"):
            if is_macho(code) and code != helper / "Contents" / "MacOS" / helper.stem:
                _sign(code, identity)
        _sign(helper, identity, HELPER_ENTITLEMENTS)
    for path in contents.rglob("*"):
        if is_macho(path) and not any(h in path.parents for h in helpers) and path != contents / "MacOS" / NAME:
            _sign(path, identity)
    _sign(app, identity, ENTITLEMENTS)
    run("codesign", "--verify", "--deep", "--strict", app)
    unsigned = [p for p in contents.rglob("*") if is_macho(p) and f"TeamIdentifier={_team(identity)}"
                not in subprocess.run(["codesign", "-dv", str(p)], capture_output=True, text=True).stderr]
    if unsigned:
        raise SystemExit("Not signed by the team: " + ", ".join(str(p.relative_to(app)) for p in unsigned[:5]))


def _team(identity: str) -> str:
    """The team a signing identity (its SHA-1 or its name) belongs to."""
    listed = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True,
                            text=True).stdout
    line = next((l for l in listed.splitlines() if identity in l), "")
    return line.rsplit("(", 1)[-1].rstrip(')"') if "(" in line else identity


# --- Putting it together ------------------------------------------------------------------------------------

# --- The DMG: the window people download, Eclipse beside an Applications folder to drag it to ------------------

DMG_SIZE = (660, 420)  # (the window, in points)
DMG_ICONS = {"app": (180, 200), "Applications": (480, 200)}


def dmg_background(folder: Path) -> Path:
    """The DMG window's picture, at 1x and 2x (one TIFF, so Retina screens get the sharp one): the night sky of
    Eclipse's icon, an arrow from where Eclipse sits to the Applications folder, and what to do."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen
    app = QGuiApplication.instance() or QGuiApplication(["eclipse-dmg"])  # noqa: F841 (fonts need one)
    paths = []
    for scale in (1, 2):
        w, h = DMG_SIZE
        image = QImage(w * scale, h * scale, QImage.Format.Format_ARGB32)
        image.setDevicePixelRatio(scale)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, w, h)
        sky.setColorAt(0, QColor("#1e1b4b"))
        sky.setColorAt(1, QColor("#020617"))
        p.fillRect(QRectF(0, 0, w, h), sky)
        (ax, ay), (bx, by) = DMG_ICONS["app"], DMG_ICONS["Applications"]
        start, end = QPointF(ax + 84, ay), QPointF(bx - 84, by)
        pen = QPen(QColor(255, 210, 122, 200), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        arc = QPainterPath(start)
        arc.quadTo(QPointF((start.x() + end.x()) / 2, ay - 34), end)
        p.drawPath(arc)
        head = QPainterPath(end)  # (the arrow's head, along the curve's last direction)
        head.lineTo(end + QPointF(-14, -10))
        head.moveTo(end)
        head.lineTo(end + QPointF(-14, 9))
        p.drawPath(head)
        p.setPen(QColor(242, 242, 244, 210))
        font = QFont("Helvetica Neue", 15)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.drawText(QRectF(0, h - 96, w, 28), Qt.AlignmentFlag.AlignCenter, f"Drag {NAME} into Applications")
        p.end()
        path = folder / ("background.png" if scale == 1 else "background@2x.png")
        image.save(str(path))
        paths.append(path)
    tiff = folder / "background.tiff"
    run("tiffutil", "-cathidpicheck", *paths, "-out", tiff)
    return tiff


def make_dmg(app: Path, identity: str) -> Path:
    """dist/Eclipse.dmg: Eclipse and an Applications shortcut, laid out over the background (dmgbuild writes the
    window's layout itself: nothing on your screen moves while it builds), then signed."""
    import dmgbuild  # (in the release venv: tools/release_python.py)
    dmg = DIST / f"{NAME}.dmg"
    dmg.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        settings = {
            "files": [str(app)], "symlinks": {"Applications": "/Applications"},
            "icon_locations": {app.name: DMG_ICONS["app"], "Applications": DMG_ICONS["Applications"]},
            "background": str(dmg_background(Path(scratch))), "window_rect": ((200, 160), DMG_SIZE),
            "icon_size": 128, "text_size": 13, "show_status_bar": False, "show_toolbar": False,
            "show_sidebar": False, "format": "ULFO", "filesystem": "APFS",
            "badge_icon": None, "default_view": "icon-view", "show_icon_preview": False,
        }
        dmgbuild.build_dmg(str(dmg), NAME, settings=settings)
    run("codesign", "--force", "--timestamp", "--sign", identity, dmg)
    return dmg


def notarize(path: Path, profile: str):
    print(f"Sending {path.name} to Apple for notarization (usually a few minutes)…")
    done = subprocess.run(["xcrun", "notarytool", "submit", str(path), "--keychain-profile", profile, "--wait"],
                          text=True)
    if done.returncode:
        raise SystemExit(f"Notarization failed: `xcrun notarytool log <id> --keychain-profile {profile}` says why")


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout.strip()


def publishable() -> str | None:
    """Why this checkout can't be published as a release, or None. A release is built from a commit that's on
    GitHub, so anyone can see exactly what code is in it."""
    if git("status", "--porcelain", "--untracked-files=no"):
        return "the checkout has changes that aren't committed"
    if loose := git("ls-files", "--others", "--exclude-standard", "--", "browser", "main.py"):
        return "these files aren't committed (git add them first): " + ", ".join(loose.split()[:5])
    head = git("rev-parse", "HEAD")
    if not head or not git("branch", "-r", "--contains", head):
        return "the commit isn't pushed to GitHub"
    return None


def release_notes() -> str:
    """What's changed since the last release: the commit subjects."""
    git("fetch", "--quiet", "--tags")  # (releases are tagged on GitHub: the latest isn't here otherwise)
    last = git("describe", "--tags", "--abbrev=0", "--match", "release-*")
    log = git("log", "--format=- %s", "-n", "30", f"{last}..HEAD" if last else "HEAD")
    return log or "- Small fixes"


def publish(archive: Path, build: str, version: str, dmg: Path | None = None):
    """The release on GitHub: the zip (what updates download) and the DMG (what people download)."""
    tag = f"release-{build}"
    print(f"Publishing {tag} on GitHub…")
    run("gh", "release", "create", tag, archive, *([dmg] if dmg and dmg.exists() else []),
        "--target", git("rev-parse", "HEAD"), "--title", f"{NAME} {version}", "--notes", release_notes())
    print(f"Published: every release copy of {NAME} will offer it within a day")


def differences(built: Path, source: Path) -> list[str]:
    """Eclipse's files (code and what it loads: scripts, styles…) that differ between an app's copy and a checkout,
    or are only in one. (Compiled .pyc files left out: the app has its own.)"""
    def code(root):
        files = [root / "main.py", *(p for p in (root / "browser").rglob("*") if p.is_file())]
        return {p.relative_to(root).as_posix(): p for p in files
                if p.exists() and p.suffix != ".pyc" and "__pycache__" not in p.parts and p.name != ".DS_Store"}
    ours, theirs = code(built), code(source)
    return sorted(name for name in ours.keys() | theirs.keys()
                  if name not in ours or name not in theirs or ours[name].read_bytes() != theirs[name].read_bytes())


def publish_built():
    """The notarized release in dist/, published as it is: only when the commit has exactly its code."""
    app, archive = DIST / f"{NAME}.app", DIST / f"{NAME}.zip"
    if not app.exists() or not archive.exists():
        raise SystemExit("There's no release in dist/ yet: ./run.sh --release --notarize PROFILE")
    if subprocess.run(["xcrun", "stapler", "validate", str(app)], capture_output=True).returncode != 0:
        raise SystemExit("The release in dist/ isn't notarized: build it with --notarize PROFILE")
    if why := publishable():
        raise SystemExit(f"Can't publish: {why}")
    if different := differences(app / "Contents" / "Resources" / "app", ROOT):
        raise SystemExit("The release in dist/ was built from different code than this commit (" +
                         ", ".join(different[:5]) + "): build it again with --notarize PROFILE --publish")
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    dmg = DIST / f"{NAME}.dmg"
    if dmg.exists() and subprocess.run(["xcrun", "stapler", "validate", str(dmg)], capture_output=True).returncode:
        raise SystemExit("The DMG in dist/ isn't notarized: build it again with --notarize PROFILE")
    publish(archive, info["CFBundleVersion"], info["CFBundleShortVersionString"], dmg)


def release(identity: str, notary_profile: str | None, install: bool, publishing=False) -> Path:
    if identity.startswith(("-", "Apple Development")) or not identity:
        raise SystemExit("A release needs the Developer ID Application certificate (none of the team's is here)")
    if publishing and (why := publishable()):
        raise SystemExit(f"Can't publish: {why}")
    # The version people see (2026.10.27), and the build number updates compare (the minute it was built)
    version, build = time.strftime("%Y.%-m.%-d"), time.strftime("%Y%m%d%H%M")
    app = DIST / f"{NAME}.app"
    shutil.rmtree(app, ignore_errors=True)
    contents = app / "Contents"
    for folder in ("MacOS", "Frameworks", "Resources"):
        (contents / folder).mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({**build_app.info_plist(),
                                                          "CFBundleShortVersionString": version,
                                                          "CFBundleVersion": build}))
    print("Copying Python, Qt and Eclipse in…")
    copy_python(contents)
    copy_packages(contents)
    copy_app(contents)
    for link in [p for p in app.rglob("*") if p.is_symlink() and not p.exists()]:
        link.unlink()  # (a link to something left out: the signature can't seal what isn't there)
    print("Bringing in the libraries they need…")
    thin(contents)
    bring_in_libraries(contents)
    compile_launcher(contents)
    build_app.make_icon(contents / "Resources" / "AppIcon.icns")
    print("Checking it runs (offscreen: no windows)…")
    check_runs(contents)
    print("Compiling the Python ahead of time…")
    precompile(contents)
    minimum = minimum_macos(contents)
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    (contents / "Info.plist").write_bytes(plistlib.dumps({**info, "LSMinimumSystemVersion": minimum}))
    print("Signing every piece (a few minutes: each one gets Apple's timestamp)…")
    sign_all(app, identity)
    size = sum(f.stat().st_size for f in app.rglob("*") if f.is_file() and not f.is_symlink()) // 1_000_000
    print(f"Signed: {app} ({size} MB)")
    print("Making the DMG…")
    dmg = make_dmg(app, identity)
    if notary_profile:
        # One round with Apple: the DMG, whose ticket covers the app inside it too
        notarize(dmg, notary_profile)
        run("xcrun", "stapler", "staple", dmg)
        run("xcrun", "stapler", "staple", app)
        print("Notarized and stapled")
    archive = DIST / f"{NAME}.zip"  # (what updates download: the stapled app)
    archive.unlink(missing_ok=True)
    run("ditto", "-c", "-k", "--keepParent", app, archive)
    if install:
        if APP.exists():
            existing = plistlib.loads((APP / "Contents" / "Info.plist").read_bytes()).get("CFBundleIdentifier")
            if existing not in build_app.OURS:
                raise SystemExit(f"{APP} isn't {NAME}; not touching it")
            shutil.rmtree(APP)
        run("ditto", app, APP)
        subprocess.run([build_app.LSREGISTER, "-f", str(APP)], check=False)
        print(f"Installed {APP}")
    if publishing:
        publish(archive, build, version, dmg)
    print(f"\nShare {dmg}: it opens on Macs with Apple silicon and macOS {minimum} or later.")
    return app


def main():
    parser = argparse.ArgumentParser(description=f"Build the release {NAME}.app")
    parser.add_argument("--sign", metavar="IDENTITY",
                        help="default: the team's Developer ID Application certificate")
    parser.add_argument("--notarize", metavar="PROFILE", help="notarytool keychain profile")
    parser.add_argument("--install", action="store_true", help="put it in /Applications too")
    parser.add_argument("--publish", action="store_true", help="put it on GitHub as a release (needs --notarize)")
    parser.add_argument("--publish-built", action="store_true",
                        help="put the notarized release already in dist/ on GitHub, without building it again")
    args = parser.parse_args()
    if args.publish_built:
        publish_built()
        return
    if args.publish and not args.notarize:
        parser.error("--publish needs --notarize: a release has to open on other Macs")
    identity = args.sign
    if identity is None:
        best = build_app.team_identity()
        identity = best[0] if best and best[1].startswith("Developer ID Application") else ""
        if best:
            print(f"Signing with {best[1]}")
    release(identity, args.notarize, args.install, args.publish)


if __name__ == "__main__":
    main()
