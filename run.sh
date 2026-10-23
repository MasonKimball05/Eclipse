#!/bin/zsh
# One-command launcher: sets up the virtualenv on first run (and whenever
# requirements.txt changes), then starts the browser.
#
#   ./run.sh           run with your normal profile
#   ./run.sh --fresh   run with a throwaway profile (wiped each launch)
#   ./run.sh --reset   rebuild the virtualenv from scratch, then run

set -e
SCRIPT_DIR="${0:A:h}"

# The venv lives outside the repo on purpose: Qt 6 refuses to load its macOS
# plugin from any path containing a hidden folder (.venv, .claude/worktrees, …)
VENV="$HOME/Library/Application Support/PythonBrowser/venv"
PYTHON="${PYTHON:-python3}"

for arg in "$@"; do
  case "$arg" in
    --fresh)
      export PYBROWSER_DATA_DIR="${TMPDIR:-/tmp}pybrowser-fresh"
      rm -rf "$PYBROWSER_DATA_DIR"
      ;;
    --reset)
      rm -rf "$VENV"
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
  "$VENV/bin/pip" install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"
  echo "$REQ_HASH" > "$VENV/.requirements-hash"
fi

cd "$SCRIPT_DIR"
exec "$VENV/bin/python" main.py
