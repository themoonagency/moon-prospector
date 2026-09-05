#!/bin/bash
# Creeaza mediul Python al proiectului si instaleaza dependentele. O singura data.
cd "$(dirname "$0")"
python3 -m venv .venv || exit 1
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt || exit 1
echo
echo "Gata. De acum poti rula:"
echo "  ./colecteaza.sh      - colecteaza firmele noi (tier A)"
echo "  ./colecteaza.sh A,B  - tier A si B"
echo "  ./dashboard.sh       - deschide dashboard-ul"
