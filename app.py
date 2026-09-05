"""MOON Prospector — dashboard de aprobare și trimitere.

Rulare:  streamlit run app.py
"""
from __future__ import annotations

import os
import pathlib
from datetime import datetime

import streamlit as st

# Pe Streamlit Cloud cheile se pun in Settings -> Secrets, nu intr-un .env.
# Le mutam in mediu INAINTE de a importa modulele, ca sa le vada si ele.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    pass

from moon import contacte, db, mesaj, places
from moon.caen import ListaAlba

st.set_page_config(page_title="MOON Prospector", page_icon="🌙", layout="wide")

db.initializeaza()

ORE_OK = range(9, 18)          # 9:00-17:59
ZILE_OK = range(0, 5)          # luni-vineri


def moment_bun() -> tuple[bool, str]:
    a = datetime.now()
    if a.weekday() not in ZILE_OK:
        return False, "E weekend. Mesajele comerciale pe WhatsApp în weekend supără."
    if a.hour not in ORE_OK:
        return False, f"E ora {a.hour}:{a.minute:02d}. Trimite între 9:00 și 18:00, în zile lucrătoare."
    return True, ""

# ---------------------------------------------------------------- sidebar
@st.cache_data
def _logo_html() -> str:
    """Logo-ul THE MOON Agency, pe fundal negru (asa se foloseste mereu)."""
    import base64
    f = pathlib.Path(__file__).parent / "brand" / "logo-agency-red.png"
    if not f.exists():
        return ""
    b64 = base64.b64encode(f.read_bytes()).decode()
    return (
        '<div style="background:#08080a;border-radius:8px;padding:18px 20px 16px;'
        'margin:0 0 14px;text-align:center;">'
        f'<img src="data:image/png;base64,{b64}" style="width:100%;max-width:190px;'
        'display:block;margin:0 auto;">'
        '</div>'
        '<div style="font-size:12px;letter-spacing:.14em;text-transform:uppercase;'
        'color:#8a8a94;margin:-6px 0 10px;text-align:center;">Prospector</div>'
    )


# ---------------------------------------------------------------- acces
def _poarta() -> None:
    """Cere parola inainte sa se vada orice. Fara parola configurata, blocheaza.

    Parola sta in MOON_PAROLA: in .env local, in Secrets pe Streamlit Cloud.
    Aplicatia poate fi publica ca adresa - fara parola nu se vede nimic.
    """
    import hmac

    corecta = os.environ.get("MOON_PAROLA", "")
    if not corecta:
        st.error("Aplicația nu e configurată: lipsește **MOON_PAROLA** din Secrets.")
        st.caption("Settings → Secrets → adaugă  MOON_PAROLA = \"...\"  și repornește aplicația.")
        st.stop()

    if st.session_state.get("acces_permis"):
        return

    _, mij, _ = st.columns([1, 2, 1])
    with mij:
        html = _logo_html()
        if html:
            st.markdown(html, unsafe_allow_html=True)
        st.text_input("Parolă", type="password", key="_parola",
                      label_visibility="collapsed", placeholder="Parolă")
        if st.button("Intră", type="primary", width="stretch"):
            if hmac.compare_digest(st.session_state.get("_parola", ""), corecta):
                st.session_state["acces_permis"] = True
                del st.session_state["_parola"]
                st.rerun()
            else:
                st.error("Parolă greșită.")
    st.stop()


_poarta()

