#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 1

MIN_PY="3.10"

echo '=== Pusyuu IP Watcher - Python diagnostics ==='
echo

for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    ver="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"
    if "$c" -c "import sys; sys.exit(0 if sys.version_info >= tuple(map(int, '$MIN_PY'.split('.'))) else 1)" 2>/dev/null; then
      echo "[OK] Python command: $c ($ver)"
    else
      echo "[NG] Python command: $c ($ver) is older than $MIN_PY."
    fi
    if "$c" -c 'import venv, ensurepip' >/dev/null 2>&1; then
      echo "[OK] $c has the venv module."
    else
      echo "[NG] $c cannot create virtual environments. On Debian/Ubuntu install python3-venv."
    fi
  else
    echo "[NG] Python command: $c is not available."
  fi
done

if [[ -x ".venv/bin/python" ]]; then
  echo "[OK] Virtual environment exists. ($(.venv/bin/python --version 2>&1))"
elif [[ -d ".venv" ]]; then
  echo '[WARN] .venv exists but is not usable here (broken, or created on Windows).'
else
  echo '[INFO] Virtual environment does not exist yet. start_linux_mac.sh will create it.'
fi

pm=""
for c in brew apt-get dnf yum pacman zypper apk; do
  if command -v "$c" >/dev/null 2>&1; then pm="$c"; break; fi
done
if [[ -n "$pm" ]]; then
  echo "[OK] Package manager: $pm (automatic Python installation is possible)."
else
  echo '[WARN] No supported package manager: install Python manually.'
fi

if [[ "$(id -u)" -eq 0 ]]; then
  echo '[INFO] Running as root.'
elif command -v sudo >/dev/null 2>&1; then
  echo '[OK] sudo is available.'
elif [[ "$pm" != "brew" && -n "$pm" ]]; then
  echo '[WARN] sudo is not available: run the startup script as root to install packages.'
fi

echo
echo 'Recommended action:'
echo '  1. Run ./start_linux_mac.sh (or: bash start_linux_mac.sh).'
echo '  2. If asked whether to install Python, press Y.'
echo "  3. If automatic installation is unavailable, install Python $MIN_PY+ from https://www.python.org/downloads/"
