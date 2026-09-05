#!/bin/bash
# Construieste DATABASE_URL pentru Supabase si testeaza conexiunea.
#
# Nimic nu e scris in acest fisier: sirul de conexiune si parola se cer la
# rulare si ajung doar in .env, care nu se urca pe GitHub.
#
# De unde iei sirul: Supabase -> proiect -> Connect -> Direct / Connection string
#   -> Connection Method: "Session pooler"  (NU "Direct connection": aceea
#      merge doar pe IPv6, iar GitHub Actions si Streamlit Cloud sunt IPv4)
#   -> copiaza sirul, cu tot cu [YOUR-PASSWORD]
cd "$(dirname "$0")"

echo
echo "Lipeste sirul de conexiune Supabase (Session pooler)."
echo "Arata asa: postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-...pooler.supabase.com:5432/postgres"
echo
read -p "Sir: " SIR
SIR="$(echo "$SIR" | tr -d '[:space:]')"
case "$SIR" in
  postgres://*|postgresql://*) ;;
  *) echo "Nu pare un sir de conexiune Postgres."; exit 1 ;;
esac
if ! echo "$SIR" | grep -q "pooler.supabase.com"; then
  echo
  echo "ATENTIE: sirul nu contine 'pooler.supabase.com'."
  echo "Probabil ai copiat 'Direct connection', care merge doar pe IPv6."
  read -p "Continui oricum? (d/N): " R
  [ "$R" = "d" ] || [ "$R" = "D" ] || exit 1
fi

echo
read -s -p "Parola bazei de date: " PAROLA
echo
if [ -z "$PAROLA" ]; then echo "N-ai introdus nimic."; exit 1; fi

source .venv/bin/activate

URL=$(SIR="$SIR" PAROLA="$PAROLA" python - <<'PY'
import os, re, urllib.parse
sir = os.environ["SIR"]
enc = urllib.parse.quote(os.environ["PAROLA"], safe="")
# inlocuim marcajul de parola, oricare din formele folosite de Supabase
sir = re.sub(r"\[YOUR-PASSWORD\]|\[YOUR_PASSWORD\]|:\[.*?\]@", lambda m: enc if "[" in m.group(0) and m.group(0).startswith("[") else ":" + enc + "@", sir)
if enc not in sir:                      # sirul n-avea marcaj: il punem noi
    sir = re.sub(r"://([^:/@]+)(:[^@]*)?@", r"://\1:" + enc + "@", sir, count=1)
if "sslmode=" not in sir:
    sir += ("&" if "?" in sir else "?") + "sslmode=require"
print(sir)
PY
)

touch .env
grep -v '^DATABASE_URL=' .env > .env.tmp 2>/dev/null || true
mv .env.tmp .env
echo "DATABASE_URL=$URL" >> .env
echo "Salvat in .env."
echo
echo "Testez conexiunea..."
python - <<'PY'
import sys; sys.path.insert(0, ".")
import moon
from moon import db
if not db.dsn():
    print("DATABASE_URL nu a fost citit. Verifica .env."); sys.exit(1)
try:
    db.initializeaza()
    with db.conexiune() as con:
        v = con.execute("SELECT version()").fetchone()
        s = db.sumar(con)
    print("CONECTAT.")
    print(" ", list(v.values())[0][:60])
    print("  prospecti in Supabase:", sum(s.values()))
except Exception as e:
    m = str(e).lower()
    print("A esuat:", type(e).__name__, str(e)[:250])
    print()
    if "password authentication" in m:
        print("=> Parola gresita. Reseteaz-o: Supabase -> Connect -> Reset database password.")
    elif "could not translate" in m or "timeout" in m or "unreachable" in m or "network" in m:
        print("=> Host inaccesibil. Ai copiat 'Direct connection' in loc de 'Session pooler'?")
    sys.exit(1)
PY
