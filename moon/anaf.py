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


class AnafIndisponibil(RuntimeError):
    """ANAF nu raspunde (timeout, 5xx, 429, raspuns stricat) nici dupa reincercari."""

    def __init__(self, motiv: str, http: Optional[int] = None):
        super().__init__(motiv)
        self.motiv = motiv
        self.http = http


class AnafRefuza(RuntimeError):
    """ANAF a refuzat cererea (4xx): nu are rost sa reincercam, ceva s-a schimbat."""

    def __init__(self, motiv: str, http: Optional[int] = None):
        super().__init__(motiv)
        self.motiv = motiv
        self.http = http


# Cat asteptam inainte de reincercarea 1, 2, 3 (secunde). Peste pauza obisnuita.
ASTEPTARI = (2, 5, 10)


def sesiune_noua() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "User-Agent": UA})
    return s


def _citeste_raspuns(r) -> Optional[list]:
    """Lista `found` din raspuns, sau None daca raspunsul nu e al ANAF (stricat)."""
    try:
        j = r.json()
    except ValueError:
        return None
    if not isinstance(j, dict) or ("found" not in j and "notFound" not in j):
        return None
    gasite = j.get("found") or []
    return gasite if isinstance(gasite, list) else None


def interogheaza_lot(
    lot: List[int],
    zi: Optional[date] = None,
    sesiune: Optional[requests.Session] = None,
    pauza: float = 1.2,
    timeout: int = 30,
    incercari: int = 1 + len(ASTEPTARI),
    dormi=time.sleep,
) -> Dict[int, Firma]:
    """Un singur lot (maxim 100 de CUI-uri) -> {cui: Firma}.

    Dupa FIECARE cerere asteapta `pauza` (limita ANAF: 1 cerere/secunda).
    Reincearca la timeout, conexiune cazuta, 429, 5xx si raspuns care nu e JSON-ul ANAF,
    cu asteptari tot mai lungi (ASTEPTARI). La alt 4xx se opreste imediat (AnafRefuza).
    Un 404 cu JSON-ul ANAF inseamna doar „niciun CUI gasit".
    """
    if len(lot) > 100:
        raise ValueError("ANAF primeste maxim 100 de CUI-uri pe cerere")
    zi = zi or date.today()
    sesiune = sesiune or sesiune_noua()
    payload = [{"cui": int(c), "data": zi.isoformat()} for c in lot]
    motiv, http = "nu răspunde", None
    for incercare in range(max(1, incercari)):
        if incercare:
            dormi(ASTEPTARI[min(incercare - 1, len(ASTEPTARI) - 1)])
        try:
            r = sesiune.post(URL, json=payload, timeout=timeout)
        except requests.exceptions.Timeout:
            motiv, http = "nu răspunde (timeout)", None
            dormi(pauza)
            continue
        except requests.exceptions.RequestException:
            motiv, http = "nu răspunde (conexiune căzută)", None
            dormi(pauza)
            continue
        dormi(pauza)
        cod = r.status_code
        if cod == 429 or cod >= 500:
            motiv, http = f"nu răspunde (HTTP {cod})", cod
            continue
        gasite = _citeste_raspuns(r)
        if cod >= 400 and not (cod == 404 and gasite is not None):
            if cod in (401, 403, 451):
                raise AnafRefuza(f"blochează cererile noastre (HTTP {cod})", cod)
            raise AnafRefuza(f"refuză cererea (HTTP {cod})", cod)
        if gasite is None:
            motiv, http = "dă un răspuns stricat", cod
            continue
        out: Dict[int, Firma] = {}
        for bloc in gasite:
            f = _to_firma(bloc) if isinstance(bloc, dict) else None
            if f:
                out[f.cui] = f
        return out
    raise AnafIndisponibil(motiv, http)


def interogheaza(
    cuis: Iterable[int],
    zi: Optional[date] = None,
    pauza: float = 1.2,
    timeout: int = 30,
    incercari: int = 1 + len(ASTEPTARI),
) -> Dict[int, Firma]:
    """Interogheaza ANAF in loturi de 100 si returneaza {cui: Firma}."""
    from .cui import loturi

    rezultate: Dict[int, Firma] = {}
    sesiune = sesiune_noua()
    for lot in loturi(list(cuis), 100):
        rezultate.update(interogheaza_lot(lot, zi=zi, sesiune=sesiune, pauza=pauza,
                                          timeout=timeout, incercari=incercari))
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
