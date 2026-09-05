"""Generator de mesaje WhatsApp pentru prospecti.

Principii:
  - mesaj SCURT (WhatsApp, nu email) - sub 60 de cuvinte
  - afirmam DOAR ce am verificat: daca modulul places a intrebat Google, spunem
    "am cautat"; daca nu, formulam fara sa pretindem ca am verificat
  - patru scenarii, in functie de ce am gasit pe Google
  - oferta de intrare gratuita (Google Business Profile), site-ul e pasul doi
  - variatie deterministica: fiecare prospect primeste alta combinatie de fraze,
    ca sa nu trimiti de 40 de ori acelasi text si sa fii marcat ca spam
  - fiecare mesaj isi returneaza si eticheta variantei, ca sa poti masura ulterior
    ce formulare converteste
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional

# Formula de adresare. "dumneavoastra" e alegerea implicita pentru primul
# contact B2B in Romania; se schimba dintr-un singur loc.
ADRESARE = "dumneavoastra"

WA_BASE = "https://wa.me/"


@dataclass
class Grup:
    cheie: str
    eticheta: str
    cautare: str        # ce cauta clientul lui pe Google
    problema: str       # durerea concreta
    upsell: str         # ce vinzi dupa GBP


GRUPURI: Dict[str, Grup] = {
    "medical": Grup("medical", "medical", "{serviciu} {oras}",
                    "pacienții caută pe Google și sună din primele 3 rezultate de pe hartă",
                    "site de prezentare cu programari online"),
    "beauty": Grup("beauty", "beauty", "{serviciu} {oras}",
                   "clientele caută pe Google și aleg salonul după poze și recenzii",
                   "site cu programari online si campanii pe social media"),
    "fitness": Grup("fitness", "fitness", "sala fitness {oras}",
                    "lumea caută sala aproape de casă și compară pe Google",
                    "site cu abonamente si campanii de generare de lead-uri"),
    "horeca": Grup("horeca", "HoReCa", "{serviciu} {oras}",
                   "lumea caută pe Google și pe hartă înainte să aleagă unde merge",
                   "site cu meniu si rezervari, plus social media"),
    "retail": Grup("retail", "retail", "{serviciu} {oras}",
                   "cumpărătorii compară online înainte să vină în magazin",
                   "magazin online si Google Shopping"),
    "imobiliare": Grup("imobiliare", "imobiliare", "{serviciu} {oras}",
                       "clienții verifică agenția pe Google înainte să sune",
                       "site cu listari si campanii de generare de lead-uri"),
    "constructii": Grup("constructii", "constructii", "{serviciu} {oras}",
                        "clienții caută pe Google și sună firma care are lucrări de arătat",
                        "site de portofoliu si campanii Google Ads locale"),
    "servicii": Grup("servicii", "servicii profesionale", "{serviciu} {oras}",
                     "clienții verifică pe Google înainte să contacteze pe cineva",
                     "site de prezentare si optimizare pentru cautari"),
    "educatie": Grup("educatie", "educatie", "{serviciu} {oras}",
                     "părinții caută pe Google și compară opțiunile din zonă",
                     "site cu inscrieri online si campanii pe social media"),
    "turism": Grup("turism", "turism", "{serviciu} {oras}",
                   "lumea caută și rezervă online, aproape niciodată la telefon",
                   "site cu oferte si rezervari, plus campanii sezoniere"),
    "auto": Grup("auto", "auto", "{serviciu} {oras}",
                 "șoferii caută service aproape și sună din harta Google",
                 "site cu servicii si preturi, plus campanii locale"),
    "altele": Grup("altele", "general", "{serviciu} {oras}",
                   "clienții vă caută pe Google și nu vă găsesc",
                   "site de prezentare si prezenta online"),
}

_PREFIXE = [
    (("8621", "8622", "8623", "8691", "8693", "8695", "8696", "8710",
      "4773", "4774", "8730", "8810", "8899"), "medical"),
    (("9621", "9622", "9623", "4775"), "beauty"),
    (("9311", "9312", "9313", "9319", "8551"), "fitness"),
    (("5510", "5520", "5530", "5590", "5611", "5612", "5621", "5622", "5630"), "horeca"),
    (("6811", "6812", "6820", "6831"), "imobiliare"),
    (("7911", "7912", "7990"), "turism"),
    (("4781", "4782", "4783", "9531", "9532"), "auto"),
    (("8552", "8553", "8559", "8561", "8569", "8891"), "educatie"),
    (("6910", "6920", "7020", "7111", "7112", "7120", "7411", "7412", "7413",
      "7414", "7420", "7430", "7491", "7499", "7320", "7330"), "servicii"),
]


def grup_pentru(caen: Optional[str]) -> Grup:
    c = (caen or "").zfill(4)
    for coduri, cheie in _PREFIXE:
        if c in coduri:
            return GRUPURI[cheie]
    if c.startswith("47"):
        return GRUPURI["retail"]
    if c.startswith("41") or c.startswith("43"):
        return GRUPURI["constructii"]
    if c.startswith("95") or c.startswith("96"):
        return GRUPURI["servicii"]
    if c.startswith("59"):
        return GRUPURI["servicii"]
    return GRUPURI["altele"]


# --- variatii, ca sa nu trimiti de 40 de ori acelasi text ------------------

# Variantele sunt grupate pe scenariu. Scenariul vine din moon.places:
#   nimic       - am verificat pe Google si nu are nici fisa, nici site
#   fisa_slaba  - are fisa, dar e goala (fara recenzii, fara site)
#   are_tot     - are fisa activa si site -> nu e prospect de GBP
#   neverificat - nu avem cheie Places, deci nu pretindem ca am cautat

_DESCHIDERI_PF = [
    "Bună ziua, {pren}! Am văzut că v-ați autorizat activitatea în {oras}.",
    "Bună ziua, {pren}! Felicitări, am văzut că v-ați înregistrat acum în {oras}.",
    "Bună ziua, {pren}! Am observat că ați început activitatea în {oras}.",
]

_DESCHIDERI = [
    "Bună ziua! Am văzut că ați înființat {firma} în {oras}.",
    "Bună ziua! Felicitări pentru {firma}, am văzut că s-a înregistrat acum în {oras}.",
    "Bună ziua! Am observat că {firma} s-a deschis recent în {oras}.",
]

_CONSTATARI = {
    "nimic": [
        "Am căutat pe Google și încă nu apăreți nicăieri — nici pe hartă.",
        "Am verificat pe Google: firma nu apare încă în rezultate și nici pe hartă.",
        "V-am căutat pe Google Maps și nu sunteți acolo, iar {problema}.",
    ],
    "fisa_slaba": [
        "Am găsit fișa pe Google Maps, dar nu aveți încă site.",
        "Aveți fișă pe Google Maps, însă fără site — iar {problema}.",
        "V-am găsit pe Google Maps, dar site încă nu aveți.",
    ],
    "are_tot": [
        "Am văzut că aveți deja fișă pe Google și site — ați pornit bine.",
        "Aveți deja prezență pe Google și un site, ceea ce e mai mult decât au majoritatea "
        "firmelor la o săptămână.",
    ],
    "neverificat": [
        "La început, majoritatea firmelor noi nu apar pe Google Maps — iar {problema}.",
        "În primele săptămâni firmele noi nu apar în căutări, iar {problema}.",
    ],
}

_OFERTE = {
    "nimic": [
        "Vă configurez gratuit fișa de Google (Google Business Profile) — e ce aduce primele căutări.",
        "Pot să vă fac gratuit fișa de Google Maps, e cel mai rapid lucru care aduce clienți.",
        "Vă pot pune gratuit pe Google Maps, se rezolvă în 2-3 zile.",
    ],
    "fisa_slaba": [
        "Vă optimizez gratuit fișa — poze, program, servicii, descriere. Durează 2-3 zile.",
        "Pot să v-o duc la punct gratuit, ca să apăreți mai sus în căutările din zonă.",
        "V-o completez gratuit, ca lumea din zonă să vă găsească mai ușor.",
    ],
    "are_tot": [
        "Vă fac gratuit o analiză a prezenței online — ce funcționează și ce vă scapă.",
        "Pot să vă trimit gratuit un audit scurt: ce v-ar aduce mai mulți clienți de aici încolo.",
    ],
    "neverificat": [
        "Vă configurez gratuit fișa de Google (Google Business Profile) — e ce aduce primele căutări.",
        "Pot să vă fac gratuit fișa de Google Maps, e cel mai rapid lucru care aduce clienți.",
    ],
}

_INCHIDERI = [
    "Vă interesează?",
    "Vreți să v-o fac?",
    "Să v-o pregătesc?",
]

_INCHIDERI_AUDIT = [
    "Vi-l trimit?",
    "Vă interesează?",
]

_FOLLOWUP_3 = [
    "Bună ziua! Revin scurt legat de fișa de Google pentru {firma}. Rămâne valabil, durează 2-3 zile.",
    "Bună ziua! Verific doar dacă ați apucat să vedeți mesajul despre fișa Google pentru {firma}.",
]

_FOLLOWUP_7 = [
    "Bună ziua! Ultimul mesaj de la mine — dacă la un moment dat vreți să apăreți pe Google, scrieți-mi. Succes cu {firma}!",
    "Bună ziua! Nu vă mai deranjez. Dacă aveți nevoie de prezență online pentru {firma}, știți unde mă găsiți. Succes!",
]


def _alege(variante, samanta: str):
    """Alege determinist o variantă. Returnează (text, indice)."""
    h = int(hashlib.sha256(samanta.encode()).hexdigest()[:8], 16)
    i = h % len(variante)
    return variante[i], i


# Forme juridice care se elimina din denumire inainte de a o folosi in mesaj.
_SUFIXE = (
    "S.R.L.", "SRL", "S.A.", "SA", "S.R.L", "SNC", "SCS", "SCA",
    "PERSOANA FIZICA AUTORIZATA", "PERSOANĂ FIZICĂ AUTORIZATĂ", "P.F.A.", "PFA",
    "INTREPRINDERE INDIVIDUALA", "ÎNTREPRINDERE INDIVIDUALĂ", "I.I.", "II",
    "INTREPRINDERE FAMILIALA", "ÎNTREPRINDERE FAMILIALĂ", "I.F.", "IF",
)
# Titulaturi de profesie liberala: entitatea E o persoana, nu o firma.
_PROFESII = re.compile(
    r"\b(CABINET(UL)? (INDIVIDUAL )?(DE |MEDICAL |STOMATOLOGIC )?[A-ZĂÂÎȘȚ ]*|"
    r"MEDIC[A-ZĂÂÎȘȚ ]*|ASISTENT MEDICAL|MOA[SȘ]Ă|FIZIOTERAPEUT|PSIHOLOG[A-ZĂÂÎȘȚ]*|"
    r"AVOCAT|NOTAR[A-ZĂÂÎȘȚ ]*|EXPERT CONTABIL|BIROU[L]? INDIVIDUAL[A-ZĂÂÎȘȚ ]*)\b")
_RE_PF = re.compile(
    r"PERSOAN[ĂA] FIZIC[ĂA] AUTORIZAT[ĂA]|\bP\.?F\.?A\.?\b|"
    r"[ÎI]NTREPRINDERE (INDIVIDUAL[ĂA]|FAMILIAL[ĂA])|\bI\.?I\.?\b|"
    r"CABINET|MEDIC\b|ASISTENT MEDICAL|FIZIOTERAPEUT|PSIHOLOG|AVOCAT|NOTAR")


def este_persoana(denumire: str) -> bool:
    """True daca entitatea e de fapt o persoana (PFA, II, cabinet individual)."""
    return bool(_RE_PF.search((denumire or "").upper()))


# ONRC scrie diacriticele vechi cu sedila; le aducem la forma corecta.
_SEDILA = str.maketrans({"\u015e": "\u0218", "\u015f": "\u0219",
                         "\u0162": "\u021a", "\u0163": "\u021b"})
# Cuvinte de legatura care raman cu litera mica intr-o denumire.
_MICI = {"de", "si", "și", "la", "cu", "din", "pe", "pentru", "al", "ale", "a", "în", "in"}
# Abrevieri scurte care raman majuscule (AI, IT, TV, 3D...)
_NU_SUNT_CUVINTE = _MICI | {"o", "un"}


def _titlu(cuvinte):
    out = []
    for i, c in enumerate(cuvinte):
        jos = c.lower()
        if i > 0 and jos in _MICI:
            out.append(jos)
        elif len(c) <= 3 and jos not in _NU_SUNT_CUVINTE:
            out.append(c)                      # acronim scurt: AI, DC, VSNS
        elif len(c) <= 4 and not set(c) & set("AEIOUĂÂÎ"):
            out.append(c)                      # acronim fara vocale: VSNS
        else:
            out.append("-".join(x.capitalize() for x in c.split("-")))
    return " ".join(out)


# Prenume romanesti frecvente. Folosite doar ca sa stim CARE token din denumire
# e prenumele - ONRC scrie de obicei NUME Prenume, dar nu intotdeauna.
_PRENUME = {
 "adela","adina","adrian","adriana","alberto","alex","alexandra","alexandru","alin","alina",
 "amalia","ana","anca","andra","andrada","andreea","andrei","aneta","angela","anisoara","anton",
 "antonia","antonio","ariana","armand","aurel","aurelia","aurora","beatrice","bianca","bogdan",
 "bogdana","camelia","carla","carmen","catalin","catalina","cezar","ciprian","claudia","claudiu",
 "codrin","codruta","constantin","corina","cornel","cornelia","cosmin","cosmina","costel","cristi",
 "cristian","cristina","dan","dana","daniel","daniela","daria","darius","david","delia","denisa",
 "diana","dorin","dorina","dragos","dumitru","ecaterina","eduard","elena","eliza","emanuel","emil",
 "emilia","eugen","eugenia","fabian","felicia","felix","filip","flavia","flaviu","florentina",
 "florian","florin","florina","gabriel","gabriela","genoveva","george","georgiana","geta","ghita",
 "gheorghe","grigore","horia","horatiu","iancu","ilie","ileana","ioan","ioana","ion","ionel",
 "ionela","ionut","irina","iulia","iulian","iuliana","iustin","ivan","larisa","laura","laurentiu",
 "lavinia","lenuta","leonard","liliana","liviu","loredana","lucia","lucian","luciana","ludovic",
 "luiza","madalin","madalina","magda","magdalena","marcel","marcela","marian","mariana","marilena",
 "marin","marina","marius","maria","mihai","mihaela","mihail","mircea","mirela","mirona","monica",
 "narcis","narcisa","nadia","nelu","niculina","nicolae","nicoleta","nicu","nicusor","noemi",
 "octavian","olga","olimpia","oana","ovidiu","pavel","paul","paula","petre","petru","petronela",
 "radu","rares","raluca","ramona","razvan","rebeca","remus","robert","roberta","roxana","ruxandra",
 "sabina","samuel","sandra","sebastian","sergiu","silvia","silviu","simona","sorin","sorina",
 "stefan","stefania","stelian","tatiana","teodor","teodora","tiberiu","tudor","valentin",
 "valentina","valeria","valeriu","vasile","veronica","victor","victoria","viorel","viorica",
 "vlad","vladimir","zamfir","zoe","neculai","gherghina","aurica","doina","elisabeta","ionica","vasilica","constanta","maricica","floarea","lacramioara","sanda","rodica","costica","dorel","gabi","geanina","iuliu","leon","lidia","lili","marioara","mihaita","nadina","olimpiu","ovidiu-ionut","petrica","sabin","stela","tanase","toma","valica","aniko","anikö","augustin","liviu-paul","paul-liviu",
}


def _curata(nume: str) -> str:
    """VSNS DENTAL S.R.L. -> VSNS Dental; sedila -> virgula; 'De'/'Si' raman mici."""
    import re as _re
    n = (nume or "").strip().translate(_SEDILA)
    up = n.upper()
    for suf in _SUFIXE:
        if up.endswith(" " + suf.upper()):
            n = n[: -len(suf) - 1]
            break
    n = _PROFESII.sub("", n)
    n = _re.sub(r"\s*[-–]\s*$", "", n).strip(" .,-")
    n = _re.sub(r"\s{2,}", " ", n)
    return n if not n.isupper() else _titlu(n.split())


def _fara_diacritice(x: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", x.lower())
                   if not unicodedata.combining(c))


def prenume(denumire: str):
    """Prenumele persoanei din denumire, sau None daca nu putem fi siguri.

    ONRC scrie de obicei NUME Prenume ("CIOIENARU LAURA-ELENA"), dar nu mereu
    ("ALINA FARCAS - MEDIC"). Decidem dupa o lista de prenume frecvente; daca
    nu se potriveste nimic, preferam mesajul fara nume decat sa ne adresam
    cuiva cu numele de familie.
    """
    import re as _re
    parti = [x for x in _curata(denumire).split()
             if x and not _re.fullmatch(r"[A-ZĂÂÎȘȚa-zăâîșț]\.?", x)]
    if len(parti) < 2:
        return None

    def e_prenume(tok):
        prim = tok.split("-")[0].strip(".,")
        return len(prim) > 1 and _fara_diacritice(prim) in _PRENUME

    def curat(tok):
        return tok.split("-")[0].strip(".,").capitalize()

    p0, p1 = parti[0], parti[1]
    if e_prenume(p1):
        return curat(p1)          # NUME Prenume (conventia ONRC)
    if e_prenume(p0):
        return curat(p0)          # Prenume NUME
    return None


def _curata(nume: str) -> str:
    """VSNS DENTAL S.R.L. -> VSNS Dental; sedila -> virgula; 'De'/'Si' raman mici."""
    import re as _re
    n = (nume or "").strip().translate(_SEDILA)
    up = n.upper()
    for suf in _SUFIXE:
        if up.endswith(" " + suf.upper()):
            n = n[: -len(suf) - 1]
            break
    n = _PROFESII.sub("", n)
    n = _re.sub(r"\s*[-–]\s*$", "", n).strip(" .,-")
    n = _re.sub(r"\s{2,}", " ", n)
    return n if not n.isupper() else _titlu(n.split())


def _fara_diacritice(x: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", x.lower())
                   if not unicodedata.combining(c))


def compune(prospect: dict, semnatura: str = "Felix, THE MOON Agency",
            scenariu: str | None = None) -> tuple[str, str]:
    """Construiește mesajul de prim contact.

    Returnează (text, varianta) — varianta e eticheta de urmărit în statistici,
    de forma "nimic:d1c0o2i1".
    """
    firma = _curata(prospect.get("denumire", ""))
    oras = prospect.get("localitate") or prospect.get("judet") or "zona"
    grup = grup_pentru(prospect.get("caen"))
    s = str(prospect.get("cui", firma))

    if scenariu is None:
        scenariu = prospect.get("scenariu") or "neverificat"
    if scenariu not in _CONSTATARI:
        scenariu = "neverificat"

    pf = este_persoana(prospect.get("denumire", ""))
    pren = prenume(prospect.get("denumire", "")) if pf else None
    deschidere, i_d = _alege(_DESCHIDERI_PF if pren else _DESCHIDERI, s + "d")
    constatare, i_c = _alege(_CONSTATARI[scenariu], s + "c")
    oferta, i_o = _alege(_OFERTE[scenariu], s + "o")
    inchidere, i_i = _alege(
        _INCHIDERI_AUDIT if scenariu == "are_tot" else _INCHIDERI, s + "i")

    salut = deschidere.format(firma=firma, oras=oras, pren=pren or "")
    # Dacă știm numele administratorului, personalizăm adresarea.
    adresare = prospect.get("adresare") or _adresare(prospect)
    if adresare:
        salut = salut.replace("Bună ziua!", f"Bună ziua, {adresare}!", 1)

    corp = constatare.format(problema=grup.problema)
    varianta = f"{scenariu}:d{i_d}c{i_c}o{i_o}i{i_i}"
    return f"{salut} {corp}\n\n{oferta} {inchidere}\n\n{semnatura}", varianta


def _adresare(prospect: dict):
    from .contacte import nume_pentru_adresare
    return nume_pentru_adresare(prospect.get("administrator"))


def compune_followup(prospect: dict, zi: int, semnatura: str = "Felix, THE MOON Agency") -> str:
    firma = _curata(prospect.get("denumire", ""))
    s = str(prospect.get("cui", firma))
    variante = _FOLLOWUP_3 if zi <= 4 else _FOLLOWUP_7
    return f"{_alege(variante, s + str(zi)).format(firma=firma)}\n\n{semnatura}"


def link_whatsapp(telefon: str, mesaj: str) -> str:
    """Link wa.me care deschide conversatia cu mesajul deja scris."""
    numar = telefon.lstrip("+").replace(" ", "")
    return f"{WA_BASE}{numar}?text={urllib.parse.quote(mesaj)}"
