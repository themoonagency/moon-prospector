"""Puntea către MOON Chat: firme EXISTENTE pe cod CAEN → site → demo pe catalogul lor.

De ce e alt flux decât `colectare`:
    `colectare` ia firmele NOI de la ONRC. Pentru pitch-ul cu fișa Google e exact ce
    trebuie — o firmă de ieri chiar n-are fișă. Pentru MOON Chat e invers: o firmă
    înregistrată săptămâna asta n-are magazin, n-are catalog, n-are ce demo să i se
    facă. Aici interogăm firmele EXISTENTE după cod CAEN (4791 e ținta principală),
    le luăm site-ul din Google Places și îl trimitem în MOON Chat, care descoperă
    singur catalogul public și construiește demoul.

Buget: firmeapi costă 0,70 credite per REZULTAT, iar planul gratuit are 1.000/lună.
Deci ~1.400 de firme pe lună. De aia fiecare rulare are un buget propriu de credite
și ține minte de unde a rămas, ca să nu plătească de două ori aceleași firme.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import requests

from . import db, firmeapi, places

# Codurile care ne interesează pentru MOON Chat, în ordinea în care le atacăm.
# 4791 = comerț cu amănuntul prin internet. Restul sunt retail specializat: au
# magazin fizic, dar cei mai mulți vând și online.
CAEN_MAGAZINE = ["4791", "4771", "4772", "4776", "4778", "4759", "4765", "4764", "4763", "4799"]

# Servicii cu site de prezentare — pachetele de lead-gen, nu cele de magazin.
CAEN_SERVICII = ["5610", "9313", "8690", "5510", "9602", "4520"]

BAZA_MOONCHAT = os.getenv("MOONCHAT_URL", "https://chat.moonchat.ro")


@dataclass
class Rezultat:
    cui: int
    denumire: str
    site: Optional[str] = None
    stare: str = "nou"          # nou | fara_site | trimis | respins | eroare
    detaliu: str = ""


def _cheie_moonchat() -> Optional[str]:
    return os.getenv("MOONCHAT_KEY") or None


def trimite_in_moonchat(site: str, nume: str = "", timeout: int = 90) -> dict:
    """Cere lui MOON Chat să facă demo pentru site-ul ăsta.

    MOON Chat face singur toată treaba grea: găsește catalogul public (feed Merchant,
    Store API, products.json sau JSON-LD), verifică ce chat are deja pe site, creează
    tenantul de demo și sincronizează. Noi doar îi dăm domeniul.
    """
    k = _cheie_moonchat()
    if not k:
        raise RuntimeError("Lipsește MOONCHAT_KEY (wrangler secret put PROSPECT_KEY)")
    r = requests.post(
        f"{BAZA_MOONCHAT}/admin/api/prospect/creeaza",
        headers={"X-Prospect-Key": k, "Content-Type": "application/json"},
        data=json.dumps({"site": site, "nume": nume or ""}),
        timeout=timeout,
    )
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": f"MOON Chat a răspuns {r.status_code}"}


def firme_pe_caen(coduri: Iterable[str], max_credite: float = 200.0,
                  per_cod: int = 100, pauza: float = 0.2, log=None) -> List[firmeapi.FirmaAPI]:
    """Firme EXISTENTE pentru codurile CAEN date, fără filtru de dată.

    Se oprește la bugetul de credite. Cere doar firmele cu telefon: dacă n-au lăsat
    nici măcar un telefon nicăieri, șansa să aibă magazin online e mică.
    """
    out: dict[int, firmeapi.FirmaAPI] = {}
    credite = 0.0
    for cod in coduri:
        if credite >= max_credite:
            if log:
                log(f"     oprit la {credite:.0f} credite (bugetul rulării)")
            break
        pagina = 1
        luate = 0
        while luate < per_cod and credite < max_credite:
            params = {"caen": cod, "per_page": 20, "page": pagina, "telefon": 1}
            try:
                brut = firmeapi.interogheaza_brut(params)
            except Exception as e:
                if log:
                    log(f"     CAEN {cod}: {e}")
                break
            randuri = firmeapi._rezultate(brut)
            if not randuri:
                break
            credite += len(randuri) * firmeapi.CREDITE_PER_REZULTAT
            for d in randuri:
                f = firmeapi._to_firma(d)
                if f and f.cui not in out:
                    out[f.cui] = f
                    luate += 1
            if len(randuri) < 20:
                break
            pagina += 1
            time.sleep(pauza)
        time.sleep(pauza)
    if log:
        log(f"     {len(out)} firme, ~{credite:.0f} credite consumate")
    return list(out.values())


def _site_din_places(f, log=None) -> Optional[str]:
    """Site-ul firmei, de pe fișa ei de Google. ANAF nu dă niciodată site-ul."""
    try:
        p = places.verifica(getattr(f, "denumire", "") or "",
                            getattr(f, "localitate", "") or "",
                            getattr(f, "judet", "") or "")
    except Exception as e:
        if log:
            log(f"     Places: {e}")
        return None
    return getattr(p, "website", None)


def deja_incercat(cui: int) -> bool:
    """Nu plătim și nu deranjăm de două ori aceeași firmă."""
    try:
        with db.conexiune() as c:
            r = c.execute("SELECT note FROM prospecti WHERE cui = ?", (cui,)).fetchone()
    except Exception:
        return False
    if not r:
        return False
    note = r["note"] if isinstance(r, dict) or hasattr(r, "keys") else r[0]
    return bool(note and "moonchat:" in str(note))


def noteaza(cui: int, stare: str, detaliu: str = "") -> None:
    """Ține minte pe firmă ce s-a întâmplat, ca rulările următoare să nu repete."""
    try:
        with db.conexiune() as c:
            r = c.execute("SELECT note FROM prospecti WHERE cui = ?", (cui,)).fetchone()
            if r is None:
                return
            vechi = r["note"] if (isinstance(r, dict) or hasattr(r, "keys")) else r[0]
            vechi = str(vechi or "")
            nou = (vechi + " | " if vechi else "") + f"moonchat:{stare}"
            if detaliu:
                nou += f" ({detaliu[:120]})"
            c.execute("UPDATE prospecti SET note = ? WHERE cui = ?", (nou[:900], cui))
    except Exception:
        pass


def ruleaza(coduri: Optional[List[str]] = None, cate: int = 25,
            max_credite: float = 200.0, log=print) -> List[Rezultat]:
    """Fluxul întreg: firmeapi → Places → MOON Chat.

    `cate` e numărul de demo-uri pe care le cerem într-o rulare. MOON Chat are
    propriile lui plafoane și ne va spune „Plafon atins" când s-a ajuns la ele —
    atunci ne oprim, nu insistăm.
    """
    coduri = coduri or CAEN_MAGAZINE
    log(f"  1. Cer firme de la firmeapi pentru CAEN {', '.join(coduri)}")
    firme = firme_pe_caen(coduri, max_credite=max_credite, log=log)

    rez: List[Rezultat] = []
    trimise = 0
    log(f"  2. Caut site-ul fiecăreia pe Google Places")
    for f in firme:
        if trimise >= cate:
            break
        if deja_incercat(f.cui):
            continue
        site = _site_din_places(f, log=log)
        if not site:
            rez.append(Rezultat(f.cui, f.denumire, stare="fara_site"))
            noteaza(f.cui, "fara_site")
            continue
        log(f"     {f.denumire} → {site}")
        try:
            r = trimite_in_moonchat(site, f.denumire)
        except Exception as e:
            rez.append(Rezultat(f.cui, f.denumire, site, "eroare", str(e)))
            continue
        if r.get("ok"):
            trimise += 1
            rez.append(Rezultat(f.cui, f.denumire, site, "trimis", f"{r.get('continut', 0)} produse"))
            noteaza(f.cui, "demo", f"{r.get('continut', 0)} produse")
            log(f"       demo gata: {r.get('continut', 0)} produse ({r.get('sursa', '?')})")
        else:
            mesaj = str(r.get("error", ""))
            rez.append(Rezultat(f.cui, f.denumire, site, "respins", mesaj))
            noteaza(f.cui, "respins", mesaj)
            log(f"       respins: {mesaj}")
            if mesaj.startswith("Plafon atins"):
                log("  MOON Chat a atins plafonul. Mă opresc.")
                break

    log(f"  Gata: {trimise} demo-uri noi din {len(firme)} firme verificate.")
    return rez


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Trimite magazine existente în MOON Chat")
    ap.add_argument("--cate", type=int, default=25, help="câte demo-uri cerem")
    ap.add_argument("--credite", type=float, default=200.0, help="buget de credite firmeapi")
    ap.add_argument("--servicii", action="store_true", help="coduri de servicii (site de prezentare), nu magazine")
    a = ap.parse_args(argv)
    if not _cheie_moonchat():
        print("Lipsește MOONCHAT_KEY în mediu (aceeași valoare ca PROSPECT_KEY din worker).")
        return 1
    ruleaza(CAEN_SERVICII if a.servicii else CAEN_MAGAZINE, cate=a.cate, max_credite=a.credite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
