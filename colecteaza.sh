#!/bin/bash
# Colecteaza firmele noi. Foloseste: ./colecteaza.sh  (implicit tier A)
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Rulez intai instalarea..."; ./instaleaza.sh || exit 1; }
source .venv/bin/activate
python -m moon.pipeline colectare --tiers "${1:-A}"
