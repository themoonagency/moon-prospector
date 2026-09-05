"""Sursa de start: lista publica a firmelor nou infiintate.

Pagina afiseaza doar ultimele ~100 de inregistrari, insa noua ne trebuie in
principal intervalul de CUI-uri al zilei: restul firmelor le descoperim
enumerand CUI-urile valide din interval si intrebandu-le pe ANAF.

Parserul se leaga de atributul `title` al linkurilor de firma (contine numele,
localitatea si CUI-ul), nu de clasele CSS - acelea sunt minificate si se
schimba. Exista si o rezerva pe linkurile /profile/<cui>.html.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests

URL = "https://new.firme-on-line.ro/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# title="DC URBAN GROUP SRL din Șendreni cui 55542190"
_RE_TITLU = re.compile(r'title="([^"]{3,120}?)\s+din\s+([^"]{1,60}?)\s+cui\s+(\d{5,10})"', re.I)
# rezerva: orice link catre profilul unei firme
_RE_PROFIL = re.compile(r"/profile/(\d{5,10})\.html")
# CAEN-ul apare in acelasi card, dupa link; il luam daca il gasim
_RE_CAEN = re.compile(r"CAEN\s*</span>\s*<span[^>]*>\s*(\d{3,4})\s*<", re.I)

# Nu enumeram niciodata un interval mai mare de atat (protectie daca pagina
# se schimba si prindem un CUI vechi din alta parte a paginii).
MAX_INTERVAL = 20_000


@dataclass
class Intrare:
    denumire: str
    cui: int
    localitate: str
    caen: str


def parseaza(html: str) -> List[Intrare]:
    """Extrage intrarile din HTML-ul brut (separat, ca sa poata fi testat)."""
    intrari: List[Intrare] = []
    vazute = set()
    for m in _RE_TITLU.finditer(html):
        cui = int(m.group(3))
        if cui in vazute:
            continue
        vazute.add(cui)
        caen = ""
        c = _RE_CAEN.search(html, m.end(), m.end() + 2000)
        if c:
            caen = c.group(1).zfill(4)
        intrari.append(Intrare(denumire=m.group(1).strip(), cui=cui,
                               localitate=m.group(2).strip(), caen=caen))
    if intrari:
        return intrari

    # Rezerva: doar CUI-urile, fara denumire. Suficient pentru interval.
    cuis = sorted({int(x) for x in _RE_PROFIL.findall(html)})
    if not cuis:
        return []
    prag = cuis[-1] - MAX_INTERVAL          # pastram doar grupul recent
    return [Intrare("", c, "", "") for c in cuis if c >= prag]


def descarca(timeout: int = 30) -> List[Intrare]:
    r = requests.get(URL, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return parseaza(r.text)


def interval_cui(intrari: List[Intrare]) -> Optional[Tuple[int, int]]:
    """(cel mai mic, cel mai mare) CUI - frontiera de explorat, cu limita."""
    if not intrari:
        return None
    cuis = [i.cui for i in intrari]
    hi = max(cuis)
    lo = max(min(cuis), hi - MAX_INTERVAL)
    return lo, hi
