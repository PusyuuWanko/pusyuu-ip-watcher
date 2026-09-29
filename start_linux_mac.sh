#!/usr/bin/env bash
set -u
cd "$(dirname "$0")" || exit 1

# checker.py uses "str | None" annotations, which need Python 3.10+.
MIN_PY="3.10"

printf '\n=== Pusyuu IP Watcher - Linux/macOS startup ===\n\n'

is_tty() { [[ -t 0 && -t 1 ]]; }

pause_exit() {
  if is_tty; then
    echo
    read -r -p 'Press Enter to close...' _ || true
  fi
  exit "$1"
}

fail() {
  echo
  echo "[ERROR] $*"
  echo 'Read the error above. If this is a Python/package error, run ./check_python.sh for diagnostics.'
  pause_exit 1
}

ask_yes() {
  local answer
  if ! is_tty; then
    echo "$1 -> no terminal, skipped."
    return 1
  fi
  read -r -p "$1 [Y/n]: " answer || return 1
  [[ "${answer:-Y}" =~ ^[Yy]$ ]]
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo '[ERROR] sudo is not available. Run this script as root, or install the packages manually.'
    return 1
  fi
}

python_ok() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= tuple(map(int, '$MIN_PY'.split('.'))) else 1)" >/dev/null 2>&1
}

python_version() {
  "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null
}

OLD_PYTHON=""
find_python() {
  local c
  hash -r 2>/dev/null
  for c in ${PYTHON_BIN:-} python3 python3.13 python3.12 python3.11 python3.10 python \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    if python_ok "$c"; then
      PYTHON_BIN="$(command -v "$c")"
      return 0
    fi
    [[ -z "$OLD_PYTHON" ]] && OLD_PYTHON="$c ($(python_version "$c"))"
  done
  return 1
}

install_python() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
      echo '[ERROR] Homebrew is not installed. See https://brew.sh/'
      return 1
    fi
    brew install python
  elif command -v apt-get >/dev/null 2>&1; then
    as_root apt-get update && as_root apt-get install -y python3 python3-venv python3-pip
  elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y python3 python3-pip
  elif command -v yum >/dev/null 2>&1; then
    as_root yum install -y python3 python3-pip
  elif command -v pacman >/dev/null 2>&1; then
    as_root pacman -S --needed --noconfirm python python-pip
  elif command -v zypper >/dev/null 2>&1; then
    as_root zypper --non-interactive install python3 python3-pip
  elif command -v apk >/dev/null 2>&1; then
    as_root apk add python3 py3-pip
  else
    echo '[ERROR] No supported package manager was found (apt-get, dnf, yum, pacman, zypper, apk, brew).'
    return 1
  fi
}

manual_install() {
  echo
  echo "Please install Python $MIN_PY or newer manually:"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo '  https://www.python.org/downloads/macos/'
    echo '  or install Homebrew (https://brew.sh/) and run this script again.'
  else
    echo '  Use your distribution package manager, or'
    echo '  https://www.python.org/downloads/source/'
  fi
  echo 'Then reopen the terminal and run this script again.'
  pause_exit 1
}

if ! find_python; then
  if [[ -n "$OLD_PYTHON" ]]; then
    echo "[ERROR] Python $MIN_PY or newer is required, but only $OLD_PYTHON was found."
  else
    echo '[ERROR] Python 3 was not found.'
  fi
  echo
  echo 'Python is required to run this application.'
  echo
  if ask_yes "Install Python $MIN_PY+ automatically with the OS package manager?"; then
    echo
    if ! install_python; then
      echo
      echo '[ERROR] Python installation failed.'
      manual_install
    fi
    if ! find_python; then
      echo
      if [[ -n "$OLD_PYTHON" ]]; then
        echo "[ERROR] The package manager installed $OLD_PYTHON, which is older than $MIN_PY."
      else
        echo '[ERROR] Python was installed but this shell cannot see it yet.'
      fi
      manual_install
    fi
  else
    manual_install
  fi
fi

echo "Using: $PYTHON_BIN ($(python_version "$PYTHON_BIN"))"
echo

venv_ok() { [[ -x ".venv/bin/python" ]] && .venv/bin/python -m pip --version >/dev/null 2>&1; }

if ! venv_ok; then
  if [[ -d ".venv" && ! -d ".venv/bin" ]]; then
    fail '.venv was created on another OS (Windows). Delete the .venv folder, or use a separate copy of this folder on Linux/macOS.'
  fi
  if [[ -d ".venv" ]]; then
    echo 'The existing virtual environment is broken. Recreating it...'
    rm -rf .venv
  fi
  echo 'Creating Python virtual environment...'
  if ! "$PYTHON_BIN" -m venv .venv; then
    rm -rf .venv
    # Debian/Ubuntu ship venv/ensurepip as a separate package.
    PY_MM="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if command -v apt-get >/dev/null 2>&1 && ask_yes "The venv module is missing. Install python${PY_MM}-venv with apt-get?"; then
      as_root apt-get install -y "python${PY_MM}-venv" || as_root apt-get install -y python3-venv
      "$PYTHON_BIN" -m venv .venv || { rm -rf .venv; fail 'Could not create the virtual environment.'; }
    else
      fail 'Could not create the virtual environment. On Debian/Ubuntu install python3-venv.'
    fi
  fi
fi

if [[ ! -f ".env" && -f ".env.example" ]]; then
  echo
  echo 'Creating .env from .env.example...'
  cp .env.example .env
fi

echo 'Installing/updating dependencies...'
.venv/bin/python -m pip install -r requirements.txt || fail 'Dependency installation failed.'

echo
echo 'Starting server...'
# Host and port come from WATCHER_HOST / WATCHER_PORT in .env.
exec .venv/bin/python main.py
