"""Lista alba CAEN Rev. 3 - decide pe cine contactam si ce ii propunem."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

FISIER_IMPLICIT = Path(__file__).resolve().parent.parent / "caen_whitelist_moon.csv"


@dataclass
class Nisa:
    cod: str
    denumire: str
    tier: str
    serviciu: str
    nota: str


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

    def get(self, caen: Optional[str]) -> Optional[Nisa]:
        if not caen:
            return None
        return self._nise.get(str(caen).strip().zfill(4))

    def accepta(self, caen: Optional[str], tiers: str = "A,B") -> Optional[Nisa]:
        """Returneaza nisa daca CAEN-ul e in lista SI in tier-urile cerute."""
        n = self.get(caen)
        permise = {t.strip().upper() for t in tiers.split(",") if t.strip()}
        return n if (n and n.tier in permise) else None

    def __len__(self) -> int:
        return len(self._nise)

    def statistici(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for n in self._nise.values():
            out[n.tier] = out.get(n.tier, 0) + 1
        return out
