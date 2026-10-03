"""Orchestrarea colectarii: frontiera CUI -> ANAF lot cu lot -> filtru CAEN -> baza de date.

Lista ONRC (new.firme-on-line.ro) e doar un indiciu pentru capatul zilei: din
30 sept 2026 blocheaza IP-urile GitHub (403), asa ca in modul `auto` cadem pe
ANAF direct (moon/sondare.py). Firmele vin oricum TOATE de la ANAF.

Rulare:
    python -m moon.pipeline colectare
    python -m moon.pipeline colectare --tiers A --max-varsta 3
    python -m moon.pipeline colectare --din-setari      # setarile din panou
    python -m moon.pipeline sumar

Logurile GitHub Actions sunt PUBLICE: la stdout doar cifre (fara nume, telefoane, CUI-uri).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import anaf, contacte, db, firmeapi, mesaj, onrc, places, setari, sondare
from .caen import ListaAlba
from .cui import valid

# Doar la prima rulare (fara `ultim_cui` in baza): cat de departe sub cel mai
# mic CUI de pe pagina ONRC incepem.
MARJA_INAPOI = 400

# Limita ANAF e 1 cerere/secunda; sub atat nu coboram, orice ar zice --pauza.
PAUZA_MIN = 1.0
JURNAL_MAX = 500

# Semnatura ramane substituent in baza de date; interfata o inlocuieste
# la afisare, ca schimbarea ei sa nu ceara regenerarea mesajelor.
SEMN = "{{semnatura}}"


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
    coduri = lista.coduri(tiers)
    start = _date.today() - timedelta(days=max_varsta)
    log(f"1/3  firmeapi.ro: {len(coduri)} coduri CAEN, "
        f"{start.isoformat()} -> {_date.today().isoformat()}")
    firme = firmeapi.firme_noi(coduri, start, _date.today(), doar_cu_telefon=True,
                               verbose=lambda m: log(m))
    return {c: _din_firmeapi(f) for c, f in firme.items()}


class Probleme:
    """Ce a mers prost intr-o rulare, pe limba omului, pentru coloana `jurnal.eroare`.

    Conventie (o citesc dashboardul si alertele): textul incepe cu „Eroare: " cand
    rularea a esuat sau a ramas la jumatate si cu „Atenție: " cand a mers, dar e
    ceva de stiut (ex. a cazut pe ANAF direct). Fara probleme: NULL.
    """

    def __init__(self):
        self.erori, self.avert = [], []

    def eroare(self, m: str) -> None:
        if m not in self.erori:
            self.erori.append(m)

    def atentie(self, m: str) -> None:
        if m not in self.avert:
            self.avert.append(m)

    def text(self) -> Optional[str]:
        if self.erori:
            t = "Eroare: " + " · ".join(self.erori + self.avert)
        elif self.avert:
            t = "Atenție: " + " · ".join(self.avert)
        else:
            return None
        return t if len(t) <= JURNAL_MAX else t[:JURNAL_MAX - 1] + "…"


def _acum() -> str:
    """Ora UTC, ISO fara Z, la secunda (conventia bazei)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _int(x) -> Optional[int]:
    try:
        v = int(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _mesaj_anaf(e: Optional[BaseException]) -> str:
    if isinstance(e, anaf.AnafRefuza):
        if e.http in (401, 403, 451):
            return f"ANAF {e.motiv}"
        return f"ANAF {e.motiv} — poate s-a schimbat serviciul lor"
    if isinstance(e, anaf.AnafIndisponibil):
        return f"ANAF {e.motiv}"
    return f"ANAF n-a mers ({type(e).__name__})"


def _e_de_baza(e: BaseException) -> bool:
    return (type(e).__module__ or "").startswith(("psycopg", "sqlite3"))


_RE_COD_BAZA = re.compile(r"^[A-Z0-9_]{1,40}$")


def _cod_baza(e: BaseException) -> Optional[str]:
    """sqlstate-ul Postgres (ex. 23502) sau numele codului SQLite (ex. SQLITE_CONSTRAINT_NOTNULL).

    NICIODATA textul erorii: la Postgres el are `DETAIL: Failing row contains (…, denumire, adresa …)`.
    """
    for camp in ("sqlstate", "pgcode", "sqlite_errorname", "sqlite_errorcode"):
        v = getattr(e, camp, None)
        if v is not None and _RE_COD_BAZA.match(str(v)):
            return str(v)
    return None


def _eroare_de_baza(e: BaseException) -> str:
    """„NotNullViolation, cod 23502" — doar tipul si codul, pentru stdout si `jurnal.eroare`."""
    cod = _cod_baza(e)
    return type(e).__name__ + (f", cod {cod}" if cod else "")


def _mesaj_neprevazut(e: BaseException) -> str:
    if isinstance(e, KeyboardInterrupt):
        return "colectarea a fost oprită înainte de final"
    if _e_de_baza(e):
        return f"baza de date a dat eroare ({_eroare_de_baza(e)})"
    return f"eroare neprevăzută ({type(e).__name__}) — detaliile sunt în GitHub Actions"


_RE_ASCUNDE = [(re.compile(r"\d{5,}"), "…"),
               (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "…@…"),
               (re.compile(r"postgres\.[a-z0-9]+", re.I), "postgres.…")]


def _ascunde(text: str) -> str:
    """Logurile din Actions sunt publice: scoatem CUI-uri, telefoane, emailuri, userul bazei."""
    for rx, inlocuire in _RE_ASCUNDE:
        text = rx.sub(inlocuire, text)
    return text


_MARI = "A-ZĂÂÎȘŞȚŢ"
_RE_STRICT = [
    (re.compile(r"\?\S*"), "?…"),                                       # parametrii din URL-uri
    (re.compile(r"'[^']*'|\"[^\"]*\"|„[^”\"]*[”\"]|«[^»]*»"), "…"),    # valori intre ghilimele
    (re.compile(r"\([^()]*\)"), "(…)"),                                  # randuri / tupluri intre paranteze
    (re.compile(r"\d{3,}"), "…"),                                        # CUI, telefon, cod postal, numar
    (re.compile(rf"\b[{_MARI}][{_MARI}0-9.&-]*(?:[ ,]+[{_MARI}0-9][{_MARI}0-9.&-]*)+"), "…"),  # FIRMA EXEMPLU SRL, STR. LUNGA
]


def _ascunde_strict(text: str, maxim: int = 200) -> str:
    """Mesajul unei exceptii pentru logurile PUBLICE: doar primul rand (fara DETAIL / CONTEXT),
    fara valori intre ghilimele sau paranteze, fara cifre de 3+, fara siruri de cuvinte cu majuscule."""
    randuri = str(text or "").strip().splitlines()
    t = _ascunde(randuri[0] if randuri else "")
    for rx, inlocuire in _RE_STRICT:
        t = rx.sub(inlocuire, t)
    return t[:maxim]


def _unde(e: BaseException) -> str:
    """Unde a picat: fisier:rand in functie, cadru cu cadru (fara randurile de cod si fara valori)."""
    return "\n".join(f"  {os.path.basename(c.filename)}:{c.lineno} in {c.name}"
                     for c in traceback.extract_tb(e.__traceback__))


def _descrie_exceptia(e: BaseException) -> str:
    """Ce tiparim in Actions despre o exceptie: la baza doar tipul si codul, altfel mesajul mascat strict."""
    if _e_de_baza(e):
        return f"baza de date: {_eroare_de_baza(e)}"
    m = _ascunde_strict(str(e))
    return type(e).__name__ + (f": {m}" if m else "")


def colectare(tiers: Optional[str] = None, max_varsta: Optional[int] = None,
              pauza: float = 1.2, doar_mobil: bool = True, verifica_google: bool = True,
              sursa: Optional[str] = None, din_setari: bool = False,
              verbose: bool = True) -> dict:
    """Colecteaza firmele noi. Scrie MEREU un rand in `jurnal`, si cand ceva pica.

    tiers / max_varsta / sursa: None = din panou (cu din_setari), apoi din mediu, apoi implicit.
    Erorile asteptate (ANAF cazut, ONRC blocat) nu arunca: ajung in `rez["eroare"]`.
    Cele neprevazute se scriu in jurnal si apoi se arunca mai departe.
    """
    rez = {"interogate": 0, "gasite": 0, "in_lista_alba": 0, "cu_mobil": 0,
           "verificate_google": 0, "sarite_duplicat": 0, "sarite_nefirma": 0,
           "adaugate": 0, "cui_min": None, "cui_max": None, "eroare": None}
    pr = Probleme()
    pornit = _acum()

    def log(*a):
        if verbose:
            print(*a, flush=True)

    try:
        _colecteaza(rez, pr, log, tiers=tiers, max_varsta=max_varsta, pauza=pauza,
                    doar_mobil=doar_mobil, verifica_google=verifica_google,
                    sursa=sursa, din_setari=din_setari)
    except BaseException as e:
        pr.eroare(_mesaj_neprevazut(e))
        raise
    finally:
        rez["eroare"] = pr.text()
        try:
            with db.conexiune() as con:
                db.scrie_jurnal(con, {**rez, "pornit_la": pornit})
        except Exception as e:      # noqa: BLE001 - baza cazuta: macar sa se vada in Actions
            log(f"Nu am putut scrie nici in jurnal ({type(e).__name__}).")
        if rez["eroare"]:
            log(rez["eroare"])
    return rez


def _colecteaza(rez: dict, pr: Probleme, log, *, tiers, max_varsta, pauza, doar_mobil,
                verifica_google, sursa, din_setari) -> None:
    lista = ListaAlba()
    db.initializeaza()
    max_db = None
    with db.conexiune() as con:
        brut_setari = db.get_stare(con, "colectare_setari") if din_setari else None
        brut_override = db.get_stare(con, "caen_override")
        frontiera = _int(db.get_stare(con, "ultim_cui"))
        if not frontiera:
            max_db = db.max_cui_prospecti(con)

    panou = None
    if din_setari:
        x = setari.din_json(brut_setari)
        panou, gresite = setari.curata_setari(x)
        if gresite or (brut_setari and x is None):
            pr.atentie("setările de colectare din panou au valori greșite — "
                       "am folosit valorile implicite pentru ele")
    s, gresite_explicit = setari.rezolva({"tiers": tiers, "max_varsta": max_varsta, "sursa": sursa},
                                         panou, os.environ)
    if gresite_explicit:
        pr.atentie("am ignorat valori greșite date la pornire (" + ", ".join(gresite_explicit) + ")")
    tiers, max_varsta, sursa = s["tiers"], s["max_varsta"], s["sursa"]
    x = setari.din_json(brut_override)
    override, sarite = setari.curata_override(x)
    if sarite or (brut_override and x is None):
        pr.atentie("unele coduri CAEN din panou au valori greșite și au fost sărite")
    lista.aplica_override(override)
    pauza = max(PAUZA_MIN, float(pauza or 0))
    log(f"Setari: tier {tiers}, vechime maxima {max_varsta} zile, sursa {sursa}"
        + (f", {len(override)} coduri CAEN schimbate din panou" if override else ""))

    if sursa == "firmeapi":
        # Doar local (din GitHub Actions / panou nu se poate alege). Si aici nimic de la frontiera in jos:
        # ce a sters Felix din panou nu revine. Frontiera nu se muta (firmeapi nu vede toate CUI-urile).
        prag = frontiera or max_db
        firme = _descopera_firmeapi(lista, tiers, max_varsta, log)
        sub = sum(1 for c in firme if prag and c <= prag)
        if sub:
            log(f"     {sub} firme sub ultimul CUI stiut, sarite")
        rez["gasite"] = len(firme) - sub
        _salveaza(firme, prag, None, lista, rez, log, tiers=tiers, max_varsta=max_varsta,
                  doar_mobil=doar_mobil, verifica_google=verifica_google)
        return

    # 1. Capatul zilei din lista ONRC (doar indiciu; firmele vin oricum de la ANAF).
    indiciu = None
    if sursa in ("auto", "onrc"):
        log("1/4  Citesc lista ONRC de firme noi...")
        try:
            intrari = onrc.descarca()
            indiciu = onrc.interval_cui(intrari)
            if not indiciu:
                raise onrc.OnrcFaraFirme()
            log(f"     {len(intrari)} firme pe pagina")
        except Exception as e:      # noqa: BLE001 - orice problema cu lista -> ANAF direct
            m = onrc.motiv(e)
            if sursa == "onrc":
                pr.eroare(m)
                log(f"     {m}")
                return
            pr.atentie(m + " — am folosit ANAF direct")
            log(f"     {m} — trec pe ANAF direct")
    else:
        log("1/4  Sursa: doar ANAF (fara lista ONRC)")

    # 2. De unde pornim: de la frontiera de data trecuta, inclusiv (ea e proba ca ANAF
    #    raspunde normal). Sub ea nu adaugam nimic: ce s-a sters din panou nu revine.
    proba = None
    if frontiera:
        start = proba = frontiera
    elif indiciu:
        start = max(1, indiciu[0] - MARJA_INAPOI)
    elif max_db:
        start = proba = frontiera = max_db
    else:
        pr.eroare("nu știu de unde să încep: lipsește ultimul CUI și lista ONRC nu merge")
        return
    proba = proba if proba and valid(proba) else None
    capat_onrc = indiciu[1] if indiciu and indiciu[1] >= start else None

    log(f"2/4  Intreb ANAF lot cu lot ({sondare.MARIME_LOT} CUI-uri pe cerere; ma opresc dupa "
        f"{sondare.K_GOALE} loturi goale la rand, maxim {sondare.PLAFON_LOTURI} cereri)")
    sesiune = anaf.sesiune_noua()
    sj = sondare.sondeaza(start, lambda lot: anaf.interogheaza_lot(lot, sesiune=sesiune,
                                                                    pauza=pauza),
                          k=sondare.K_GOALE, plafon=sondare.PLAFON_LOTURI,
                          nu_te_opri_sub=capat_onrc, log=log)
    rez["cui_min"], rez["cui_max"], rez["interogate"] = sj.cui_min, sj.cui_max, sj.interogate
    noi = [c for c in sj.firme if not frontiera or c > frontiera]
    rez["gasite"] = len(noi)
    log(f"3/4  {sj.loturi} cereri ANAF, {sj.interogate} CUI-uri, {len(noi)} firme noi")

    if sj.motiv == "eroare":
        m = _mesaj_anaf(sj.eroare)
        if sj.loturi == 0:
            pr.eroare(m + " — n-am putut colecta nimic; reîncerc la rularea următoare")
            return
        else:
            pr.eroare(f"{m} după {sj.loturi} cereri — am salvat ce am găsit; "
                      "continui de aici la rularea următoare")
    elif sj.motiv == "plafon":
        pr.atentie(f"am ajuns la plafonul de {sondare.PLAFON_LOTURI} de cereri ANAF — "
                   "continui de aici la rularea următoare")
    if proba and sj.loturi and proba not in sj.firme:
        pr.atentie("ANAF nu mai găsește ultima firmă știută — verifică dacă s-a schimbat ceva la ANAF")
    if sj.motiv == "capat" and not noi:
        pr.atentie("nicio firmă nouă la ANAF de la rularea trecută")

    _salveaza(sj.firme, frontiera, sj.ultim_gasit, lista, rez, log, tiers=tiers,
              max_varsta=max_varsta, doar_mobil=doar_mobil, verifica_google=verifica_google)


def _salveaza(firme: dict, frontiera: Optional[int], noua_frontiera: Optional[int],
              lista: ListaAlba, jurnal: dict, log, *, tiers: str, max_varsta: int,
              doar_mobil: bool, verifica_google: bool) -> None:
    """Filtreaza si salveaza; muta frontiera in aceeasi tranzactie (totul sau nimic).

    Doar firmele de peste frontiera: frontiera insasi (proba) si ce e sub ea au
    fost tratate data trecuta, iar ce a sters Felix din panou nu trebuie sa revina.
    """
    cu_google = verifica_google and bool(places.cheie())
    log("4/4  Filtrez si salvez..." + ("" if cu_google else
        "  (fara verificare Google - lipseste GOOGLE_PLACES_API_KEY)"))
    acum = _acum()
    with db.conexiune() as con:
        for cui, f in sorted(firme.items()):
            if frontiera and cui <= frontiera:
                continue

            def numara(cheie: str) -> None:
                jurnal[cheie] = jurnal.get(cheie, 0) + 1

            if not f.activa or _prea_veche(f.data_inregistrare, max_varsta):
                continue
            if _de_ignorat(f.denumire):
                numara("sarite_nefirma")
                continue
            nisa = lista.accepta(f.caen, tiers)
            if not nisa:
                continue
            numara("in_lista_alba")

            tel = anaf.normalizeaza_telefon(f.telefon)
            mobil = anaf.este_mobil(tel)
            if mobil:
                numara("cu_mobil")
            if doar_mobil and not mobil:
                continue
            if db.e_blacklistat(con, tel):
                continue
            # Acelasi administrator poate deschide mai multe firme cu acelasi numar.
            if db.telefon_deja_folosit(con, tel):
                numara("sarite_duplicat")
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

            rand = {
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
            }

            # Mesajele se scriu ACUM, la colectare, ca dashboardul (worker
            # Cloudflare) sa nu poarte logica din mesaj.py. Semnatura ramane
            # substituent, ca sa se poata schimba din interfata.
            rand["mesaj_draft"], rand["varianta_mesaj"] = mesaj.compune(rand, SEMN)
            rand["mesaj_fu3"] = mesaj.compune_followup(rand, 3, SEMN)
            rand["mesaj_fu7"] = mesaj.compune_followup(rand, 7, SEMN)

            nou_in_baza = db.upsert_prospect(con, rand)
            jurnal["adaugate"] += int(nou_in_baza)

        if noua_frontiera and (not frontiera or noua_frontiera > frontiera):
            db.set_stare(con, "ultim_cui", noua_frontiera)

    log(f"\nGata: {jurnal['in_lista_alba']} in lista alba, {jurnal['cu_mobil']} cu mobil, "
        f"{jurnal['sarite_duplicat']} sarite (acelasi telefon), "
        f"{jurnal['verificate_google']} verificate pe Google, "
        f"{jurnal['adaugate']} adaugate ca prospecti noi.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="moon.pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("colectare", help="colecteaza firmele noi de azi")
    c.add_argument("--tiers", default=None,
                   help="A, A,B, A,B,C (lipsa = panou cu --din-setari, apoi MOON_TIERS, apoi A,B)")
    c.add_argument("--max-varsta", default=None,
                   help="vechimea maxima in zile (lipsa = panou / MOON_MAX_AGE_DAYS / 7)")
    c.add_argument("--pauza", type=float, default=float(os.getenv("MOON_ANAF_SLEEP", 1.2)))
    c.add_argument("--toate-telefoanele", action="store_true",
                   help="pastreaza si fixele, nu doar mobilele")
    c.add_argument("--fara-google", action="store_true",
                   help="nu verifica prezenta pe Google (economiseste apeluri Places)")
    c.add_argument("--sursa", default=None,
                   help="auto = lista ONRC, iar daca nu merge ANAF direct; anaf = doar ANAF; "
                        "onrc = doar cu lista ONRC; firmeapi = API-ul platit (onrc si firmeapi doar "
                        "local; in GitHub Actions se ignora). O valoare gresita se ignora, cu avertisment in jurnal")
    c.add_argument("--din-setari", action="store_true",
                   help="ia tier-urile, vechimea si sursa din panou (stare.colectare_setari); "
                        "ce dai explicit in linia de comanda castiga")

    v = sub.add_parser("verifica",
                       help="cauta pe Google prospectii deja colectati, dar neverificati")
    v.add_argument("--limita", type=int, default=500)

    sub.add_parser("sumar", help="cati prospecti sunt si in ce stadiu")
    rg = sub.add_parser("regenereaza",
                        help="rescrie mesajele pre-generate pentru prospectii vechi")
    rg.add_argument("--noi", action="store_true",
                    help="rescrie si mesajele deja scrise ale prospectilor netrimisi (status nou/trimis)")
    sub.add_parser("statistici", help="rata de raspuns pe nisa, tier si varianta de mesaj")

    g = sub.add_parser("test-google", help="verifica cheia Google Places pe o firma reala")
    g.add_argument("--firma", default="Dedeman Bucuresti")

    t = sub.add_parser("test-firmeapi", help="verifica cheia firmeapi si arata raspunsul brut")
    t.add_argument("--caen", default="8623")
    t.add_argument("--zile", type=int, default=7)

    a = p.parse_args(argv)
    if a.cmd == "colectare":
        try:
            rez = colectare(tiers=a.tiers, max_varsta=a.max_varsta, pauza=a.pauza,
                            doar_mobil=not a.toate_telefoanele,
                            verifica_google=not a.fara_google, sursa=a.sursa,
                            din_setari=a.din_setari)
        except BaseException as e:      # noqa: BLE001 - jurnalul e deja scris
            print(f"Colectarea a esuat: {_descrie_exceptia(e)}", file=sys.stderr, flush=True)
            print(_unde(e), file=sys.stderr, flush=True)
            return 1
        return 1 if (rez.get("eroare") or "").startswith("Eroare") else 0
    elif a.cmd == "regenereaza":
        # Prospectii adunati inainte de mutarea dashboardului pe Cloudflare
        # nu au mesajele scrise in baza. Le scriem acum, o singura data.
        # initializeaza() adauga intai coloanele noi pe baza existenta.
        db.initializeaza()
        with db.conexiune() as con:
            randuri = db.prospecti(con, limita=100000)
            n = 0
            for r in randuri:
                if r.get("mesaj_draft") and not (a.noi and r.get("status") in ("nou", "trimis")):
                    continue
                d = dict(r)
                text, varianta = mesaj.compune(d, SEMN)
                con.execute(
                    "UPDATE prospecti SET mesaj_draft=?, mesaj_fu3=?, mesaj_fu7=?, "
                    "varianta_mesaj=CASE WHEN status='nou' THEN ? ELSE COALESCE(varianta_mesaj,?) END WHERE cui=?",
                    (text, mesaj.compune_followup(d, 3, SEMN),
                     mesaj.compune_followup(d, 7, SEMN), varianta, varianta, r["cui"]))
                n += 1
        print(f"{n} prospecti au primit mesajele pre-generate "
              f"(din {len(randuri)} in total).")

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
