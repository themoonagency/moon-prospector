"""Muta prospectii din baza locala SQLite in Postgres/Supabase.

Rulare:
    source .venv/bin/activate
    python migreaza_in_supabase.py
Citeste DATABASE_URL din .env. Nu sterge nimic din baza locala.
"""
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import moon  # incarca .env
from moon import db

SURSA = pathlib.Path(os.getenv("MOON_DB_PATH", "moon_prospector.db"))


def main() -> int:
    if not db.dsn():
        print("Lipseste DATABASE_URL in .env. Pune sirul de conexiune Supabase si reia.")
        return 1
    if not SURSA.exists():
        print(f"Nu gasesc baza locala {SURSA}.")
        return 1

    loc = sqlite3.connect(str(SURSA))
    loc.row_factory = sqlite3.Row
    randuri = [dict(r) for r in loc.execute("SELECT * FROM prospecti").fetchall()]
    negre = [dict(r) for r in loc.execute("SELECT * FROM blacklist").fetchall()]
    loc.close()
    print(f"Local: {len(randuri)} prospecti, {len(negre)} numere in blacklist")

    db.initializeaza()
    mutati = sarite = 0
    with db.conexiune() as con:
        for r in randuri:
            r = {k: v for k, v in r.items() if v is not None}
            if db.upsert_prospect(con, r):
                mutati += 1
            else:
                sarite += 1
        for b in negre:
            db.adauga_blacklist(con, b["telefon"], b.get("motiv") or "")
        db.set_stare(con, "ultim_cui",
                     max((r["cui"] for r in randuri), default=0))

    with db.conexiune() as con:
        total = sum(db.sumar(con).values())
    print(f"Mutati: {mutati} · existau deja: {sarite}")
    print(f"Total in Supabase acum: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
