#!/bin/bash
# Cauta pe Google prospectii deja colectati care n-au fost verificati.
cd "$(dirname "$0")"
source .venv/bin/activate
python -m moon.pipeline verifica "$@"
