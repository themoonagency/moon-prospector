"""Orchestrarea colectarii: ONRC -> enumerare CUI -> ANAF -> filtru CAEN -> baza de date.

Rulare:
    python -m moon.pipeline colectare
    python -m moon.pipeline colectare --tiers A --max-varsta 3
    python -m moon.pipeline sumar
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional

from . import anaf, contacte, db, firmeapi, onrc, places
from .caen import ListaAlba
from .cui import enumera

# Cat de departe sub cel mai mic CUI vazut mai cautam, ca sa nu ratam
# inregistrarile care nu au aparut inca pe pagina publica.
MARJA_INAPOI = 400


# ANAF foloseste diacriticele vechi cu sedila (Ş Ţ). Le aducem la forma
# corecta cu virgula (Ș Ț) inainte de orice potrivire.
_SEDILA = str.maketrans({"\u015e": "\u0218", "\u015f": "\u0219",
                         "\u0162": "\u021a", "\u0163": "\u021b"})

_RE_JUDET = re.compile(r"JUD\.?\s*([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ \-]*?)\s*(?:,|$)")
# Ordinea conteaza: preferam municipiul/orasul, apoi comuna, apoi satul.
_RE_LOC = [
    re.compile(r"\b(?:MUN\.?|MUNICIPIUL)\s+([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 \-\.]*?)\s*(?:,|$)"),
    re.compile(r"\b(?:ORAȘ(?:UL)?|ORAS(?:UL)?|ORȘ\.?|ORS\.?)\s+([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 \-\.]*?)\s*(?:,|$)"),
    re.compile(r"\bCOM\.?\s+([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 \-\.]*?)\s*(?:,|$)"),
    re.compile(r"\bSAT\s+([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 \-\.]*?)\s*(?:,|COM\.|$)"),
]


def _judet_localitate(adresa: str) -> tuple[Optional[str], Optional[str]]:
    """Sparge adresa ANAF in judet si localitate."""
    if not adresa:
        return None, None
    a = adresa.translate(_SEDILA).upper()

    judet = localitate = None
    m = _RE_JUDET.search(a)
    if m:
        judet = m.group(1).strip().title()
    elif a.startswith("MUNICIPIUL BUCUREȘTI") or a.startswith("BUCUREȘTI"):
        judet = "București"

    for rx in _RE_LOC:
        m = rx.search(a)
        if m:
            localitate = m.group(1).strip(" .").title()
            break

    if judet == "București":
        m = re.search(r"SECTOR\s*(\d)", a)
        localitate = f"Sector {m.group(1)}" if m else "București"
    return judet, localitate


# Intrari care NU sunt firme noi, desi apar in lista zilei.
_EXCLUSE = re.compile(
    r"SEDIU SECUNDAR|PUNCT DE LUCRU|SUCURSAL|FILIAL|"
    r"ASOCIA[TȚ]I|FUNDA[TȚ]I|FEDERA[TȚ]I|SINDICAT|PAROHI|CULTUL|"
    r"UNIUNEA|LIGA |CLUBUL SPORTIV|PARTIDUL", re.I)


def _de_ignorat(denumire: str) -> bool:
    """Sedii secundare ale unor firme existente si entitati non-profit."""
    return bool(_EXCLUSE.search(denumire or ""))


def _prea_veche(data_inreg: Optional[str], max_zile: int) -> bool:
    if not data_inreg:
        return True
    try:
        d = datetime.strptime(data_inreg[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (date.today() - d).days > max_zile


def _din_firmeapi(f) -> anaf.Firma:
    """FirmaAPI -> aceeași structură ca cea de la ANAF, ca restul fluxului să nu se schimbe."""
    return anaf.Firma(cui=f.cui, denumire=f.denumire, adresa=f.adresa or "",
                      telefon=f.telefon, caen=f.caen,
                      data_inregistrare=f.data_inregistrare, stare=f.stare,
                      nr_reg_com=f.nr_reg_com,
                      judet=(f.judet or None), localitate=(f.localitate or None))


def _descopera_firmeapi(lista, tiers: str, max_varsta: int, log) -> dict:
    """Descoperire prin API-ul firmeapi.ro: filtrează pe CAEN din start."""
    from datetime import date as _date
    coduri = [n.cod for n in lista._nise.values()
              if n.tier in {t.strip().upper() for t in tiers.split(",") if t.strip()}]
    start = _date.today() - timedelta(days=max_varsta)
    log(f"1/3  firmeapi.ro: {len(coduri)} coduri CAEN, "
        f"{start.isoformat()} -> {_date.today().isoformat()}")
    firme = firmeapi.firme_noi(coduri, start, _date.today(), doar_cu_telefon=True,
                               verbose=lambda m: log(m))
    return {c: _din_firmeapi(f) for c, f in firme.items()}


def colectare(tiers: str = "A,B", max_varsta: int = 7, pauza: float = 1.2,
              doar_mobil: bool = True, verifica_google: bool = True,
              sursa: str = "auto", verbose: bool = True) -> dict:
    lista = ListaAlba()
    db.initializeaza()
    jurnal = {"interogate": 0, "gasite": 0, "in_lista_alba": 0, "cu_mobil": 0,
              "verificate_google": 0, "sarite_duplicat": 0, "sarite_nefirma": 0,
              "adaugate": 0}

    def log(*a):
        if verbose:
            print(*a, flush=True)

    if sursa == "auto":
        sursa = "firmeapi" if firmeapi.activ() else "onrc"

    cui_min = cui_max = None
    if sursa == "firmeapi":
        firme = _descopera_firmeapi(lista, tiers, max_varsta, log)
        jurnal["gasite"] = len(firme)
    else:
        log("1/4  Citesc lista publica de firme noi...")
        intrari = onrc.descarca()
        interval = onrc.interval_cui(intrari)
        if not interval:
            raise RuntimeError("Nu am gasit nicio firma pe pagina sursa.")
        cui_min_pagina, cui_max = interval
        log(f"     {len(intrari)} firme pe pagina, CUI {cui_min_pagina}-{cui_max}")

        with db.conexiune() as con:
            vazut = db.get_stare(con, "ultim_cui")
        cui_min = max(int(vazut) + 1, cui_min_pagina - MARJA_INAPOI) if vazut \
            else cui_min_pagina - MARJA_INAPOI

        candidati = list(enumera(cui_min, cui_max))
        jurnal["interogate"] = len(candidati)
        log(f"2/4  {len(candidati)} CUI-uri valide de verificat "
            f"({len(candidati) - len(intrari)} peste ce arata pagina)")

        log("3/4  Interoghez ANAF...")
        firme = anaf.interogheaza(candidati, pauza=pauza)
        jurnal["gasite"] = len(firme)
        log(f"     {len(firme)} firme existente in ANAF")

    cu_google = verifica_google and bool(places.cheie())
    log("4/4  Filtrez si salvez..." + ("" if cu_google else
        "  (fara verificare Google - lipseste GOOGLE_PLACES_API_KEY)"))
    acum = datetime.now().isoformat(timespec="seconds")
    with db.conexiune() as con:
        for cui, f in sorted(firme.items()):
            if not f.activa or _prea_veche(f.data_inregistrare, max_varsta):
                continue
            if _de_ignorat(f.denumire):
                jurnal["sarite_nefirma"] = jurnal.get("sarite_nefirma", 0) + 1
                continue
            nisa = lista.accepta(f.caen, tiers)
            if not nisa:
                continue
            jurnal["in_lista_alba"] += 1

            tel = anaf.normalizeaza_telefon(f.telefon)
            mobil = anaf.este_mobil(tel)
            if mobil:
                jurnal["cu_mobil"] += 1
            if doar_mobil and not mobil:
                continue
            if db.e_blacklistat(con, tel):
                continue
            # Acelasi administrator poate deschide mai multe firme cu acelasi numar.
            if db.telefon_deja_folosit(con, tel):
                jurnal["sarite_duplicat"] += 1
                continue

            # firmeapi da judetul si localitatea direct; ANAF nu, deci le deducem
            judet, localitate = _judet_localitate(f.adresa)
            judet = getattr(f, "judet", None) or judet
            localitate = getattr(f, "localitate", None) or localitate

            # Verificarea pe Google se face DUPA toate filtrele gratuite,
            # ca sa nu cheltuim apeluri pe prospecti pe care oricum ii aruncam.
            pz = places.Prezenta(verificat=False)
            if cu_google:
                pz = places.verifica(f.denumire, localitate or "", judet or "")
                if pz.verificat:
                    jurnal["verificate_google"] += 1

            ct = contacte.imbogateste(f.cui) if contacte.activ() else contacte.Contact()

            nou = db.upsert_prospect(con, {
                "cui": f.cui,
                "denumire": f.denumire,
                "nr_reg_com": f.nr_reg_com,
                "data_inregistrare": f.data_inregistrare,
                "caen": f.caen,
                "caen_denumire": nisa.denumire,
                "tier": nisa.tier,
                "serviciu_moon": nisa.serviciu,
                "adresa": f.adresa,
                "judet": judet,
                "localitate": localitate,
                "telefon_brut": f.telefon,
                "telefon": tel,
                "este_mobil": int(mobil),
                "stare_anaf": f.stare,
                "administrator": ct.administrator,
                "email": ct.email,
                "scenariu": pz.scenariu,
                "are_fisa_google": int(pz.are_fisa),
                "place_id": pz.place_id,
                "website": pz.website or ct.website,
                "recenzii": pz.recenzii,
                "status": "nou",
                "data_colectare": acum,
            })
            jurnal["adaugate"] += int(nou)

        if cui_max:
            db.set_stare(con, "ultim_cui", cui_max)
        con.execute(
            "INSERT INTO jurnal(pornit_la,cui_min,cui_max,interogate,gasite,"
            "in_lista_alba,cu_mobil,verificate_google,adaugate) VALUES(?,?,?,?,?,?,?,?,?)",
            (acum, cui_min, cui_max, jurnal["interogate"], jurnal["gasite"],
             jurnal["in_lista_alba"], jurnal["cu_mobil"],
             jurnal["verificate_google"], jurnal["adaugate"]),
        )

    log(f"\nGata: {jurnal['in_lista_alba']} in lista alba, {jurnal['cu_mobil']} cu mobil, "
        f"{jurnal['sarite_duplicat']} sarite (acelasi telefon), "
        f"{jurnal['verificate_google']} verificate pe Google, "
        f"{jurnal['adaugate']} adaugate ca prospecti noi.")
    return jurnal


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="moon.pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("colectare", help="colecteaza firmele noi de azi")
    c.add_argument("--tiers", default=os.getenv("MOON_TIERS", "A,B"))
    c.add_argument("--max-varsta", type=int, default=int(os.getenv("MOON_MAX_AGE_DAYS", 7)))
    c.add_argument("--pauza", type=float, default=float(os.getenv("MOON_ANAF_SLEEP", 1.2)))
    c.add_argument("--toate-telefoanele", action="store_true",
                   help="pastreaza si fixele, nu doar mobilele")
    c.add_argument("--fara-google", action="store_true",
                   help="nu verifica prezenta pe Google (economiseste apeluri Places)")
    c.add_argument("--sursa", choices=("auto", "onrc", "firmeapi"), default="auto",
                   help="de unde vin firmele noi (auto = firmeapi daca ai cheie)")

    v = sub.add_parser("verifica",
                       help="cauta pe Google prospectii deja colectati, dar neverificati")
    v.add_argument("--limita", type=int, default=500)

    sub.add_parser("sumar", help="cati prospecti sunt si in ce stadiu")
    sub.add_parser("statistici", help="rata de raspuns pe nisa, tier si varianta de mesaj")

    g = sub.add_parser("test-google", help="verifica cheia Google Places pe o firma reala")
    g.add_argument("--firma", default="Dedeman Bucuresti")

    t = sub.add_parser("test-firmeapi", help="verifica cheia firmeapi si arata raspunsul brut")
    t.add_argument("--caen", default="8623")
    t.add_argument("--zile", type=int, default=7)

    a = p.parse_args(argv)
    if a.cmd == "colectare":
        colectare(tiers=a.tiers, max_varsta=a.max_varsta, pauza=a.pauza,
                  doar_mobil=not a.toate_telefoanele,
                  verifica_google=not a.fara_google, sursa=a.sursa)
    elif a.cmd == "sumar":
        db.initializeaza()
        with db.conexiune() as con:
            s = db.sumar(con)
            total = sum(s.values())
            print(f"Total prospecti: {total}")
            for k, v in sorted(s.items()):
                print(f"  {k:15s} {v}")
    elif a.cmd == "verifica":
        import time as _time
        db.initializeaza()
        if not places.cheie():
            print("Lipseste GOOGLE_PLACES_API_KEY (pune-l cu ./cheie.sh).")
            return 1
        with db.conexiune() as con:
            de_facut = db.neverificati(con, a.limita)
        if not de_facut:
            print("Toti prospectii sunt deja verificati.")
            return 0
        print(f"{len(de_facut)} de verificat pe Google "
              f"(~{len(de_facut)} apeluri din cota lunara)...\n")
        numar = {"nimic": 0, "fisa_slaba": 0, "are_tot": 0, "neverificat": 0}
        for i, r in enumerate(de_facut, 1):
            pz = places.verifica(r["denumire"], r.get("localitate") or "",
                                 r.get("judet") or "")
            numar[pz.scenariu] = numar.get(pz.scenariu, 0) + 1
            if pz.verificat:
                with db.conexiune() as con:
                    db.seteaza_prezenta(con, r["cui"], pz)
            print(f"  {i:3d}/{len(de_facut)}  {pz.scenariu:12s} {r['denumire'][:44]}")
            _time.sleep(0.2)
        print(f"\nGata: {numar['nimic']} fara fisa · {numar['fisa_slaba']} fisa fara site · "
              f"{numar['are_tot']} au si fisa si site · {numar['neverificat']} neverificate")
    elif a.cmd == "test-google":
        d = places.diagnostic(a.firma)
        if d["ok"]:
            pz = places.verifica(a.firma, "", "")
            print(f"Cheia functioneaza. Cautare: {a.firma!r}")
            print(f"  rezultate brute : {len(d['locuri'])}")
            print(f"  are fisa Google : {pz.are_fisa}")
            print(f"  nume gasit      : {pz.nume_gasit}")
            print(f"  site            : {pz.website}")
            print(f"  scenariu mesaj  : {pz.scenariu}")
            print(f"  nivel facturare : {'Enterprise' if 'rating' in places.campuri() else 'Pro (5.000 gratis/luna)'}")
            return 0
        print("NU merge inca.\n")
        if d["tip"] == "retea":
            print(d["mesaj"])
        elif d["tip"] == "fara_cheie":
            print(d["mesaj"])
        else:
            print(f"Google a raspuns HTTP {d.get('http')} {d.get('cod') or ''}")
            print(f"Mesaj: {d.get('mesaj')}\n")
            cod = (d.get("cod") or "") + " " + (d.get("mesaj") or "")
            if "SERVICE_DISABLED" in cod or "has not been used" in cod:
                print("=> Activeaza 'Places API (New)' pe proiectul cheii, in Google Cloud Console.")
            elif "PERMISSION_DENIED" in cod or "referer" in cod.lower() or "restrict" in cod.lower():
                print("=> Cheia are restrictii. Scoate restrictia de aplicatie (HTTP referrer / IP),")
                print("   sau adauga Places API in 'API restrictions'.")
            elif "BILLING" in cod.upper():
                print("=> Activeaza facturarea pe proiect (cardul e necesar si pentru tier-ul gratuit).")
            elif "API key not valid" in cod or "INVALID_ARGUMENT" in cod:
                print("=> Cheia pare gresita sau incompleta. Ruleaza din nou ./cheie.sh")
        return 1
    elif a.cmd == "test-firmeapi":
        import json
        from datetime import date as _date
        if not firmeapi.activ():
            print("Lipseste FIRMEAPI_KEY in mediu.")
            return 1
        start = _date.today() - timedelta(days=a.zile)
        try:
            brut = firmeapi.interogheaza_brut(
                {"caen": a.caen, "data_start": start.isoformat(),
                 "data_end": _date.today().isoformat(), "telefon": 1, "per_page": 5})
        except Exception as e:
            print(f"Apelul a esuat: {e}")
            return 1
        randuri = firmeapi._rezultate(brut)
        print(f"CAEN {a.caen}, ultimele {a.zile} zile: {len(randuri)} rezultate "
              f"(~{len(randuri) * firmeapi.CREDITE_PER_REZULTAT:.1f} credite)\n")
        print("Chei de nivel 1:", list(brut)[:12] if isinstance(brut, dict) else "lista")
        if randuri:
            print("\nPrimul rezultat, brut:")
            print(json.dumps(randuri[0], ensure_ascii=False, indent=2)[:1400])
            print("\nInterpretat:", firmeapi._to_firma(randuri[0]))
    elif a.cmd == "statistici":
        db.initializeaza()
        with db.conexiune() as con:
            for titlu, fn in (("TIER", db.rata_pe_tier), ("SCENARIU", db.rata_pe_scenariu),
                              ("NISA", db.rata_pe_nisa), ("JUDET", db.rata_pe_judet),
                              ("VARIANTA", db.rata_pe_varianta)):
                randuri = fn(con)
                if not randuri:
                    continue
                print(f"\n{titlu}")
                for r in randuri[:12]:
                    print(f"  {str(r['grup'])[:44]:44s} {r['trimise']:4d} trimise  "
                          f"{r['raspunsuri'] or 0:3d} raspunsuri  {r['rata']:5.1f}%  "
                          f"{r['clienti'] or 0} clienti")
    return 0


if __name__ == "__main__":
    sys.exit(main())
