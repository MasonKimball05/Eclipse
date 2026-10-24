"""Build PythonBrowser.app into ~/Applications.

Run it with:   ./run.sh --build-app
               ./run.sh --build-app --sign "Developer ID Application: Your Name (TEAMID)"
               ./run.sh --build-app --sign "Developer ID Application: …" --notarize PROFILE

By default the app is signed ad hoc, which is all your own Mac needs. --sign uses a real
certificate with Apple's hardened runtime, which some macOS features require (Apple's
Passwords helper, for the iCloud Passwords extension, refuses ad hoc browsers). --notarize
then sends it to Apple using credentials saved once with
    xcrun notarytool store-credentials PROFILE --apple-id you@example.com --team-id TEAMID
and staples the result to the app.

The app is a thin native launcher around this checkout and its virtualenv, so code
changes show up the next time you open it, with no rebuild. Rebuild only after moving
the checkout or upgrading Homebrew's Python.

Why a compiled launcher instead of a shell script: macOS ties the Dock icon, the app
name and incoming links (from other apps, once it's the default browser) to the
process the bundle starts. A script that runs `python` would show up as "Python" and
never receive those links, so the launcher embeds Python and runs main.py itself.
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = Path.home() / "Applications" / "PythonBrowser.app"
BUNDLE_ID = "com.masonkimball.pythonbrowser"  # must match browser/macos.py
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
              "/Support/lsregister")

LAUNCHER = r"""
#include <Python.h>
#include <stdlib.h>

