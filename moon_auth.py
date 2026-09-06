"""Poarta cu parola + cookie "ramai logat" pentru aplicatiile MOON pe Streamlit.

De ce exista: Streamlit tine starea in memorie per sesiune de browser, deci la
fiecare refresh / tab nou / repornire a aplicatiei se cerea parola din nou.
Aici parola ramane la fel, dar dupa login se scrie in browser un cookie cu un
token semnat HMAC-SHA256 care il tine logat 30 de zile.

Cookie-ul NU contine parola. Contine doar "v1.<expira_la>.<semnatura>".
Semnatura se face cu MOON_COOKIE_SECRET; daca secretul lipseste, se deriva din
MOON_PAROLA - adica schimbarea parolei invalideaza automat toate cookie-urile.

Folosire in app.py:

    import moon_auth
    moon_auth.poarta(antet_html=_logo_html())   # in locul vechiului _poarta()
    ...
    if st.button("Iesi din cont"):
        moon_auth.iesire()

Secrete (Streamlit: Settings -> Secrets; local: .env):
    MOON_PAROLA        obligatoriu
    MOON_COOKIE_SECRET optional (recomandat pe termen lung)
    MOON_COOKIE_ZILE   optional, implicit 30

Dependinta:  streamlit-cookies-controller>=0.0.4
Fara ea modulul functioneaza tot, dar fara "ramai logat".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from datetime import datetime, timedelta, timezone

import streamlit as st

try:
    from streamlit_cookies_controller import CookieController
except Exception:                                    # pachetul nu e instalat
    CookieController = None                          # type: ignore[assignment]

_STARE = "moon_cookies"                              # cheia din session_state


def _nume_cookie() -> str:
    return os.environ.get("MOON_COOKIE_NUME", "moon_sesiune")


def _zile() -> int:
    try:
        return max(1, int(os.environ.get("MOON_COOKIE_ZILE", "30")))
    except ValueError:
        return 30


def _parola() -> str:
    return os.environ.get("MOON_PAROLA", "")


def _secret() -> bytes:
    s = os.environ.get("MOON_COOKIE_SECRET", "") or ("moon:" + _parola())
    return hashlib.sha256(s.encode()).digest()


def _semneaza(corp: str) -> str:
    d = hmac.new(_secret(), corp.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(d).decode().rstrip("=")


def _emite(zile: int) -> str:
    corp = "v1.%d" % (int(time.time()) + zile * 86400)
    return "%s.%s" % (corp, _semneaza(corp))


def _verifica(token) -> int:
    """Secundele ramase din token, sau 0 daca e lipsa / falsificat / expirat."""
    if not isinstance(token, str) or token.count(".") != 2:
        return 0
    versiune, expira, semnatura = token.split(".")
    if versiune != "v1":
        return 0
    if not hmac.compare_digest(semnatura, _semneaza("%s.%s" % (versiune, expira))):
        return 0
    try:
        return max(0, int(expira) - int(time.time()))
    except ValueError:
        return 0


# ---------------------------------------------------------------- cookie
def _controller():
    """Controllerul citeste cookie-urile o singura data pe sesiune.

    Prima rulare a scriptului returneaza {} (raspunsul din browser inca nu a
    ajuns), apoi componenta declanseaza singura un rerun cu valorile reale.
    """
    if CookieController is None:
        return None
    try:
        return CookieController(key=_STARE)
    except Exception:
        return None


def _scrie(ctrl, zile: int) -> None:
    try:
        ctrl.set(
            _nume_cookie(),
            _emite(zile),
            expires=datetime.now(timezone.utc) + timedelta(days=zile),
            max_age=zile * 86400,
            same_site="lax",
        )
    except Exception:
        pass


def _reinnoieste(ctrl, ramas: int) -> None:
    """Prelungeste cookie-ul cand mai are sub 7 zile - o singura data pe sesiune."""
    if ctrl is None or not ramas or st.session_state.get("_cookie_reinnoit"):
        return
    st.session_state["_cookie_reinnoit"] = True
    if ramas < 7 * 86400:
        _scrie(ctrl, _zile())


# ---------------------------------------------------------------- poarta
def poarta(antet_html: str = "") -> None:
    """Blocheaza aplicatia pana la login. Cu cookie valid, trece direct."""
    corecta = _parola()
    if not corecta:
        st.error("Aplicația nu e configurată: lipsește **MOON_PAROLA** din Secrets.")
        st.caption('Settings → Secrets → adaugă  MOON_PAROLA = "..."  și repornește aplicația.')
        st.stop()

    prima_rulare = _STARE not in st.session_state
    ctrl = _controller()

    if st.session_state.get("acces_permis"):
        if ctrl is not None:
            _reinnoieste(ctrl, _verifica(ctrl.get(_nume_cookie())))
        return

    ramas = _verifica(ctrl.get(_nume_cookie())) if ctrl is not None else 0
    if ramas:
        st.session_state["acces_permis"] = True
        _reinnoieste(ctrl, ramas)
        return

    _, mij, _ = st.columns([1, 2, 1])

    # Cookie-urile din browser nu au ajuns inca: nu aratam formularul degeaba.
    if ctrl is not None and prima_rulare:
        with mij:
            if antet_html:
                st.markdown(antet_html, unsafe_allow_html=True)
            st.caption("Se verifică sesiunea…")
        st.stop()

    with mij:
        if antet_html:
            st.markdown(antet_html, unsafe_allow_html=True)
        st.text_input("Parolă", type="password", key="_parola",
                      label_visibility="collapsed", placeholder="Parolă")
        tine_minte = st.checkbox("Ține-mă logat %d de zile" % _zile(),
                                 value=True, disabled=ctrl is None)
        if st.button("Intră", type="primary", width="stretch"):
            if hmac.compare_digest(st.session_state.get("_parola", ""), corecta):
                st.session_state["acces_permis"] = True
                st.session_state.pop("_parola", None)
                if ctrl is not None and tine_minte:
                    _scrie(ctrl, _zile())
                    time.sleep(0.25)          # lasa cookie-ul sa ajunga in browser
                st.rerun()
            else:
                st.error("Parolă greșită.")
        if ctrl is None:
            st.caption("Fără „ține-mă logat”: lipsește pachetul "
                       "`streamlit-cookies-controller`.")
    st.stop()


def iesire() -> None:
    """Sterge cookie-ul si sesiunea."""
    ctrl = _controller()
    if ctrl is not None:
        try:
            ctrl.remove(_nume_cookie())
        except Exception:
            pass
        time.sleep(0.25)
    st.session_state.clear()
    st.rerun()
