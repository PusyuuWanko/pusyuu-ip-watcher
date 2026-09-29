#!/usr/bin/env bash
set -u
cd "$(dirname "$0")"

printf '\n=== Pusyuu IP Watcher - Linux/macOS startup ===\n\n'

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]] && command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"; fi
if [[ -z "$PYTHON_BIN" ]] && command -v python >/dev/null 2>&1; then PYTHON_BIN="python"; fi

if [[ -z "$PYTHON_BIN" ]]; then
  printf 'Python 3 was not found.\n\n'
  read -r -p 'Install Python automatically if the OS package manager is available? [Y/n]: ' answer
  answer=${answer:-Y}
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v brew >/dev/null 2>&1; then
      brew install python
    else
      echo '[ERROR] No supported package manager was found.'
      echo 'Install Python 3 manually, then run this script again.'
      exit 1
    fi
  else
    echo 'Python installation cancelled.'
    exit 1
  fi
  if command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"; elif command -v python >/dev/null 2>&1; then PYTHON_BIN="python"; else echo '[ERROR] Python was installed but is still not available in this shell. Reopen the terminal and try again.'; exit 1; fi
fi

echo "Using: $PYTHON_BIN"

if [[ ! -x ".venv/bin/python" ]]; then
  echo 'Creating Python virtual environment...'
  "$PYTHON_BIN" -m venv .venv || { echo '[ERROR] Could not create the virtual environment. On Debian/Ubuntu install python3-venv.'; exit 1; }
fi

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  echo 'Created .env from .env.example.'
fi

echo 'Installing/updating dependencies...'
.venv/bin/python -m pip install -r requirements.txt || { echo '[ERROR] Dependency installation failed. See the output above.'; exit 1; }

echo 'Starting server...'
echo 'Open http://127.0.0.1:8000/ in your browser.'
echo 'Guide: http://127.0.0.1:8000/guide'
echo 'Press Ctrl+C to stop.'
exec .venv/bin/python main.py --host 127.0.0.1 --port 8000