with st.sidebar:
    html = _logo_html()
    if html:
        st.markdown(html, unsafe_allow_html=True)
    else:
        st.title("MOON Prospector")

    semnatura = st.text_input("Semnătură", "Felix, THE MOON Agency")

    if st.button("Ieși din cont", width="stretch"):
        st.session_state.clear()
        st.rerun()

    st.divider()
    st.caption("Verificări active")
    st.write("✅ Google Places" if places.cheie() else
             "⚪ Google Places — fără cheie, mesajul nu pretinde că a căutat")
    st.write("✅ Administrator + email" if contacte.activ() else
             "⚪ Administrator + email — fără FIRMEAPI_KEY")

    st.divider()
    st.caption("Colectare")
    tiers = st.multiselect("Tier", ["A", "B", "C"], default=["A"])
    max_varsta = st.slider("Vechime maximă (zile)", 1, 30, 7)

    if st.button("Colectează firmele noi", type="primary", width="stretch"):
        from moon.pipeline import colectare
        with st.spinner("Interoghez ONRC și ANAF..."):
            try:
                j = colectare(tiers=",".join(tiers) or "A",
                              max_varsta=max_varsta, verbose=False)
                st.success(f"{j['adaugate']} prospecți noi din {j['gasite']} firme "
                           f"· {j['verificate_google']} verificate pe Google "
                           f"· {j['sarite_duplicat']} sărite (același telefon)")
            except Exception as e:
                st.error(f"Colectarea a eșuat: {e}")

    st.divider()
    with db.conexiune() as con:
        s = db.sumar(con)
    st.caption("Situație")
    etichete = {"nou": "De trimis", "trimis": "Trimise", "raspuns": "Răspunsuri",
                "client": "Clienți", "respins": "Respinse"}
    coloane = st.columns(2)
    for i, (k, eticheta) in enumerate(etichete.items()):
        if s.get(k):
            coloane[i % 2].metric(eticheta, s[k])
    st.caption(f"Total în baza de date: {sum(s.values())}")


def _actiune(cui: int, status: str, text: str | None = None, telefon: str | None = None,
             varianta: str | None = None):
    with db.conexiune() as con:
        db.seteaza_status(con, cui, status, text, varianta)
        if status == "blacklist" and telefon:
            db.adauga_blacklist(con, telefon, "cerere din dashboard")
    st.rerun()


ETICHETE_SCENARIU = {
    "nimic": "🔴 fără fișă Google",
    "fisa_slaba": "🟡 fișă goală",
    "are_tot": "🟢 are fișă și site",
    "neverificat": "⚪ neverificat",
}


def _card(r, cheie: str, text_mesaj: str, actiuni: list[tuple[str, str]],
          varianta: str | None = None):
    with st.container(border=True):
        sus, jos = st.columns([3, 2])
        with sus:
            st.markdown(f"**{r['denumire']}**  ·  `{r['tier']}`  ·  "
                        f"{ETICHETE_SCENARIU.get(r.get('scenariu'), '')}")
            st.caption(
                f"{r['caen']} {r['caen_denumire']} · {r['localitate'] or ''}"
                f"{', ' + r['judet'] if r['judet'] else ''} · "
                f"înmatriculată {r['data_inregistrare']} · CUI {r['cui']}"
            )
            detalii = [f"Propunere: {r['serviciu_moon']}"]
            if r.get("administrator"):
                detalii.append(f"Administrator: {r['administrator']}")
            if r.get("website"):
                detalii.append(f"Site: {r['website']}")
            if r.get("recenzii"):
                detalii.append(f"{r['recenzii']} recenzii")
            st.caption(" · ".join(detalii))
        with jos:
            st.markdown(f"### {r['telefon']}")

        text = st.text_area("Mesaj", text_mesaj, height=150, key=f"m{cheie}")

        coloane = st.columns(len(actiuni) + 1)
        with coloane[0]:
            st.link_button("Deschide WhatsApp", mesaj.link_whatsapp(r["telefon"], text),
                           type="primary", width="stretch")
        for col, (eticheta, status) in zip(coloane[1:], actiuni):
            with col:
                if st.button(eticheta, key=f"b{status}{cheie}", width="stretch"):
                    _actiune(r["cui"], status, text, r["telefon"], varianta)


# ---------------------------------------------------------------- continut
t_noi, t_follow, t_stat, t_toti = st.tabs(
    ["De trimis", "Follow-up", "Statistici", "Toți prospecții"])

