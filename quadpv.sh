#!/usr/bin/env bash
# ==========================================================================
#  QuadPV - opens the interface with a Python already installed (Linux and macOS).
#
#  Looks, in this order, for: the QUADPV_PYTHON variable, a .venv virtual environment
#  in this folder, Miniconda/Anaconda/Miniforge in the user folder and in /opt,
#  and finally python3/python on the PATH. Requires Python 3.10 or newer. If a
#  library is missing, offers to install it with "pip install -r requirements.txt".
# ==========================================================================
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"

ok() {   # ok <python>: does it exist and is it 3.10 or newer?
    [ -n "$1" ] && command -v "$1" >/dev/null 2>&1 &&
        "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

PY=""
for cand in "${QUADPV_PYTHON:-}" "$HERE/.venv/bin/python" \
            "$HOME/miniconda3/bin/python" "$HOME/anaconda3/bin/python" \
            "$HOME/miniforge3/bin/python" "$HOME/mambaforge/bin/python" \
            "/opt/miniconda3/bin/python" "/opt/anaconda3/bin/python" \
            python3 python; do
    if ok "$cand"; then PY="$cand"; break; fi
done

if [ -z "$PY" ]; then
    echo "No Python 3.10 or newer was found."
    echo "Install Miniconda (https://docs.conda.io/projects/miniconda) or the system Python,"
    echo "or set QUADPV_PYTHON to the path of the python executable."
    exit 1
fi
echo "Python: $(command -v "$PY")"

if ! "$PY" -c 'import numpy, serial, pyqtgraph; from pyqtgraph.Qt import QtCore' >/dev/null 2>&1; then
    echo "Libraries missing for the interface: numpy, pyserial, pyqtgraph and PySide6."
    read -r -p "Install them now with pip? [y/N] " ans
    case "$ans" in
        y|Y|s|S) ;;
        *) exit 1 ;;
    esac
    if ! "$PY" -m pip install -r "$HERE/requirements.txt"; then
        echo
        echo "The installation failed. On distributions that block the system pip, create a virtual"
        echo "environment in this folder (this script will then use it automatically):"
        echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi
fi

exec "$PY" "$HERE/interface/quadpv.py" "$@"
