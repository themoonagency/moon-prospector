"""Verificare reală pe Google: are firma fișă (Google Business Profile) și site?

Fără pasul ăsta, mesajul afirmă ceva ce nu a verificat nimeni. Cu el, afirmația
devine fapt — și poți trimite trei mesaje diferite în loc de unul singur.

Cost: Places API Text Search, ~0,002 $ per prospect. Tier-ul gratuit e de 1.000
de apeluri pe lună, adică ~45 pe zi lucrătoare — suficient pentru tier A.
Fără cheie API, verificarea se dezactivează singură și mesajul nu mai pretinde
că a căutat pe Google.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import requests

URL = "https://places.googleapis.com/v1/places:searchText"

# Google factureaza pe cel mai scump camp cerut, iar cota gratuita difera mult:
#   Pro        -> 5.000 apeluri gratuite/luna   (denumire, adresa, website, status)
#   Enterprise -> 1.000 apeluri gratuite/luna   (adauga rating + numar de recenzii)
# Implicit cerem doar campuri Pro: de 5 ori mai multa cota, aceeasi decizie de mesaj.
CAMPURI_PRO = ("places.id,places.displayName,places.formattedAddress,"
               "places.websiteUri,places.businessStatus,places.googleMapsUri")
CAMPURI_ENTERPRISE = CAMPURI_PRO + ",places.rating,places.userRatingCount"


def campuri() -> str:
    import os as _os
    return (CAMPURI_ENTERPRISE
            if _os.getenv("MOON_PLACES_TIER", "pro").lower() == "enterprise"
            else CAMPURI_PRO)

# Cat de asemanatoare trebuie sa fie denumirea gasita cu cea cautata ca sa
# consideram ca e aceeasi firma (0-1).
PRAG_POTRIVIRE = 0.62


@dataclass
class Prezenta:
    verificat: bool                  # am reusit sa intrebam Google?
    are_fisa: bool = False
    place_id: Optional[str] = None
    nume_gasit: Optional[str] = None
    website: Optional[str] = None
    recenzii: int = 0
    maps_url: Optional[str] = None

    @property
    def are_site(self) -> bool:
        return bool(self.website)

    @property
    def scenariu(self) -> str:
        """Ce mesaj i se potrivește: 'nimic' | 'fisa_slaba' | 'are_tot' | 'neverificat'.

        Decizia se ia pe prezenta fisei si a site-ului - amandoua sunt campuri Pro,
        deci nu avem nevoie de tier-ul Enterprise ca sa alegem corect mesajul.
        """
        if not self.verificat:
            return "neverificat"
        if not self.are_fisa:
            return "nimic"
        return "are_tot" if self.are_site else "fisa_slaba"


def _normalizeaza(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\b(s\.?r\.?l\.?|s\.?a\.?|p\.?f\.?a\.?|srl|sa|pfa|ii|if)\b", " ", s)
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def _similitudine(a: str, b: str) -> float:
    """Suprapunerea cuvintelor semnificative dintre două denumiri."""
    ca = {w for w in _normalizeaza(a).split() if len(w) > 2}
    cb = {w for w in _normalizeaza(b).split() if len(w) > 2}
    if not ca or not cb:
        return 0.0
    return len(ca & cb) / min(len(ca), len(cb))


def cheie() -> Optional[str]:
    return os.getenv("GOOGLE_PLACES_API_KEY") or None


def verifica(denumire: str, localitate: str = "", judet: str = "",
             timeout: int = 20) -> Prezenta:
    """Caută firma pe Google. Returnează ce prezență online are."""
    k = cheie()
    if not k:
        return Prezenta(verificat=False)

    interogare = " ".join(x for x in (denumire, localitate, judet, "România") if x)
    try:
        r = requests.post(
            URL, timeout=timeout,
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": k,
                     "X-Goog-FieldMask": campuri()},
            json={"textQuery": interogare, "languageCode": "ro",
                  "regionCode": "RO", "pageSize": 5},
        )
        r.raise_for_status()
        locuri = r.json().get("places") or []
    except Exception:
        return Prezenta(verificat=False)

    for loc in locuri:
        nume = (loc.get("displayName") or {}).get("text", "")
        if _similitudine(denumire, nume) < PRAG_POTRIVIRE:
            continue
        if loc.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        return Prezenta(
            verificat=True, are_fisa=True, place_id=loc.get("id"), nume_gasit=nume,
            website=loc.get("websiteUri"), recenzii=int(loc.get("userRatingCount") or 0),
            maps_url=loc.get("googleMapsUri"),
        )

    # Am întrebat Google și nu am găsit-o — asta e o informație, nu o eroare.
    return Prezenta(verificat=True, are_fisa=False)


def diagnostic(interogare: str = "Dedeman Bucuresti", timeout: int = 25) -> dict:
    """Apel de test care NU inghite erorile - spune exact ce raspunde Google."""
    k = cheie()
    if not k:
        return {"ok": False, "tip": "fara_cheie",
                "mesaj": "Lipseste GOOGLE_PLACES_API_KEY (pune-l cu ./cheie.sh)."}
    try:
        r = requests.post(
            URL, timeout=timeout,
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": k,
                     "X-Goog-FieldMask": campuri()},
            json={"textQuery": interogare, "languageCode": "ro",
                  "regionCode": "RO", "pageSize": 3})
    except Exception as e:
        return {"ok": False, "tip": "retea",
                "mesaj": f"Nu am putut ajunge la Google: {type(e).__name__}. "
                         "Verifica internetul sau daca rulezi in spatele unui proxy."}
    try:
        j = r.json()
    except Exception:
        return {"ok": False, "tip": "raspuns", "http": r.status_code,
                "mesaj": r.text[:300]}
    if r.status_code == 200:
        return {"ok": True, "http": 200, "locuri": j.get("places", [])}
    e = j.get("error", {}) or {}
    return {"ok": False, "tip": "api", "http": r.status_code,
            "cod": e.get("status"), "mesaj": e.get("message", "")[:400]}
