#!/bin/zsh
# One-command launcher: sets up the virtualenv on first run (and whenever
# requirements.txt changes), then starts the browser.
#
#   ./run.sh           run with your normal profile
#   ./run.sh --fresh   run with a throwaway profile (wiped each launch)
#   ./run.sh --reset   rebuild the virtualenv from scratch, then run
#   ./run.sh --build-app   build Eclipse.app into /Applications (Dock icon, default browser): a launcher that
#                          runs this checkout, signed with the team's certificate if this Mac has one
#   ./run.sh --release     the release app in dist/: everything inside it, signed with the Developer ID;
#                          add --notarize PROFILE to send it to Apple, --install to put it in /Applications.
#                          Built with python.org's Python once tools/release_python.py has set it up (so it
#                          runs on macOS 13 and up), else with this venv's
#   ./run.sh --tests       run the unit tests
#   ./run.sh --live-tests  run the live tests too (a browser window opens while they run, ~3 min)
#   ./run.sh --core        run without the features (browser/features.py): the plain browser
#   ./run.sh <url>     open a link (in the already-running browser, if there is one)

set -e
SCRIPT_DIR="${0:A:h}"

# The venv lives outside the repo on purpose: Qt 6 refuses to load its macOS
# plugin from any path containing a hidden folder (.venv, .claude/worktrees, …)
BUILD="$HOME/Library/Application Support/Eclipse Build"
VENV="$BUILD/venv"
RELEASE_VENV="$BUILD/release-python/venv"  # (tools/release_python.py)
OLD_BUILD="$HOME/Library/Application Support/PythonBrowser"  # (before the rename)
if [[ -d "$OLD_BUILD" && ! -e "$BUILD" ]]; then
  mv "$OLD_BUILD" "$BUILD"
fi
PYTHON="${PYTHON:-python3}"
BUILD_APP=0
RELEASE=0
TESTS=""
PASS=()

for arg in "$@"; do
  case "$arg" in
    --fresh)
      export ECLIPSE_DATA_DIR="${TMPDIR:-/tmp}eclipse-fresh"
      rm -rf "$ECLIPSE_DATA_DIR"
      ;;
    --reset)
      rm -rf "$VENV"
      ;;
    --build-app)
      BUILD_APP=1
      ;;
    --release)
      RELEASE=1
      ;;
    --tests)
      TESTS="unit"
      ;;
    --live-tests)
      TESTS="live"
      ;;
    --core)
      export ECLIPSE_CORE_ONLY=1
      ;;
    *)
      PASS+=("$arg")
      ;;
  esac
done

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Setting up virtualenv in $VENV …"
  mkdir -p "${VENV:h}"
  "$PYTHON" -m venv "$VENV"
fi

REQ_HASH="$(shasum "$SCRIPT_DIR/requirements.txt" | cut -d' ' -f1)"
if [[ "$(cat "$VENV/.requirements-hash" 2>/dev/null)" != "$REQ_HASH" ]]; then
  echo "Installing requirements …"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"
  echo "$REQ_HASH" > "$VENV/.requirements-hash"
fi

cd "$SCRIPT_DIR"
if [[ -n "$TESTS" ]]; then
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt
  if [[ "$TESTS" == "live" ]]; then
    exec "$VENV/bin/python" -m pytest tests --live -q
  fi
  exec "$VENV/bin/python" -m pytest tests -q
fi
if (( RELEASE )); then
  if [[ -x "$RELEASE_VENV/bin/python" ]]; then
    exec "$RELEASE_VENV/bin/python" tools/release.py "${PASS[@]}"  # e.g. --notarize PROFILE --install
  fi
  exec "$VENV/bin/python" tools/release.py "${PASS[@]}"
fi
if (( BUILD_APP )); then
  exec "$VENV/bin/python" tools/build_app.py "${PASS[@]}"  # e.g. --sign "Developer ID Application: …"
fi
exec "$VENV/bin/python" main.py "${PASS[@]}"
