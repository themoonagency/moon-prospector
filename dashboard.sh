#!/bin/bash
# Porneste dashboard-ul. Foloseste: ./dashboard.sh
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Rulez intai instalarea..."; ./instaleaza.sh || exit 1; }
source .venv/bin/activate
streamlit run app.py
