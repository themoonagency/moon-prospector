#!/bin/bash
# Afiseaza secretele in formatul cerut de Streamlit Cloud, gata de copiat.
# Le vezi doar tu, in terminalul tau.
cd "$(dirname "$0")"
source .venv/bin/activate
python - <<'PY'
import pathlib, re
env = pathlib.Path(".env")
if not env.exists():
    print("Nu exista .env."); raise SystemExit(1)
vrem = ["DATABASE_URL", "GOOGLE_PLACES_API_KEY", "MOON_PAROLA", "FIRMEAPI_KEY"]
print("\n--- copiaza de aici ---\n")
for linie in env.read_text(encoding="utf-8").splitlines():
    if "=" not in linie or linie.strip().startswith("#"):
        continue
    k, v = linie.split("=", 1)
    k, v = k.strip(), v.strip().strip('"')
    if k in vrem and v:
        print(f'{k} = "{v}"')
print("\n--- pana aici ---\n")
print("Se lipeste in Streamlit: App settings -> Secrets.")
PY
