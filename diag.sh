#!/bin/bash
# Salveaza HTML-ul brut de la sursa, ca sa poata fi reparat parserul.
cd "$(dirname "$0")"
source .venv/bin/activate
mkdir -p test
python - <<'PY'
import requests, pathlib
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
r = requests.get("https://new.firme-on-line.ro/", headers={"User-Agent": UA}, timeout=30)
p = pathlib.Path("test/onrc_raw.html")
p.write_text(r.text, encoding="utf-8")
print("status:", r.status_code, "| octeti:", len(r.text))
print("linkuri /profile/ in sursa:", r.text.count("/profile/"))
print("salvat in", p)
PY
