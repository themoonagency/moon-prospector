"""Lista alba CAEN Rev. 3 - decide pe cine contactam si ce ii propunem.

Baza e `caen_whitelist_moon.csv`. Peste ea se pun schimbarile facute din panou
(`stare.caen_override`, vezi `moon/setari.py`): alt tier, cod oprit, cod nou.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

FISIER_IMPLICIT = Path(__file__).resolve().parent.parent / "caen_whitelist_moon.csv"


@dataclass
class Nisa:
    cod: str
    denumire: str
    tier: str
    serviciu: str
    nota: str
    activ: bool = True


class ListaAlba:
    def __init__(self, cale: Optional[Path] = None):
        self.cale = Path(cale) if cale else FISIER_IMPLICIT
        self._nise: Dict[str, Nisa] = {}
        self._incarca()

    def _incarca(self) -> None:
        with open(self.cale, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                cod = str(r["cod"]).strip().zfill(4)
                self._nise[cod] = Nisa(
                    cod=cod,
                    denumire=r["denumire"].strip(),
                    tier=r["tier"].strip().upper(),
                    serviciu=r["serviciu_moon"].strip(),
                    nota=(r.get("nota") or "").strip(),
                )

    def aplica_override(self, override: Dict[str, dict]) -> int:
        """Pune peste CSV schimbarile din panou (deja curatate de setari.curata_override).

        Cod existent: tier / activ / denumire / serviciu schimbate doar daca sunt date.
        Cod nou: intra doar cu tier si denumire. Returneaza cate coduri s-au aplicat.
        """
        aplicate = 0
        for cod, o in (override or {}).items():
            n = self._nise.get(cod)
            if n is None:
                if not o.get("tier") or not o.get("denumire"):
                    continue
                n = self._nise[cod] = Nisa(cod=cod, denumire=o["denumire"], tier=o["tier"],
                                           serviciu=o.get("serviciu") or "", nota="")
            else:
                if o.get("tier"):
                    n.tier = o["tier"]
                if o.get("denumire"):
                    n.denumire = o["denumire"]
                if o.get("serviciu"):
                    n.serviciu = o["serviciu"]
            if "activ" in o:
                n.activ = bool(o["activ"])
            aplicate += 1
        return aplicate

    def get(self, caen: Optional[str]) -> Optional[Nisa]:
        if not caen:
            return None
        return self._nise.get(str(caen).strip().zfill(4))

    def accepta(self, caen: Optional[str], tiers: str = "A,B") -> Optional[Nisa]:
        """Returneaza nisa daca CAEN-ul e in lista, e pornit SI e in tier-urile cerute."""
        n = self.get(caen)
        permise = {t.strip().upper() for t in tiers.split(",") if t.strip()}
        return n if (n and n.activ and n.tier in permise) else None

    def coduri(self, tiers: str) -> List[str]:
        """Codurile pornite din tier-urile cerute (pentru sursele care filtreaza din start)."""
        permise = {t.strip().upper() for t in tiers.split(",") if t.strip()}
        return [n.cod for n in self._nise.values() if n.activ and n.tier in permise]

    def __len__(self) -> int:
        return len(self._nise)

    def statistici(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for n in self._nise.values():
            if n.activ:
                out[n.tier] = out.get(n.tier, 0) + 1
        return out
