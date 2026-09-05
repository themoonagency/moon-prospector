"""Îmbogățire opțională cu numele administratorului și emailul firmei.

Sursele publice gratuite (ONRC republicat, ANAF) NU publică administratorul.
Singura sursă practică e un API plătit. Modulul e făcut ca plugin: fără cheie,
sistemul merge exact ca înainte și mesajul nu folosește niciun nume de persoană.

Backend disponibil: firmeapi.ro (plan Essential ~100 RON/lună) — dă administratori
și, separat, email (acoperire ~78 %) și website (~41 %).
Se activează punând FIRMEAPI_KEY în mediu.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

BAZA = "https://www.firmeapi.ro/api/v1"


@dataclass
class Contact:
    administrator: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None


def activ() -> bool:
    return bool(os.getenv("FIRMEAPI_KEY"))


def _prenume_nume(brut: str) -> Optional[str]:
    """POPESCU ION MARIUS -> Popescu Ion (pentru adresare politicoasă)."""
    if not brut:
        return None
    p = [x for x in re.split(r"\s+", brut.strip()) if x]
    if not p:
        return None
    return " ".join(x.capitalize() for x in p[:2])


def imbogateste(cui: int, timeout: int = 20) -> Contact:
    k = os.getenv("FIRMEAPI_KEY")
    if not k:
        return Contact()
    h = {"X-API-KEY": k}
    c = Contact()
    try:
        r = requests.get(f"{BAZA}/administratori/{cui}", headers=h, timeout=timeout)
        if r.ok:
            lista = r.json().get("administratori") or r.json().get("data") or []
            if lista:
                nume = lista[0].get("nume") or lista[0].get("denumire") or ""
                c.administrator = _prenume_nume(nume)
    except Exception:
        pass
    try:
        r = requests.get(f"{BAZA}/datecontact/{cui}", headers=h, timeout=timeout)
        if r.ok:
            d = r.json().get("date_contact") or r.json().get("data") or r.json()
            c.email = (d.get("email") or "").strip() or None
            c.website = (d.get("website") or "").strip() or None
    except Exception:
        pass
    return c


def nume_pentru_adresare(administrator: Optional[str]) -> Optional[str]:
    """'Popescu Ion' -> 'domnule Popescu'. Fără nume, returnează None."""
    if not administrator:
        return None
    fam = administrator.split()[0]
    return f"domnule {fam}" if fam else None
