#!/bin/bash
# Trimite magazine EXISTENTE (dupa cod CAEN) in MOON Chat, care le face demo pe catalogul lor.
# Foloseste:  ./moonchat.sh            -> 25 de demo-uri, coduri de magazine
#             ./moonchat.sh 10         -> 10 demo-uri
#             ./moonchat.sh 10 servicii -> coduri de servicii (site de prezentare)
cd "$(dirname "$0")"
[ -d .venv ] || { echo "Rulez intai instalarea..."; ./instaleaza.sh || exit 1; }
source .venv/bin/activate
[ -f .env ] && set -a && . ./.env && set +a
if [ -z "$MOONCHAT_KEY" ]; then
  echo "Lipseste MOONCHAT_KEY in .env - aceeasi valoare ca PROSPECT_KEY din worker."
  exit 1
fi
if [ "$2" = "servicii" ]; then
  python -m moon.moonchat --cate "${1:-25}" --servicii
else
  python -m moon.moonchat --cate "${1:-25}"
fi
