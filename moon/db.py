"""Baza de date. SQLite implicit; Postgres/Supabase dacă există DATABASE_URL.

Ambele dialecte folosesc același SQL: stratul de mai jos traduce parametrii
(? pentru SQLite, %s pentru Postgres) și diferențele de schemă.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

CALE_IMPLICITA = Path(os.getenv("MOON_DB_PATH", "moon_prospector.db"))


def dsn() -> Optional[str]:
    d = os.getenv("DATABASE_URL") or os.getenv("MOON_DATABASE_URL")
    return d if d and d.startswith(("postgres://", "postgresql://")) else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS prospecti (
    cui                INTEGER PRIMARY KEY,
    denumire           TEXT NOT NULL,
    nr_reg_com         TEXT,
    data_inregistrare  TEXT,
    caen               TEXT,
    caen_denumire      TEXT,
    tier               TEXT,
    serviciu_moon      TEXT,
    adresa             TEXT,
    judet              TEXT,
    localitate         TEXT,
    telefon_brut       TEXT,
    telefon            TEXT,
    este_mobil         INTEGER DEFAULT 0,
    stare_anaf         TEXT,
    administrator      TEXT,
    email              TEXT,
    scenariu           TEXT DEFAULT 'neverificat',
    are_fisa_google    INTEGER DEFAULT 0,
    place_id           TEXT,
    website            TEXT,
    recenzii           INTEGER DEFAULT 0,
    varianta_mesaj     TEXT,
    status             TEXT NOT NULL DEFAULT 'nou',
    mesaj              TEXT,
    data_colectare     TEXT NOT NULL,
    data_trimitere     TEXT,
    data_raspuns       TEXT,
    note               TEXT
);

CREATE TABLE IF NOT EXISTS blacklist (
    telefon TEXT PRIMARY KEY,
    motiv   TEXT,
    data    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stare (
    cheie   TEXT PRIMARY KEY,
    valoare TEXT
);

CREATE TABLE IF NOT EXISTS jurnal (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pornit_la      TEXT NOT NULL,
    cui_min        INTEGER,
    cui_max        INTEGER,
    interogate     INTEGER DEFAULT 0,
    gasite         INTEGER DEFAULT 0,
    in_lista_alba  INTEGER DEFAULT 0,
    cu_mobil       INTEGER DEFAULT 0,
    verificate_google INTEGER DEFAULT 0,
    adaugate       INTEGER DEFAULT 0,
    eroare         TEXT
);
"""

INDECSI = """
CREATE INDEX IF NOT EXISTS idx_prospecti_status  ON prospecti(status);
CREATE INDEX IF NOT EXISTS idx_prospecti_tier    ON prospecti(tier);
CREATE INDEX IF NOT EXISTS idx_prospecti_telefon ON prospecti(telefon);
"""

STATUSURI = ("nou", "mesaj_generat", "trimis", "raspuns", "client", "respins", "blacklist")


class _Con:
    """Înveliș subțire peste sqlite3 / psycopg, cu aceeași interfață."""

    def __init__(self, brut, pg: bool):
        self._c, self.pg = brut, pg

    def execute(self, sql: str, params: tuple | list = ()):
        if self.pg:
            sql = sql.replace("?", "%s")
            cur = self._c.cursor()
            cur.execute(sql, tuple(params))
            return cur
        return self._c.execute(sql, tuple(params))

    def executescript(self, sql: str):
        if self.pg:
            sql = (sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                      .replace("INTEGER PRIMARY KEY", "BIGINT PRIMARY KEY"))
            cur = self._c.cursor()
            for bucata in filter(str.strip, sql.split(";")):
                cur.execute(bucata)
            return
        self._c.executescript(sql)

    def commit(self):
        self._c.commit()

    def close(self):
        self._c.close()


# Pe Postgres refolosim o singura conexiune: Streamlit reruleaza scriptul la
# fiecare click, iar deschiderea unei conexiuni noi de fiecare data ar face
# interfata greoaie.
_PG = {"con": None}


