#!/bin/bash
# Pune cheia Google Places in .env, fara sa fie nevoie sa deschizi fisiere ascunse.
cd "$(dirname "$0")"
echo
read -p "Lipeste cheia Google Places si apasa Enter: " K
K="$(echo "$K" | tr -d '[:space:]')"
if [ -z "$K" ]; then echo "Nu ai introdus nimic."; exit 1; fi
touch .env
# scoate linia veche, daca exista, si o pune pe cea noua
grep -v '^GOOGLE_PLACES_API_KEY=' .env > .env.tmp 2>/dev/null || true
mv .env.tmp .env
echo "GOOGLE_PLACES_API_KEY=$K" >> .env
echo "Salvat in .env (${#K} caractere)."
echo
echo "Verific cheia..."
source .venv/bin/activate
python -m moon.pipeline test-google
