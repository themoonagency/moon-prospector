"""Sursă alternativă de firme noi: API-ul firmeapi.ro.

De ce merită, chiar și pe planul gratuit:
  - e API, nu scraping de HTML (nu se strică dacă pagina isi schimba structura)
  - filtreaza direct pe CAEN, deci nu mai interogam ANAF pentru firme
    pe care oricum le-am fi aruncat
  - filtreaza pe "are telefon"
  - accepta interval de date, deci poti recupera zilele pierdute

Costul e per REZULTAT, nu per request: 0,70 credite pe firma returnata, iar o
interogare fara rezultate nu consuma nimic. Planul gratuit are 1.000 de credite
pe luna, adica ~1.428 de firme. De aceea interogam cod CAEN cu cod CAEN, din
lista alba - nu toata ziua deodata, ca s-ar duce bugetul in cateva zile.

Endpointul /firme-noi si datele de contact (email, administrator) NU sunt in
planul gratuit - necesita abonament platit.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional

import requests

BAZA = "https://www.firmeapi.ro/api/v1"
CREDITE_PER_REZULTAT = 0.70


@dataclass
class FirmaAPI:
    cui: int
    denumire: str
    data_inregistrare: Optional[str]
    caen: Optional[str]
    telefon: Optional[str]
    judet: Optional[str]
    localitate: Optional[str]
    stare: Optional[str]
    nr_reg_com: Optional[str]
    adresa: Optional[str]


def cheie() -> Optional[str]:
    return os.getenv("FIRMEAPI_KEY") or None


def activ() -> bool:
    return bool(cheie())


def _prim(d: dict, *nume, implicit=None):
    """Primul câmp existent dintre variantele de denumire posibile."""
    for n in nume:
        if d.get(n) not in (None, ""):
            return d[n]
    return implicit


def _to_firma(d: dict) -> Optional[FirmaAPI]:
    cui = _prim(d, "cui", "CUI", "cod_fiscal")
    if not cui:
        return None
    loc = _prim(d, "localitate", "denloc", "oras")
    strada = _prim(d, "strada", "adresa")
    numar = _prim(d, "numar", "nr")
    adresa = ", ".join(str(x) for x in (strada, numar, loc, _prim(d, "judet")) if x)
    caen = _prim(d, "cod_caen", "caen", "cod_CAEN")
    return FirmaAPI(
        cui=int(cui),
        denumire=str(_prim(d, "denumire", "nume", implicit="")).strip(),
        data_inregistrare=_prim(d, "data_inregistrare", "data_inreg"),
        caen=str(caen).zfill(4) if caen else None,
        telefon=_prim(d, "telefon", "tel"),
        judet=_prim(d, "judet"),
        localitate=loc,
        stare=_prim(d, "stare", "stare_inregistrare"),
        nr_reg_com=_prim(d, "nr_reg_com", "nrRegCom"),
        adresa=adresa or None,
    )


def _rezultate(payload) -> List[dict]:
    """Răspunsul poate veni ca listă simplă sau împachetat în data/firme/results."""
    if isinstance(payload, list):
        return payload
    for k in ("data", "firme", "results", "rezultate", "items"):
        v = payload.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in ("data", "firme", "results", "items"):
                if isinstance(v.get(k2), list):
                    return v[k2]
    return []


def interogheaza_brut(params: dict, timeout: int = 25) -> dict:
    """Un singur apel, întors ca atare. Util și pentru diagnostic."""
    k = cheie()
    if not k:
        raise RuntimeError("Lipsește FIRMEAPI_KEY")
    r = requests.get(f"{BAZA}/firme", params=params, timeout=timeout,
                     headers={"X-API-KEY": k, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def firme_noi(coduri_caen: Iterable[str], data_start: date, data_end: Optional[date] = None,
              doar_cu_telefon: bool = True, pauza: float = 0.15,
              max_credite: float = 400.0, verbose=None) -> Dict[int, FirmaAPI]:
    """Firmele înmatriculate în interval, pentru codurile CAEN cerute.

    Interoghează cod cu cod, ca să nu plătească rezultate pe care oricum
    le-am arunca. Se oprește dacă depășește bugetul de credite.
    """
    data_end = data_end or data_start
    out: Dict[int, FirmaAPI] = {}
    credite = 0.0

    for cod in coduri_caen:
        if credite >= max_credite:
            if verbose:
                verbose(f"     oprit la {credite:.0f} credite (bugetul rulării)")
            break
        params = {"caen": cod, "data_start": data_start.isoformat(),
                  "data_end": data_end.isoformat(), "per_page": 20}
        if doar_cu_telefon:
            params["telefon"] = 1
        pagina = 1
        while True:
            params["page"] = pagina
            try:
                brut = interogheaza_brut(params)
            except Exception as e:
                if verbose:
                    verbose(f"     CAEN {cod}: {e}")
                break
            randuri = _rezultate(brut)
            if not randuri:
                break
            credite += len(randuri) * CREDITE_PER_REZULTAT
            for d in randuri:
                f = _to_firma(d)
                if f:
                    out[f.cui] = f
            if len(randuri) < 20 or pagina >= 5:
                break
            pagina += 1
            time.sleep(pauza)
        time.sleep(pauza)

    if verbose:
        verbose(f"     {len(out)} firme, ~{credite:.0f} credite consumate")
    return out