def _pg_conexiune(d: str):
    import psycopg
    from psycopg.rows import dict_row
    c = _PG.get("con")
    if c is not None and not c.closed:
        try:
            c.execute("SELECT 1")
            return c
        except Exception:
            try:
                c.close()
            except Exception:
                pass
    _PG["con"] = psycopg.connect(d, row_factory=dict_row, autocommit=False,
                                 connect_timeout=15)
    return _PG["con"]


@contextmanager
def conexiune(cale: Optional[Path] = None):
    d = dsn()
    if d:
        brut = _pg_conexiune(d)
        con = _Con(brut, pg=True)
        try:
            yield con
            con.commit()
        except Exception:
            try:
                brut.rollback()
            except Exception:
                pass
            raise
        return                      # conexiunea Postgres ramane deschisa
    brut = sqlite3.connect(str(cale or CALE_IMPLICITA))
    brut.row_factory = sqlite3.Row
    con = _Con(brut, pg=False)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _coloane(con, tabel: str) -> set:
    if con.pg:
        r = con.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name=?", (tabel,)).fetchall()
        return {x["column_name"] for x in r}
    return {x[1] for x in con.execute(f"PRAGMA table_info({tabel})").fetchall()}


def _coloane_din_schema() -> Dict[str, List[tuple]]:
    """Citește SCHEMA și returnează {tabel: [(coloana, tip), ...]}."""
    out: Dict[str, List[tuple]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        tabel, corp = m.group(1), m.group(2)
        col = []
        for linie in corp.split("\n"):
            linie = linie.strip().rstrip(",")
            if not linie or linie.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")):
                continue
            parti = linie.split(None, 1)
            if len(parti) != 2:
                continue
            nume, tip = parti[0], parti[1]
            # ALTER TABLE nu acceptă PRIMARY KEY sau NOT NULL fără default
            tip = re.sub(r"\bPRIMARY KEY( AUTOINCREMENT)?\b", "", tip, flags=re.I)
            tip = re.sub(r"\bNOT NULL\b", "", tip, flags=re.I).strip()
            col.append((nume, tip or "TEXT"))
        out[tabel] = col
    return out


def _migreaza(con) -> List[str]:
    """Adaugă pe bazele existente orice coloană apărută între timp în SCHEMA."""
    adaugate = []
    for tabel, coloane in _coloane_din_schema().items():
        existente = _coloane(con, tabel)
        if not existente:
            continue
        for nume, tip in coloane:
            if nume not in existente:
                con.execute(f"ALTER TABLE {tabel} ADD COLUMN {nume} {tip}")
                adaugate.append(f"{tabel}.{nume}")
    return adaugate


def initializeaza(cale: Optional[Path] = None) -> None:
    with conexiune(cale) as con:
        con.executescript(SCHEMA)   # tabelele (doar cele care lipsesc)
        _migreaza(con)              # coloanele apărute după versiunea instalată
        con.executescript(INDECSI)  # indecșii, după ce coloanele există


def get_stare(con, cheie: str) -> Optional[str]:
    r = con.execute("SELECT valoare FROM stare WHERE cheie=?", (cheie,)).fetchone()
    return r["valoare"] if r else None


def set_stare(con, cheie: str, valoare: Any) -> None:
    con.execute("INSERT INTO stare(cheie,valoare) VALUES(?,?) "
                "ON CONFLICT(cheie) DO UPDATE SET valoare=excluded.valoare",
                (cheie, str(valoare)))


def e_blacklistat(con, telefon: Optional[str]) -> bool:
    if not telefon:
        return False
    return con.execute("SELECT 1 FROM blacklist WHERE telefon=?", (telefon,)).fetchone() is not None


def adauga_blacklist(con, telefon: str, motiv: str = "") -> None:
    con.execute("INSERT INTO blacklist(telefon,motiv,data) VALUES(?,?,?) "
                "ON CONFLICT(telefon) DO UPDATE SET motiv=excluded.motiv",
                (telefon, motiv, datetime.now().isoformat(timespec="seconds")))


def telefon_deja_folosit(con, telefon: Optional[str]) -> bool:
    """Același om poate deschide mai multe firme. Nu-l contactăm de două ori."""
    if not telefon:
        return False
    return con.execute("SELECT 1 FROM prospecti WHERE telefon=?", (telefon,)).fetchone() is not None


def upsert_prospect(con, p: Dict[str, Any]) -> bool:
    if con.execute("SELECT 1 FROM prospecti WHERE cui=?", (p["cui"],)).fetchone():
        return False
    coloane = ", ".join(p.keys())
    semne = ", ".join("?" for _ in p)
    con.execute(f"INSERT INTO prospecti ({coloane}) VALUES ({semne})", list(p.values()))
    return True


def prospecti(con, status: Optional[str] = None, tier: Optional[str] = None,
              limita: int = 200) -> List[dict]:
    q = "SELECT * FROM prospecti WHERE 1=1"
    params: List[Any] = []
    if status:
        q += " AND status=?"; params.append(status)
    if tier:
        q += " AND tier=?"; params.append(tier)
    q += " ORDER BY tier ASC, data_inregistrare DESC, cui DESC LIMIT ?"
    params.append(limita)
    return [dict(r) for r in con.execute(q, params).fetchall()]


def seteaza_status(con, cui: int, status: str, mesaj: Optional[str] = None,
                   varianta: Optional[str] = None) -> None:
    if status not in STATUSURI:
        raise ValueError(f"status necunoscut: {status}")
    acum = datetime.now().isoformat(timespec="seconds")
    camp_data = {"trimis": "data_trimitere", "raspuns": "data_raspuns"}.get(status)
    seturi, vals = ["status=?"], [status]
    if mesaj is not None:
        seturi.append("mesaj=?"); vals.append(mesaj)
    if varianta is not None:
        seturi.append("varianta_mesaj=?"); vals.append(varianta)
    if camp_data:
        seturi.append(f"{camp_data}=?"); vals.append(acum)
    vals.append(cui)
    con.execute(f"UPDATE prospecti SET {', '.join(seturi)} WHERE cui=?", vals)


def seteaza_prezenta(con, cui: int, pz) -> None:
    """Salveaza ce a gasit Google pentru un prospect deja existent."""
    con.execute(
        "UPDATE prospecti SET scenariu=?, are_fisa_google=?, place_id=?, "
        "website=COALESCE(?, website), recenzii=? WHERE cui=?",
        (pz.scenariu, int(pz.are_fisa), pz.place_id, pz.website, pz.recenzii, cui))


def neverificati(con, limita: int = 500) -> List[dict]:
    """Prospectii pe care nu i-am cautat inca pe Google."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM prospecti WHERE (scenariu IS NULL OR scenariu='neverificat') "
        "AND status='nou' ORDER BY tier ASC, cui DESC LIMIT ?", (limita,)).fetchall()]


def sumar(con) -> Dict[str, int]:
    rows = con.execute("SELECT status, COUNT(*) n FROM prospecti GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


# ------------------------------------------------------------------ statistici
RASPUNSURI = ("raspuns", "client")


def _rata(con, camp: str, filtru: str = "") -> List[dict]:
    q = f"""SELECT {camp} AS grup,
                   COUNT(*) AS trimise,
                   SUM(CASE WHEN status IN ('raspuns','client') THEN 1 ELSE 0 END) AS raspunsuri,
                   SUM(CASE WHEN status='client' THEN 1 ELSE 0 END) AS clienti
            FROM prospecti
            WHERE status IN ('trimis','raspuns','client','respins') {filtru}
            GROUP BY {camp} ORDER BY trimise DESC"""
    out = []
    for r in con.execute(q).fetchall():
        d = dict(r)
        d["rata"] = round(100.0 * (d["raspunsuri"] or 0) / d["trimise"], 1) if d["trimise"] else 0.0
        out.append(d)
    return out


def rata_pe_nisa(con):    return _rata(con, "caen_denumire")
def rata_pe_tier(con):    return _rata(con, "tier")
def rata_pe_judet(con):   return _rata(con, "judet")
def rata_pe_varianta(con):
    return _rata(con, "varianta_mesaj", "AND varianta_mesaj IS NOT NULL")
def rata_pe_scenariu(con): return _rata(con, "scenariu")