// Paths are baked in at build time (see tools/build_app.py)
int main(int argc, char *argv[]) {
    setenv("PYTHONHOME", PY_HOME, 1);
    setenv("PYTHONPATH", SITE_PACKAGES, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    char **args = malloc(sizeof(char *) * (argc + 2));
    args[0] = argv[0];
    args[1] = MAIN_PY;
    for (int i = 1; i < argc; i++) {
        args[i + 1] = argv[i];
    }
    args[argc + 1] = NULL;
    return Py_BytesMain(argc + 1, args);
}
"""


# What the hardened runtime has to allow for this app (only used with --sign):
ENTITLEMENTS = {
    # Python, PyQt and Qt load from the virtualenv and Homebrew, not from inside the bundle, and
    # aren't signed by our team
    "com.apple.security.cs.disable-library-validation": True,
    # Chromium's JavaScript engine compiles code at run time, and ctypes callbacks (the native
    # macOS tweaks in browser/macos.py) need writable+executable memory
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
    # Video calls and "use my location" in websites you allow
    "com.apple.security.device.camera": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.personal-information.location": True,
}


def info_plist() -> dict:
    return {
        "CFBundleName": "PythonBrowser",
        "CFBundleDisplayName": "PythonBrowser",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "PythonBrowser",
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        # macOS asks you before a site gets these; without the reasons, it ends the app instead
        "NSCameraUsageDescription": "Websites you allow can use the camera, for video calls.",
        "NSMicrophoneUsageDescription": "Websites you allow can use the microphone, for calls and recordings.",
        "NSLocationUsageDescription": "Websites you allow can know your location.",
        "NSLocationWhenInUseUsageDescription": "Websites you allow can know your location.",
        "NSSupportsAutomaticGraphicsSwitching": True,
        # What makes it a browser in macOS's eyes: it can open web links...
        "CFBundleURLTypes": [{"CFBundleURLName": "Web site URL", "CFBundleURLSchemes": ["http", "https"]}],
        # ...and web pages from Finder ("Open With")
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Web page",
            "CFBundleTypeRole": "Viewer",
            "LSItemContentTypes": ["public.html", "public.xhtml"],
        }],
    }


def compile_launcher(dest: Path):
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = sysconfig.get_paths()["purelib"]  # this virtualenv's packages
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "launcher.c"
        source.write_text(LAUNCHER)
        subprocess.run([
            "clang", "-O2", "-o", str(dest), str(source),
            f"-I{sysconfig.get_paths()['include']}",
            f"-L{sysconfig.get_config_var('LIBPL')}", f"-l{version}",
            f'-DPY_HOME="{sys.base_prefix}"',
            f'-DSITE_PACKAGES="{site_packages}"',
            f'-DMAIN_PY="{ROOT / "main.py"}"',
        ], check=True)


def make_icon(dest: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (needed for painting)
    size = 1024
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # macOS icon grid: an 824px rounded square centred in the 1024 canvas
    tile = QRectF(100, 100, 824, 824)
    gradient = QLinearGradient(tile.topLeft(), tile.bottomRight())
    gradient.setColorAt(0, QColor("#a78bfa"))
    gradient.setColorAt(0.55, QColor("#6366f1"))
    gradient.setColorAt(1, QColor("#0ea5e9"))
    shape = QPainterPath()
    shape.addRoundedRect(tile, 185, 185)
    p.fillPath(shape, gradient)

    # A browser window with Arc's signature sidebar, in white
    window = QRectF(250, 290, 524, 444)
    outline = QPainterPath()
    outline.addRoundedRect(window, 64, 64)
    p.save()
    p.setClipPath(outline)
    p.fillRect(QRectF(window.left(), window.top(), 176, window.height()), QColor("white"))  # the sidebar
    p.restore()
    p.setPen(QPen(QColor("white"), 44, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(outline)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#6366f1"))
    for i in range(3):  # tabs in the sidebar
        p.drawRoundedRect(QRectF(window.left() + 40, window.top() + 74 + i * 76, 100, 34), 17, 17)
    p.setBrush(QColor(255, 255, 255, 210))
    p.drawEllipse(QPointF(window.left() + 350, window.top() + 222), 20, 20)  # a spark on the page
    p.end()

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = points * scale
                name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
                image.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation).save(str(iconset / name))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


def sign(app: Path, identity: str):
    if identity == "-":
        # Ad hoc, so macOS treats the bundle as one unit
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
        return
    with tempfile.NamedTemporaryFile(suffix=".plist") as entitlements:
        entitlements.write(plistlib.dumps(ENTITLEMENTS))
        entitlements.flush()
        result = subprocess.run(["codesign", "--force", "--options", "runtime", "--timestamp",
                                 "--entitlements", entitlements.name, "--sign", identity, str(app)],
                                capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"Signing failed:\n{result.stderr.strip()}\n\nYour signing identities:\n" +
                 subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                                capture_output=True, text=True).stdout)
    subprocess.run(["codesign", "--verify", "--strict", str(app)], check=True)
    print(f"Signed with “{identity}” (hardened runtime)")


def notarize(app: Path, profile: str):
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "PythonBrowser.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True)
        print("Sending to Apple for notarization (this usually takes a few minutes)…")
        result = subprocess.run(["xcrun", "notarytool", "submit", str(archive), "--keychain-profile", profile,
                                 "--wait"], text=True)
    if result.returncode:
        sys.exit("Notarization failed. `xcrun notarytool log <submission id> --keychain-profile "
                 f"{profile}` shows why.")
    subprocess.run(["xcrun", "stapler", "staple", str(app)], check=True)
    print("Notarized and stapled")


def main():
    parser = argparse.ArgumentParser(description="Build PythonBrowser.app")
    parser.add_argument("--sign", default=os.environ.get("PYBROWSER_SIGN_IDENTITY", "-"), metavar="IDENTITY",
                        help='a codesigning identity (e.g. "Developer ID Application: …"); default: ad hoc')
    parser.add_argument("--notarize", metavar="PROFILE", help="notarytool keychain profile (needs --sign)")
    parser.add_argument("--output", type=Path, default=APP.parent, metavar="FOLDER",
                        help="where to put the app (default: ~/Applications)")
    args = parser.parse_args()
    if args.notarize and args.sign == "-":
        parser.error("--notarize needs --sign with a Developer ID Application certificate")
    build(args.output / APP.name, args.sign, args.notarize)


def build(app: Path, identity="-", notary_profile=None):
    if app.exists():
        # Only ever replace our own app
        existing = plistlib.loads((app / "Contents" / "Info.plist").read_bytes()).get("CFBundleIdentifier")
        if existing != BUNDLE_ID:
            sys.exit(f"{app} exists and isn't PythonBrowser ({existing}); not touching it.")
        shutil.rmtree(app)

    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    (contents / "Info.plist").write_bytes(plistlib.dumps(info_plist()))
    print("Compiling the launcher…")
    compile_launcher(contents / "MacOS" / "PythonBrowser")
    print("Drawing the icon…")
    make_icon(contents / "Resources" / "AppIcon.icns")
    sign(app, identity)
    if notary_profile:
        notarize(app, notary_profile)
    if app == APP:  # only the real install claims web links; a test build elsewhere shouldn't
        subprocess.run([LSREGISTER, "-f", str(app)], check=False)  # tell Launch Services it can open links

    print(f"\nBuilt {app}")
    print("Open it from your Applications folder or Spotlight.")
    print("To make it your default browser: PythonBrowser menu → Set as Default Browser…")


if __name__ == "__main__":
    main()
