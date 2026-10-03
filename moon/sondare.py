"""Gaseste firmele noi direct la ANAF, fara lista ONRC.

CUI-urile se aloca secvential, in toata tara, din acelasi sir. Pornim de la
frontiera (ultimul CUI gasit data trecuta) si intrebam ANAF lot cu lot, cate
100 de CUI-uri valide (= 1.000 de numere, vezi cui.enumera_de_la), in sus.
Cand K loturi la rand nu mai au nicio firma, am trecut de capatul sirului.
Primul lot incepe chiar cu frontiera: o firma pe care ANAF o stia deja, deci
proba ca ANAF raspunde normal (daca lipseste, jurnalul primeste un avertisment).

De ce K = 3 (masurat pe pagina reala din 4 sept 2026, test/onrc_raw.html):
  - 100 de firme ONRC in 260 de CUI-uri valide: ~38 % din CUI-uri sunt firme
    ONRC, plus asociatiile & co. pe care ANAF le stie si ele;
  - cea mai mare gaura intre doua firme ONRC: 18 CUI-uri valide.
  Un lot (100) gol in zona deja alocata e practic imposibil; 3 loturi goale
  = 300 de CUI-uri valide fara nimic, de ~17 ori gaura maxima vazuta. Costul:
  3 cereri (~5 s) la fiecare rulare.
  Ziua cu putine inregistrari nu schimba nimic: densitatea e pe CUI, nu pe zi
  (sirul doar avanseaza mai putin).

De ce plafonul = 80 de loturi: o zi lucratoare are, estimat, ~9-10 loturi
(~9.000-10.000 de numere: cele 98 de firme ale zilei de 4 sept de pe pagina,
doar ultimele ale zilei, stateau deja pe 2.575 de numere; acelasi ordin de
marime da si ritmul CUI-urilor din ultimii ani). 80 de loturi ajung pentru ~8
zile lucratoare restante (peste vechimea implicita de 7 zile), in ~2,5 minute.
Daca restanta e mai mare, rularea urmatoare continua de unde a ramas (si
jurnalul spune asta).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice
from typing import Callable, Dict, List, Optional

from .cui import enumera_de_la

MARIME_LOT = 100
K_GOALE = 3
PLAFON_LOTURI = 80


@dataclass
class Sondaj:
    firme: Dict[int, object] = field(default_factory=dict)
    loturi: int = 0                       # cereri reusite
    interogate: int = 0                   # CUI-uri valide intrebate
    cui_min: Optional[int] = None         # primul CUI intrebat
    cui_max: Optional[int] = None         # ultimul CUI intrebat
    ultim_gasit: Optional[int] = None     # cel mai mare CUI gasit in ANAF
    motiv: str = ""                       # capat | plafon | eroare
    eroare: Optional[BaseException] = None


def sondeaza(start: int, cere_lot: Callable[[List[int]], Dict[int, object]], *,
             k: int = K_GOALE, plafon: int = PLAFON_LOTURI,
             nu_te_opri_sub: Optional[int] = None, marime: int = MARIME_LOT,
             log: Callable[[str], None] = lambda m: None) -> Sondaj:
    """Intreaba ANAF de la `start` in sus pana la K loturi goale la rand sau pana la plafon.

    `nu_te_opri_sub`: capatul aratat de lista ONRC, daca o avem. Sub el nu ne
    oprim pe loturi goale (stim ca acolo sunt firme), doar peste el.
    O eroare ANAF opreste sondarea: ce s-a gasit pana atunci ramane in rezultat.
    """
    s = Sondaj()
    sir = enumera_de_la(max(1, int(start)))
    goale = 0
    for _ in range(max(1, plafon)):
        lot = list(islice(sir, marime))
        try:
            gasite = cere_lot(lot) or {}
        except Exception as e:      # noqa: BLE001 - orice eroare opreste sondarea, curat
            s.motiv, s.eroare = "eroare", e
            return s
        s.loturi += 1
        s.interogate += len(lot)
        s.cui_min = lot[0] if s.cui_min is None else s.cui_min
        s.cui_max = lot[-1]
        s.firme.update(gasite)
        if gasite:
            goale = 0
            s.ultim_gasit = max(s.ultim_gasit or 0, max(gasite))
        else:
            goale += 1
            if goale >= k and (nu_te_opri_sub is None or lot[0] > nu_te_opri_sub):
                s.motiv = "capat"
                return s
        if s.loturi % 10 == 0:
            log(f"     {s.loturi} loturi, {len(s.firme)} firme gasite pana acum")
    s.motiv = "plafon"
    return s
