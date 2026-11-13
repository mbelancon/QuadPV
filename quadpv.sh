#!/usr/bin/env bash
# ==========================================================================
#  QuadPV - abre a interface com um Python já instalado (Linux e macOS).
#
#  Procura, nesta ordem: a variável QUADPV_PYTHON, um ambiente virtual .venv
#  nesta pasta, Miniconda/Anaconda/Miniforge na pasta do usuário e em /opt,
#  e por fim python3/python do PATH. Exige Python 3.10 ou mais novo. Se faltar
#  alguma biblioteca, oferece instalar com "pip install -r requirements.txt".
# ==========================================================================
cd "$(dirname "$0")" || exit 1
HERE="$(pwd)"

ok() {   # ok <python>: existe e é 3.10 ou mais novo?
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
    echo "Nenhum Python 3.10 ou mais novo foi encontrado."
    echo "Instale o Miniconda (https://docs.conda.io/projects/miniconda) ou o Python do sistema,"
    echo "ou defina QUADPV_PYTHON com o caminho do python."
    exit 1
fi
echo "Python: $(command -v "$PY")"

if ! "$PY" -c 'import numpy, serial, pyqtgraph; from pyqtgraph.Qt import QtCore' >/dev/null 2>&1; then
    echo "Faltam bibliotecas para a interface: numpy, pyserial, pyqtgraph e PySide6."
    read -r -p "Instalar agora com pip? [s/N] " ans
    case "$ans" in
        s|S|y|Y) ;;
        *) exit 1 ;;
    esac
    if ! "$PY" -m pip install -r "$HERE/requirements.txt"; then
        echo
        echo "A instalação falhou. Em distribuições que bloqueiam o pip do sistema, crie um ambiente"
        echo "virtual nesta pasta (este script passa a usá-lo sozinho):"
        echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi
fi

exec "$PY" "$HERE/interface/quadpv.py" "$@"