with t_noi:
    ok, avertisment = moment_bun()
    if not ok:
        st.warning(avertisment)
    with db.conexiune() as con:
        randuri = db.prospecti(con, status="nou", limita=100)
    if not randuri:
        st.info("Niciun prospect nou. Apasă „Colectează firmele noi” din stânga.")
    else:
        st.caption(f"{len(randuri)} de trimis — verifică textul, apoi deschide WhatsApp "
                   "și trimite. După ce ai trimis, apasă „Trimis”.")
        for r in randuri:
            text, varianta = mesaj.compune(dict(r), semnatura)
            _card(r, f"n{r['cui']}", text,
                  [("Trimis", "trimis"), ("Respins", "respins"), ("Blacklist", "blacklist")],
                  varianta)

with t_follow:
    azi = datetime.now()
    with db.conexiune() as con:
        trimisi = db.prospecti(con, status="trimis", limita=200)
    de_urmarit = []
    for r in trimisi:
        if not r["data_trimitere"]:
            continue
        zile = (azi - datetime.fromisoformat(r["data_trimitere"])).days
        if zile >= 3:
            de_urmarit.append((r, zile))
    if not de_urmarit:
        st.info("Nimic de urmărit încă. Follow-up-ul apare la 3 zile după trimitere.")
    else:
        st.caption(f"{len(de_urmarit)} fără răspuns de 3+ zile.")
        for r, zile in sorted(de_urmarit, key=lambda x: -x[1]):
            st.caption(f"trimis acum {zile} zile")
            _card(r, f"f{r['cui']}", mesaj.compune_followup(dict(r), zile, semnatura),
                  [("Răspuns", "raspuns"), ("Client", "client"), ("Respins", "respins")],
                  r.get("varianta_mesaj"))

with t_stat:
    with db.conexiune() as con:
        sectiuni = [
            ("Pe tier", db.rata_pe_tier(con)),
            ("Pe scenariu (ce am găsit pe Google)", db.rata_pe_scenariu(con)),
            ("Pe nișă", db.rata_pe_nisa(con)),
            ("Pe județ", db.rata_pe_judet(con)),
            ("Pe variantă de mesaj", db.rata_pe_varianta(con)),
        ]
    if not any(x[1] for x in sectiuni):
        st.info("Statisticile apar după ce marchezi primele mesaje ca trimise.")
    else:
        total = sum(r["trimise"] for r in sectiuni[0][1])
        rasp = sum(r["raspunsuri"] or 0 for r in sectiuni[0][1])
        cli = sum(r["clienti"] or 0 for r in sectiuni[0][1])
        c1, c2, c3 = st.columns(3)
        c1.metric("Trimise", total)
        c2.metric("Răspunsuri", rasp, f"{round(100*rasp/total,1) if total else 0}%")
        c3.metric("Clienți", cli)
        for titlu, randuri in sectiuni:
            if not randuri:
                continue
            st.subheader(titlu)
            st.dataframe(
                [{"Grup": r["grup"], "Trimise": r["trimise"],
                  "Răspunsuri": r["raspunsuri"] or 0, "Rată": f"{r['rata']} %",
                  "Clienți": r["clienti"] or 0} for r in randuri],
                width="stretch", hide_index=True)

with t_toti:
    with db.conexiune() as con:
        randuri = db.prospecti(con, limita=500)
    if randuri:
        st.dataframe(
            [{"CUI": r["cui"], "Firmă": r["denumire"], "Tier": r["tier"],
              "Nișă": r["caen_denumire"], "Localitate": r["localitate"],
              "Telefon": r["telefon"], "Google": ETICHETE_SCENARIU.get(r.get("scenariu"), ""),
              "Status": r["status"], "Variantă": r.get("varianta_mesaj") or "",
              "Înmatriculată": r["data_inregistrare"]} for r in randuri],
            width="stretch", hide_index=True,
        )
    else:
        st.info("Baza de date e goală.")
