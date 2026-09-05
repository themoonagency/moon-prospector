"""Client pentru serviciul web public ANAF.

Endpoint gratuit, fara autentificare. Limite oficiale:
  - maxim 100 CUI-uri per request
  - maxim 1 request pe secunda

Returneaza denumire, adresa, cod CAEN, telefon, stare si data inregistrarii.
Nu returneaza email sau website.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional

import requests

URL = "https://webservicesp.anaf.ro/api/PlatitorTvaRest/v9/tva"
UA = "MoonProspector/0.1 (+https://themoonagency.ro)"


@dataclass
class Firma:
    cui: int
    denumire: str
    adresa: str
    telefon: Optional[str]
    caen: Optional[str]
    data_inregistrare: Optional[str]
    stare: Optional[str]
    nr_reg_com: Optional[str]
    # Completate doar cand sursa le da direct (firmeapi). De la ANAF raman None
    # si se deduc din adresa.
    judet: Optional[str] = None
    localitate: Optional[str] = None

    @property
    def activa(self) -> bool:
        s = (self.stare or "").upper()
        return "RADIERE" not in s and "INACTIV" not in s


def _to_firma(bloc: dict) -> Optional[Firma]:
    g = bloc.get("date_generale") or {}
    if not g.get("cui"):
        return None
    return Firma(
        cui=int(g["cui"]),
        denumire=(g.get("denumire") or "").strip(),
        adresa=(g.get("adresa") or "").strip(),
        telefon=(g.get("telefon") or "").strip() or None,
        caen=str(g["cod_CAEN"]).zfill(4) if g.get("cod_CAEN") else None,
        data_inregistrare=g.get("data_inregistrare") or None,
        stare=g.get("stare_inregistrare") or None,
        nr_reg_com=g.get("nrRegCom") or None,
    )


def interogheaza(
    cuis: Iterable[int],
    zi: Optional[date] = None,
    pauza: float = 1.2,
    timeout: int = 30,
    incercari: int = 3,
) -> Dict[int, Firma]:
    """Interogheaza ANAF in loturi de 100 si returneaza {cui: Firma}."""
    from .cui import loturi

    zi = zi or date.today()
    lista = list(cuis)
    rezultate: Dict[int, Firma] = {}
    sesiune = requests.Session()
    sesiune.headers.update({"Content-Type": "application/json", "User-Agent": UA})

    for lot in loturi(lista, 100):
        payload = [{"cui": c, "data": zi.isoformat()} for c in lot]
        for incercare in range(incercari):
            try:
                r = sesiune.post(URL, json=payload, timeout=timeout)
                r.raise_for_status()
                for bloc in (r.json().get("found") or []):
                    f = _to_firma(bloc)
                    if f:
                        rezultate[f.cui] = f
                break
            except Exception:
                if incercare == incercari - 1:
                    raise
                time.sleep(2 ** incercare * pauza)
        time.sleep(pauza)
    return rezultate


def normalizeaza_telefon(brut: Optional[str]) -> Optional[str]:
    """Aduce numarul la formatul international +40XXXXXXXXX.

    Ia primul numar daca in camp sunt mai multe (separate prin , ; / sau spatiu).
    """
    if not brut:
        return None
    import re

    for bucata in re.split(r"[,;/]| {2,}", brut):
        d = re.sub(r"\D", "", bucata)
        if not d:
            continue
        if d.startswith("0040"):
            d = d[4:]
        elif d.startswith("40") and len(d) >= 11:
            d = d[2:]
        elif d.startswith("0"):
            d = d[1:]
        if len(d) == 9 and d[0] in "237":
            return "+40" + d
    return None


def este_mobil(telefon_normalizat: Optional[str]) -> bool:
    """True pentru numere mobile (+407...) - singurele utile pe WhatsApp."""
    return bool(telefon_normalizat and telefon_normalizat.startswith("+407"))
