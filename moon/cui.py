"""Validare si enumerare CUI-uri romanesti.

CUI-urile se aloca secvential, deci intre cel mai mic si cel mai mare CUI
inregistrat intr-o zi se afla TOATE firmele infiintate in ziua respectiva.
Cifra de control ne lasa sa generam doar numerele valide (~1 din 10),
in loc sa intrebam ANAF despre fiecare numar din interval.
"""
from typing import Iterator, List

# Cheia oficiala de control pentru CUI (aliniata la dreapta pe 9 pozitii)
_KEY = (7, 5, 3, 2, 1, 7, 5, 3, 2)


def cifra_control(corp: int) -> int:
    """Cifra de control pentru partea din CUI fara ultima cifra."""
    b = str(corp).rjust(9, "0")
    if len(b) > 9:
        raise ValueError("CUI prea lung")
    total = sum(int(b[i]) * _KEY[i] for i in range(9))
    return (total * 10) % 11 % 10


def valid(cui: int) -> bool:
    """True daca numarul respecta algoritmul oficial de cifra de control."""
    s = str(cui)
    if not (2 <= len(s) <= 10):
        return False
    try:
        return cifra_control(int(s[:-1])) == int(s[-1])
    except ValueError:
        return False


def enumera(cui_min: int, cui_max: int) -> Iterator[int]:
    """Toate CUI-urile valide din interval, crescator.

    Genereaza direct corpul + cifra de control, deci face ~1/10 din munca
    fata de testarea fiecarui numar.
    """
    if cui_min > cui_max:
        cui_min, cui_max = cui_max, cui_min
    corp_min, corp_max = cui_min // 10, cui_max // 10
    for corp in range(corp_min, corp_max + 1):
        c = corp * 10 + cifra_control(corp)
        if cui_min <= c <= cui_max:
            yield c


def loturi(cuis: List[int], marime: int = 100) -> Iterator[List[int]]:
    """Imparte lista in loturi (ANAF accepta maxim 100 de CUI-uri per request)."""
    for i in range(0, len(cuis), marime):
        yield cuis[i:i + marime]
