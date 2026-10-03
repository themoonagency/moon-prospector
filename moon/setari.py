"""Setarile colectarii venite din panou (prospect.themoonagency.ro).

Le scrie workerul (src/colectare.js) in tabela `stare`, ca JSON:
  - `colectare_setari`: {"tiers": ["A"], "max_varsta": 7, "sursa": "auto"}
  - `caen_override`:    {"<cod>": {"tier": "A", "activ": true, "denumire": "...", "serviciu": "..."}}

Aici doar le citim si le curatam: o valoare gresita se sare (cu avertisment in
jurnal), nu opreste colectarea.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

IMPLICITE = {"tiers": ["A"], "max_varsta": 7, "sursa": "auto"}
TIERS_OK = ("A", "B", "C")
# Panoul si inputs din GitHub Actions: doar sursele care pornesc de la frontiera `ultim_cui`.
SURSE_PANOU = ("auto", "anaf")
# Doar din linia de comanda, local (onrc = strict cu lista ONRC; firmeapi = API-ul platit).
SURSE_CLI = ("auto", "anaf", "onrc", "firmeapi")


def surse_permise(env: Mapping[str, str]) -> Tuple[str, ...]:
    """In GitHub Actions (inputs din workflow) doar auto/anaf; local, din linia de comanda, toate."""
    return SURSE_PANOU if str(env.get("GITHUB_ACTIONS", "")).lower() == "true" else SURSE_CLI
VARSTA_MIN, VARSTA_MAX = 1, 30
TEXT_MAX = 200
_RE_COD = re.compile(r"^\d{4}$")


def din_json(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def tiers_lista(x: Any) -> Optional[List[str]]:
    """"A,B" sau ["A","B"] -> ["A","B"]; None daca e gol sau are ceva necunoscut."""
    if isinstance(x, str):
        x = x.split(",")
    if not isinstance(x, (list, tuple)):
        return None
    out: List[str] = []
    for t in x:
        if not isinstance(t, str):
            return None
        t = t.strip().upper()
        if not t:
            continue
        if t not in TIERS_OK:
            return None
        if t not in out:
            out.append(t)
    return sorted(out) or None


def varsta(x: Any) -> Optional[int]:
    if isinstance(x, bool):
        return None
    try:
        v = int(str(x).strip())
    except (TypeError, ValueError):
        return None
    return v if VARSTA_MIN <= v <= VARSTA_MAX else None


def curata_setari(x: Any) -> Tuple[Dict[str, Any], int]:
    """Doar campurile valide din setarile panoului + cate erau gresite."""
    if x is None:
        return {}, 0
    if not isinstance(x, dict):
        return {}, 1
    out: Dict[str, Any] = {}
    gresite = 0
    if "tiers" in x:
        t = tiers_lista(x["tiers"])
        if t:
            out["tiers"] = t
        else:
            gresite += 1
    if "max_varsta" in x:
        v = varsta(x["max_varsta"])
        if v:
            out["max_varsta"] = v
        else:
            gresite += 1
    if "sursa" in x:
        s = x["sursa"] if isinstance(x["sursa"], str) else ""
        if s in SURSE_PANOU:
            out["sursa"] = s
        else:
            gresite += 1
    return out, gresite


def rezolva(explicit: Mapping[str, Any], din_panou: Optional[Mapping[str, Any]],
            env: Mapping[str, str]) -> Tuple[Dict[str, Any], List[str]]:
    """Ce setari folosim, pe rand: explicit (CLI / inputs) > panou > variabile de mediu > implicit.

    `din_panou` e None cand rularea nu cere setarile din panou (fara --din-setari).
    Sursa explicita: in GitHub Actions doar auto/anaf (vezi surse_permise), altfel se ignora ca gresita.
    Intoarce ({"tiers": "A,B", "max_varsta": 7, "sursa": "auto"}, campurile explicite gresite).
    O valoare explicita gresita (ex. o greseala de tastare in Actions) se ignora, nu opreste rularea.
    """
    panou = din_panou or {}
    gresite: List[str] = []

    def dat(k):
        v = explicit.get(k)
        return v is not None and str(v).strip() != ""

    t_explicit = tiers_lista(explicit.get("tiers")) if dat("tiers") else None
    if dat("tiers") and not t_explicit:
        gresite.append("tiers")
    tiers = (t_explicit or panou.get("tiers")
             or tiers_lista(env.get("MOON_TIERS"))
             or (IMPLICITE["tiers"] if din_panou is not None else ["A", "B"]))

    v_explicit = varsta(explicit.get("max_varsta")) if dat("max_varsta") else None
    if dat("max_varsta") and not v_explicit:
        gresite.append("max_varsta")
    v = (v_explicit or panou.get("max_varsta")
         or varsta(env.get("MOON_MAX_AGE_DAYS")) or IMPLICITE["max_varsta"])

    s = str(explicit.get("sursa")).strip().lower() if dat("sursa") else None
    if s is not None and s not in surse_permise(env):
        gresite.append("sursa")
        s = None
    s = s or panou.get("sursa") or IMPLICITE["sursa"]
    return {"tiers": ",".join(tiers), "max_varsta": int(v), "sursa": s}, gresite


def _text(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    x = re.sub(r"[\x00-\x1f\x7f]+", " ", x).strip()
    return x[:TEXT_MAX] if x else None


def curata_override(x: Any) -> Tuple[Dict[str, dict], int]:
    """Schimbarile CAEN din panou, curatate, + cate intrari am sarit (gresite)."""
    if x is None:
        return {}, 0
    if not isinstance(x, dict):
        return {}, 1
    out: Dict[str, dict] = {}
    sarite = 0
    for cod, o in x.items():
        cod = str(cod).strip()
        if not _RE_COD.match(cod) or not isinstance(o, dict):
            sarite += 1
            continue
        c: Dict[str, Any] = {}
        if "tier" in o:
            t = o["tier"].strip().upper() if isinstance(o["tier"], str) else ""
            if t in TIERS_OK:
                c["tier"] = t
            else:
                sarite += 1
                continue
        if "activ" in o:
            if not isinstance(o["activ"], bool):
                sarite += 1
                continue
            c["activ"] = o["activ"]
        for camp in ("denumire", "serviciu"):
            t = _text(o.get(camp))
            if t:
                c[camp] = t
        if c:
            out[cod] = c
    return out, sarite
