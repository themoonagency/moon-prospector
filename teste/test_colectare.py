"""Teste pentru colectare: sondarea ANAF, caderea de pe ONRC, jurnalul, setarile din panou.

Fara retea si fara baza reala: SQLite temporar, ANAF si ONRC simulate, date inventate
(repo-ul e public - aici nu intra nume, telefoane sau CUI-uri reale).

Rulare:  python3 -m unittest discover -s teste -v
Optional, si pe Postgres:  TEST_DATABASE_URL=postgresql://... python3 -m unittest discover -s teste
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

RADACINA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADACINA))

import requests  # noqa: E402

from moon import anaf, contacte, cui, db, firmeapi, mesaj, onrc, pipeline, places, setari, sondare  # noqa: E402
from moon.caen import ListaAlba  # noqa: E402

# moon/__init__.py incarca .env-ul de pe Mac: nimic din el nu are voie sa ajunga in teste.
for _k in ("DATABASE_URL", "MOON_DATABASE_URL", "GOOGLE_PLACES_API_KEY", "FIRMEAPI_KEY",
           "MOON_TIERS", "MOON_MAX_AGE_DAYS"):
    os.environ.pop(_k, None)
PG_TEST = os.environ.get("TEST_DATABASE_URL")

AZI = date.today().isoformat()


# ------------------------------------------------------------------ ANAF simulat
def registru(frontiera: int, sub: int = 300, peste: int = 900, densitate: int = 2,
             gauri=(), caen=("9622", "5611", "4100", "6210")) -> dict:
    """Firme inventate pe CUI-uri valide in jurul frontierei.

    `densitate` = una din cate CUI-uri valide exista la ANAF; `gauri` = intervale (lo, hi)
    de CUI-uri fara nicio firma. Frontiera insasi exista mereu.
    """
    reg = {}
    valide = list(cui.enumera(frontiera - sub * 10, frontiera + peste * 10))
    for i, c in enumerate(valide):
        if c != frontiera and (i % densitate or any(lo <= c <= hi for lo, hi in gauri)):
            continue
        reg[c] = anaf.Firma(
            cui=c, denumire=f"FIRMA TEST {i} SRL", adresa="JUD. CLUJ, MUN. CLUJ-NAPOCA, STR. A, NR.1",
            telefon="07" + f"{i:08d}", caen=caen[(i // densitate) % len(caen)], data_inregistrare=AZI,
            stare=f"INREGISTRAT din data {AZI}", nr_reg_com=f"J12/{i}/2026")
    return reg


class AnafFals:
    """Inlocuieste anaf.interogheaza_lot: raspunde din registru, poate pica la cererea N."""

    def __init__(self, reg: dict, pica_la: int | None = None, exceptie=None):
        self.reg, self.pica_la, self.cereri = reg, pica_la, []
        self.exceptie = exceptie or anaf.AnafIndisponibil("nu răspunde (HTTP 503)", 503)

    def __call__(self, lot, **kw):
        assert len(lot) <= 100
        self.cereri.append(list(lot))
        if self.pica_la is not None and len(self.cereri) >= self.pica_la:
            raise self.exceptie
        return {c: self.reg[c] for c in lot if c in self.reg}


def eroare_http(cod: int) -> requests.exceptions.HTTPError:
    r = requests.Response()
    r.status_code = cod
    r.url = onrc.URL
    return requests.exceptions.HTTPError(f"{cod} Client Error: Forbidden for url: {onrc.URL}",
                                         response=r)


# ------------------------------------------------------------------ cui
class TestCui(unittest.TestCase):
    def test_enumera_de_la_un_cui_la_zece_numere(self):
        g = cui.enumera_de_la(55_600_000)
        lot = [next(g) for _ in range(100)]
        self.assertTrue(all(cui.valid(c) for c in lot))
        self.assertEqual(lot, list(cui.enumera(55_600_000, lot[-1])))
        self.assertLess(lot[-1] - lot[0], 1000)
        self.assertTrue(lot[0] >= 55_600_000)

    def test_enumera_de_la_include_startul_valid(self):
        for f in (55_542_190, 55_539_615):
            self.assertTrue(cui.valid(f))
            self.assertEqual(next(cui.enumera_de_la(f)), f)


# ------------------------------------------------------------------ anaf.interogheaza_lot
class Raspuns:
    def __init__(self, cod=200, corp=None, text=None):
        self.status_code, self._corp, self.text = cod, corp, text

    def json(self):
        if self._corp is None:
            raise ValueError("nu e JSON")
        return self._corp


class SesiuneFalsa:
    def __init__(self, *raspunsuri):
        self.raspunsuri, self.cereri = list(raspunsuri), []

    def post(self, url, json=None, timeout=None):
        self.cereri.append(json)
        r = self.raspunsuri.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def bloc(c, caen="9622"):
    return {"date_generale": {"cui": c, "denumire": "X SRL", "adresa": "JUD. CLUJ",
                              "telefon": "0712345678", "cod_CAEN": caen,
                              "data_inregistrare": AZI, "stare_inregistrare": "INREGISTRAT",
                              "nrRegCom": "J1/1/2026"}}


class TestAnafLot(unittest.TestCase):
    def setUp(self):
        self.somn = []
        self.dormi = self.somn.append

    def lot(self, ses, **kw):
        return anaf.interogheaza_lot([10, 28, 36], sesiune=ses, dormi=self.dormi, **kw)

    def test_lot_plin(self):
        ses = SesiuneFalsa(Raspuns(200, {"cod": 200, "found": [bloc(10), bloc(28)], "notFound": [36]}))
        r = self.lot(ses)
        self.assertEqual(sorted(r), [10, 28])
        self.assertEqual(r[10].caen, "9622")
        self.assertEqual(self.somn, [1.2])                 # pauza dupa fiecare cerere
        self.assertEqual(ses.cereri[0][0], {"cui": 10, "data": AZI})

    def test_lot_gol(self):
        ses = SesiuneFalsa(Raspuns(200, {"cod": 200, "found": [], "notFound": [10, 28, 36]}))
        self.assertEqual(self.lot(ses), {})

    def test_404_cu_json_anaf_inseamna_gol(self):
        ses = SesiuneFalsa(Raspuns(404, {"cod": 404, "found": [], "notFound": [10, 28, 36]}))
        self.assertEqual(self.lot(ses), {})

    def test_5xx_apoi_ok(self):
        ses = SesiuneFalsa(Raspuns(503, None, "<html>"), Raspuns(502, None),
                           Raspuns(200, {"found": [bloc(36)], "notFound": []}))
        self.assertEqual(list(self.lot(ses)), [36])
        self.assertEqual(len(ses.cereri), 3)
        self.assertIn(anaf.ASTEPTARI[0], self.somn)
        self.assertIn(anaf.ASTEPTARI[1], self.somn)

    def test_429_se_reincearca(self):
        ses = SesiuneFalsa(Raspuns(429, None), Raspuns(200, {"found": [], "notFound": []}))
        self.assertEqual(self.lot(ses), {})

    def test_timeout_peste_tot(self):
        ses = SesiuneFalsa(*[requests.exceptions.ReadTimeout("t")] * 4)
        with self.assertRaises(anaf.AnafIndisponibil) as c:
            self.lot(ses)
        self.assertIn("timeout", c.exception.motiv)
        self.assertEqual(len(ses.cereri), 4)

    def test_conexiune_cazuta_apoi_ok(self):
        ses = SesiuneFalsa(requests.exceptions.ConnectionError("x"),
                           Raspuns(200, {"found": [bloc(10)], "notFound": []}))
        self.assertEqual(list(self.lot(ses)), [10])

    def test_5xx_peste_tot(self):
        ses = SesiuneFalsa(*[Raspuns(503, None)] * 4)
        with self.assertRaises(anaf.AnafIndisponibil) as c:
            self.lot(ses)
        self.assertEqual(c.exception.http, 503)
        self.assertIn("HTTP 503", c.exception.motiv)

    def test_raspuns_stricat(self):
        ses = SesiuneFalsa(*[Raspuns(200, None, "<html>pagina de mentenanta</html>")] * 4)
        with self.assertRaises(anaf.AnafIndisponibil) as c:
            self.lot(ses)
        self.assertIn("stricat", c.exception.motiv)

    def test_4xx_fara_reincercare(self):
        ses = SesiuneFalsa(Raspuns(400, {"cod": 400, "message": "bad"}), Raspuns(200, {"found": []}))
        with self.assertRaises(anaf.AnafRefuza):
            self.lot(ses)
        self.assertEqual(len(ses.cereri), 1)

    def test_403_blocat(self):
        ses = SesiuneFalsa(Raspuns(403, None, "<html>blocat</html>"))
        with self.assertRaises(anaf.AnafRefuza) as c:
            self.lot(ses)
        self.assertEqual(c.exception.motiv, "blochează cererile noastre (HTTP 403)")
        self.assertEqual(len(ses.cereri), 1)

    def test_maxim_100(self):
        with self.assertRaises(ValueError):
            anaf.interogheaza_lot(list(range(101)), sesiune=SesiuneFalsa(), dormi=self.dormi)


class TestAnafHttpLocal(unittest.TestCase):
    """Acelasi client, dar prin `requests` adevarat, pe un server HTTP local (fara internet)."""

    def setUp(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.raspunsuri, self.primite = [], []
        test = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                corp = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                test.primite.append((self.headers.get("Content-Type"), json.loads(corp)))
                cod, text, intarziere = test.raspunsuri.pop(0)
                if intarziere:
                    import time
                    time.sleep(intarziere)
                self.send_response(cod)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(text.encode())

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/api/PlatitorTvaRest/v9/tva"
        self.ses = anaf.sesiune_noua()
        self.ses.trust_env = False                       # fara proxy-ul mediului

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_503_apoi_ok_prin_http(self):
        self.raspunsuri = [(503, "<html>indisponibil</html>", 0),
                           (200, json.dumps({"cod": 200, "found": [bloc(10)], "notFound": [28]}), 0)]
        with mock.patch.object(anaf, "URL", self.url):
            r = anaf.interogheaza_lot([10, 28], sesiune=self.ses, dormi=lambda s: None)
        self.assertEqual(list(r), [10])
        self.assertEqual(len(self.primite), 2)
        self.assertEqual(self.primite[0][0], "application/json")
        self.assertEqual(self.primite[0][1], [{"cui": 10, "data": AZI}, {"cui": 28, "data": AZI}])

    def test_timeout_prin_http(self):
        self.raspunsuri = [(200, "{}", 1.5)] * 4
        with mock.patch.object(anaf, "URL", self.url):
            with self.assertRaises(anaf.AnafIndisponibil) as c:
                anaf.interogheaza_lot([10], sesiune=self.ses, timeout=0.3, dormi=lambda s: None)
        self.assertIn("timeout", c.exception.motiv)


# ------------------------------------------------------------------ sondare
class TestSondare(unittest.TestCase):
    F = next(cui.enumera_de_la(55_600_000))

    def test_se_opreste_dupa_k_loturi_goale(self):
        reg = registru(self.F, sub=0, peste=950)
        fals = AnafFals(reg)
        s = sondare.sondeaza(self.F + 1, fals, k=3, plafon=80)
        self.assertEqual(s.motiv, "capat")
        self.assertEqual(s.ultim_gasit, max(reg))
        self.assertEqual(len(s.firme), len(reg) - 1)       # fara frontiera
        # ~950 de CUI-uri cu firme = 10 loturi, + 3 goale
        self.assertEqual(s.loturi, 13)
        self.assertEqual(s.interogate, 1300)

    def test_o_gaura_de_2_loturi_nu_opreste(self):
        g = cui.enumera_de_la(self.F + 1)
        sir = [next(g) for _ in range(900)]
        gaura = (sir[300], sir[499])                          # 200 de CUI-uri fara firme
        reg = registru(self.F, sub=0, peste=900, densitate=1, gauri=[gaura])
        s = sondare.sondeaza(self.F + 1, AnafFals(reg), k=3)
        self.assertEqual(s.motiv, "capat")
        self.assertEqual(s.ultim_gasit, max(reg))

    def test_sub_capatul_onrc_nu_se_opreste(self):
        g = cui.enumera_de_la(self.F + 1)
        sir = [next(g) for _ in range(900)]
        gaura = (sir[100], sir[599])                          # 5 loturi goale
        reg = registru(self.F, sub=0, peste=900, densitate=1, gauri=[gaura])
        s = sondare.sondeaza(self.F + 1, AnafFals(reg), k=3)
        self.assertLess(s.ultim_gasit, max(reg))              # fara indiciu: se opreste in gaura
        s = sondare.sondeaza(self.F + 1, AnafFals(reg), k=3, nu_te_opri_sub=max(reg))
        self.assertEqual(s.ultim_gasit, max(reg))             # cu indiciu ONRC: trece de ea

    def test_plafon(self):
        reg = registru(self.F, sub=0, peste=2000)
        s = sondare.sondeaza(self.F + 1, AnafFals(reg), k=3, plafon=5)
        self.assertEqual((s.motiv, s.loturi), ("plafon", 5))
        self.assertTrue(s.ultim_gasit < max(reg))

    def test_eroare_la_jumatate_pastreaza_ce_a_gasit(self):
        reg = registru(self.F, sub=0, peste=900)
        s = sondare.sondeaza(self.F + 1, AnafFals(reg, pica_la=3), k=3)
        self.assertEqual((s.motiv, s.loturi), ("eroare", 2))
        self.assertIsInstance(s.eroare, anaf.AnafIndisponibil)
        self.assertTrue(s.firme)
        self.assertTrue(all(c <= s.cui_max for c in s.firme))


# ------------------------------------------------------------------ setari
class TestSetari(unittest.TestCase):
    def test_curata_setari(self):
        self.assertEqual(setari.curata_setari({"tiers": ["b", "A"], "max_varsta": "3", "sursa": "anaf"}),
                         ({"tiers": ["A", "B"], "max_varsta": 3, "sursa": "anaf"}, 0))
        c, gresite = setari.curata_setari({"tiers": ["D"], "max_varsta": 99, "sursa": "onrc"})
        self.assertEqual((c, gresite), ({}, 3))
        self.assertEqual(setari.curata_setari("nu e obiect"), ({}, 1))
        self.assertEqual(setari.curata_setari({"max_varsta": True}), ({}, 1))

    def test_ordinea(self):
        r = lambda *a: setari.rezolva(*a)[0]  # noqa: E731
        panou = {"tiers": ["B"], "max_varsta": 3, "sursa": "anaf"}
        self.assertEqual(r({}, panou, {}), {"tiers": "B", "max_varsta": 3, "sursa": "anaf"})
        self.assertEqual(r({"tiers": "A,C", "max_varsta": "10", "sursa": "auto"}, panou, {}),
                         {"tiers": "A,C", "max_varsta": 10, "sursa": "auto"})
        # inputs goale = din panou
        self.assertEqual(r({"tiers": "", "max_varsta": "", "sursa": ""}, panou, {}),
                         {"tiers": "B", "max_varsta": 3, "sursa": "anaf"})
        # panou cerut dar gol -> variabila din GitHub (MOON_TIERS) -> implicit A / 7 / auto
        self.assertEqual(r({}, {}, {"MOON_TIERS": "A,B"}), {"tiers": "A,B", "max_varsta": 7, "sursa": "auto"})
        self.assertEqual(r({}, {}, {}), {"tiers": "A", "max_varsta": 7, "sursa": "auto"})
        # fara --din-setari: ca inainte (A,B)
        self.assertEqual(r({}, None, {})["tiers"], "A,B")

    def test_valori_explicite_gresite_se_ignora(self):
        panou = {"tiers": ["B"], "max_varsta": 3, "sursa": "anaf"}
        s, gresite = setari.rezolva({"tiers": "X", "max_varsta": "sapte", "sursa": "google"}, panou, {})
        self.assertEqual(s, {"tiers": "B", "max_varsta": 3, "sursa": "anaf"})
        self.assertEqual(gresite, ["tiers", "max_varsta", "sursa"])

    def test_in_actions_doar_auto_si_anaf(self):
        panou = {"sursa": "anaf"}
        actions = {"GITHUB_ACTIONS": "true"}
        for gresita in ("firmeapi", "onrc"):
            s, gresite = setari.rezolva({"sursa": gresita}, panou, actions)
            self.assertEqual((s["sursa"], gresite), ("anaf", ["sursa"]), gresita)
        self.assertEqual(setari.rezolva({"sursa": "firmeapi"}, None, actions)[0]["sursa"], "auto")
        self.assertEqual(setari.rezolva({"sursa": "auto"}, panou, actions), ({"tiers": "A", "max_varsta": 7, "sursa": "auto"}, []))
        # local, din linia de comanda: raman toate
        self.assertEqual(setari.rezolva({"sursa": "firmeapi"}, panou, {})[0]["sursa"], "firmeapi")
        self.assertEqual(setari.rezolva({"sursa": "onrc"}, None, {"GITHUB_ACTIONS": "false"})[0]["sursa"], "onrc")
        self.assertEqual(setari.surse_permise({"GITHUB_ACTIONS": "true"}), ("auto", "anaf"))

    def test_override(self):
        o, sarite = setari.curata_override({
            "9622": {"activ": False},
            "5611": {"tier": "c"},
            "6210": {"tier": "A", "denumire": "  Programare\n", "serviciu": "Site"},
            "12": {"tier": "A"}, "4100": {"tier": "Z"}, "4321": {"activ": "nu"}, "4322": "x"})
        self.assertEqual(o, {"9622": {"activ": False}, "5611": {"tier": "C"},
                             "6210": {"tier": "A", "denumire": "Programare", "serviciu": "Site"}})
        self.assertEqual(sarite, 4)

    def test_lista_alba_cu_override(self):
        la = ListaAlba()
        n = len(la)
        la.aplica_override({"9622": {"activ": False}, "5611": {"tier": "C"},
                            "6210": {"tier": "A", "denumire": "Programare"},
                            "6201": {"tier": "A"}})                      # cod nou fara denumire: sarit
        self.assertIsNone(la.accepta("9622", "A,B,C"))
        self.assertIsNone(la.accepta("5611", "A"))
        self.assertEqual(la.accepta("5611", "C").tier, "C")
        self.assertEqual(la.accepta("6210", "A").denumire, "Programare")
        self.assertIsNone(la.get("6201"))
        self.assertEqual(len(la), n + 1)
        self.assertNotIn("9622", la.coduri("A"))
        self.assertIn("6210", la.coduri("A"))


# ------------------------------------------------------------------ colectarea cap-coada
class BazaColectare(unittest.TestCase):
    """SQLite temporar + ANAF/ONRC simulate. Google si firmeapi oprite."""

    F = next(cui.enumera_de_la(55_600_000))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = [
            mock.patch.object(db, "CALE_IMPLICITA", Path(self.tmp.name) / "t.db"),
            mock.patch.object(places, "cheie", lambda: None),
            mock.patch.object(contacte, "activ", lambda: False),
            mock.patch.dict(os.environ),             # se reface la sfarsit
        ]
        if PG_TEST:
            self.patch.append(mock.patch.object(db, "dsn", lambda: PG_TEST))
            self._curata_pg()
        else:
            self.patch.append(mock.patch.object(db, "dsn", lambda: None))
        for p in self.patch:
            p.start()
        os.environ.pop("GITHUB_ACTIONS", None)      # ca local; testele din Actions il pun singure
        db.initializeaza()
        self.onrc_cereri = 0

    def tearDown(self):
        for p in reversed(self.patch):
            p.stop()
        self.tmp.cleanup()

    def _curata_pg(self):
        import psycopg
        vechi = db._PG.get("con")
        if vechi is not None and not vechi.closed:
            vechi.close()
        with psycopg.connect(PG_TEST, autocommit=True) as c:
            for t in ("prospecti", "blacklist", "stare", "jurnal"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
        db._PG["con"] = None

    # unelte
    def stare(self, cheie, valoare):
        with db.conexiune() as con:
            db.set_stare(con, cheie, valoare if isinstance(valoare, (str, int)) else json.dumps(valoare))

    def citeste(self, sql, p=()):
        with db.conexiune() as con:
            return [dict(r) for r in con.execute(sql, p).fetchall()]

    def jurnal(self):
        return self.citeste("SELECT * FROM jurnal ORDER BY id DESC")[0]

    def ultim(self):
        return int(self.citeste("SELECT valoare FROM stare WHERE cheie='ultim_cui'")[0]["valoare"])

    def onrc_blocat(self, cod=403):
        def f(*a, **k):
            self.onrc_cereri += 1
            raise eroare_http(cod)
        return mock.patch.object(onrc, "descarca", f)

    def ruleaza(self, fals, onrc_patch=None, **kw):
        kw.setdefault("tiers", "A")
        out, err = io.StringIO(), io.StringIO()
        with (onrc_patch or self.onrc_blocat()), \
                mock.patch.object(anaf, "interogheaza_lot", fals), \
                redirect_stdout(out), redirect_stderr(err):
            rez = pipeline.colectare(**kw)
        self.stdout = out.getvalue() + err.getvalue()
        return rez

    def fara_date_personale(self, text):
        self.assertIsNone(re.search(r"\d{6,}", text), text)
        self.assertNotIn("FIRMA TEST", text)


class TestColectare(BazaColectare):
    def test_onrc_403_cade_pe_anaf_si_scrie_avertisment(self):
        reg = registru(self.F)
        self.stare("ultim_cui", self.F)
        fals = AnafFals(reg)
        rez = self.ruleaza(fals)
        j = self.jurnal()
        self.assertEqual(self.onrc_cereri, 1)
        self.assertTrue(j["eroare"].startswith("Atenție: lista ONRC blochează GitHub (403) — am folosit ANAF direct"),
                        j["eroare"])
        self.assertEqual(rez["eroare"], j["eroare"])
        noi = [c for c in reg if c > self.F]
        self.assertEqual(j["gasite"], len(noi))
        self.assertEqual(self.ultim(), max(reg))
        # primul lot incepe cu frontiera (proba), apoi sus pana la 3 loturi goale
        self.assertEqual(fals.cereri[0][0], self.F)
        self.assertEqual(j["cui_min"], fals.cereri[0][0])
        self.assertEqual(j["cui_max"], fals.cereri[-1][-1])
        # doar tier A (9622, 5611), cu mobil; 4100 (B) si 6210 (in afara listei) raman pe dinafara
        p = self.citeste("SELECT cui, caen, tier, telefon, mesaj_draft, data_colectare FROM prospecti")
        self.assertEqual({x["caen"] for x in p}, {"9622", "5611"})
        self.assertEqual(len(p), j["adaugate"])
        self.assertEqual(j["adaugate"], len([c for c in noi if reg[c].caen in ("9622", "5611")]))
        self.assertEqual(j["in_lista_alba"], j["adaugate"])
        self.assertTrue(all(x["telefon"].startswith("+407") and "{{semnatura}}" in x["mesaj_draft"] for x in p))
        self.assertRegex(p[0]["data_colectare"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertRegex(j["pornit_la"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.fara_date_personale(self.stdout)

    def test_a_doua_rulare_nu_mai_gaseste_nimic(self):
        reg = registru(self.F)
        self.stare("ultim_cui", self.F)
        self.ruleaza(AnafFals(reg))
        n = len(self.citeste("SELECT cui FROM prospecti"))
        rez = self.ruleaza(AnafFals(reg), sursa="anaf")
        self.assertEqual((rez["adaugate"], rez["gasite"]), (0, 0))
        self.assertEqual(rez["eroare"], "Atenție: nicio firmă nouă la ANAF de la rularea trecută")
        self.assertEqual(len(self.citeste("SELECT cui FROM prospecti")), n)
        self.assertEqual(len(self.citeste("SELECT id FROM jurnal")), 2)

    def test_sursa_anaf_nu_atinge_onrc(self):
        self.stare("ultim_cui", self.F)
        rez = self.ruleaza(AnafFals(registru(self.F)), sursa="anaf")
        self.assertEqual(self.onrc_cereri, 0)
        self.assertIsNone(rez["eroare"])
        self.assertIsNone(self.jurnal()["eroare"])
        self.assertIn("doar ANAF", self.stdout)

    def test_onrc_merge_si_da_capatul(self):
        g = cui.enumera_de_la(self.F + 1)
        sir = [next(g) for _ in range(900)]
        reg = registru(self.F, sub=10, densitate=1, gauri=[(sir[100], sir[599])])
        self.stare("ultim_cui", self.F)
        top = max(reg)
        pagina = [onrc.Intrare("X", c, "Cluj", "") for c in (top - 2000, top - 100, top)]
        p = mock.patch.object(onrc, "descarca", lambda *a, **k: pagina)
        rez = self.ruleaza(AnafFals(reg), onrc_patch=p)
        self.assertIsNone(rez["eroare"])
        self.assertEqual(self.ultim(), top)                  # gaura de 5 loturi trecuta datorita ONRC

    def test_onrc_fara_firme_pe_pagina(self):
        self.stare("ultim_cui", self.F)
        p = mock.patch.object(onrc, "descarca", lambda *a, **k: [])
        rez = self.ruleaza(AnafFals(registru(self.F)), onrc_patch=p)
        self.assertIn("și-a schimbat pagina", rez["eroare"])
        self.assertTrue(rez["eroare"].startswith("Atenție:"))

    def test_onrc_timeout(self):
        self.stare("ultim_cui", self.F)

        def lent(*a, **k):
            raise requests.exceptions.ConnectTimeout("t")
        rez = self.ruleaza(AnafFals(registru(self.F)), onrc_patch=mock.patch.object(onrc, "descarca", lent))
        self.assertTrue(rez["eroare"].startswith("Atenție: lista ONRC nu răspunde (timeout) — am folosit ANAF direct"))

    def test_sursa_onrc_strict_esueaza(self):
        self.stare("ultim_cui", self.F)
        fals = AnafFals(registru(self.F))
        rez = self.ruleaza(fals, sursa="onrc")
        self.assertEqual(rez["eroare"], "Eroare: lista ONRC blochează GitHub (403)")
        self.assertEqual(fals.cereri, [])
        self.assertEqual(self.jurnal()["eroare"], rez["eroare"])

    def test_anaf_cazut_de_la_inceput(self):
        self.stare("ultim_cui", self.F)
        rez = self.ruleaza(AnafFals(registru(self.F), pica_la=1), sursa="anaf")
        j = self.jurnal()
        self.assertEqual(j["eroare"], "Eroare: ANAF nu răspunde (HTTP 503) — n-am putut colecta nimic; "
                                      "reîncerc la rularea următoare")
        self.assertEqual(self.ultim(), self.F)               # frontiera ramane pe loc
        self.assertEqual((rez["adaugate"], j["interogate"]), (0, 0))

    def test_anaf_cazut_la_jumatate_salveaza_ce_a_gasit(self):
        reg = registru(self.F)
        self.stare("ultim_cui", self.F)
        fals = AnafFals(reg, pica_la=4)
        rez = self.ruleaza(fals, sursa="anaf")
        self.assertTrue(rez["eroare"].startswith("Eroare: ANAF nu răspunde (HTTP 503) după 3 cereri — "
                                                 "am salvat ce am găsit"), rez["eroare"])
        ultim = self.ultim()
        self.assertTrue(self.F < ultim < max(reg))
        self.assertTrue(ultim <= fals.cereri[2][-1])
        self.assertGreater(rez["adaugate"], 0)
        # rularea urmatoare continua de acolo si ajunge la capat
        rez2 = self.ruleaza(AnafFals(reg), sursa="anaf")
        self.assertIsNone(rez2["eroare"])
        self.assertEqual(self.ultim(), max(reg))

    def test_anaf_refuza(self):
        self.stare("ultim_cui", self.F)
        fals = AnafFals(registru(self.F), pica_la=1, exceptie=anaf.AnafRefuza("refuză cererea (HTTP 400)", 400))
        rez = self.ruleaza(fals, sursa="anaf")
        self.assertTrue(rez["eroare"].startswith("Eroare: ANAF refuză cererea (HTTP 400) — poate s-a schimbat"))

    def test_plafon(self):
        reg = registru(self.F, peste=900)
        self.stare("ultim_cui", self.F)
        with mock.patch.object(sondare, "PLAFON_LOTURI", 3):
            rez = self.ruleaza(AnafFals(reg), sursa="anaf")
        self.assertIn("am ajuns la plafonul de 3 de cereri ANAF", rez["eroare"])
        self.assertTrue(rez["eroare"].startswith("Atenție:"))
        self.assertTrue(self.F < self.ultim() < max(reg))

    def test_frontiera_necunoscuta_la_anaf(self):
        reg = registru(self.F)
        del reg[self.F]
        self.stare("ultim_cui", self.F)
        rez = self.ruleaza(AnafFals(reg), sursa="anaf")
        self.assertIn("ANAF nu mai găsește ultima firmă știută", rez["eroare"])

    def test_nimic_de_la_frontiera_in_jos_nu_revine(self):
        # ex. dupa „Șterge tot ce n-a fost trimis": firmele sterse nu trebuie sa reapara
        reg = registru(self.F, caen=("9622",))
        self.stare("ultim_cui", self.F)
        fals = AnafFals(reg)
        rez = self.ruleaza(fals, sursa="anaf")
        p = {x["cui"] for x in self.citeste("SELECT cui FROM prospecti")}
        self.assertTrue(p and min(p) > self.F)
        self.assertTrue(all(c >= self.F for lot in fals.cereri for c in lot))
        self.assertEqual(rez["adaugate"], len([c for c in reg if c > self.F]))

    def test_fara_frontiera_porneste_de_la_ultimul_prospect(self):
        reg = registru(self.F)
        with db.conexiune() as con:
            db.upsert_prospect(con, {"cui": self.F, "denumire": "VECHE", "status": "nou",
                                     "data_colectare": "2026-09-29T05:00:00"})
        rez = self.ruleaza(AnafFals(reg), sursa="anaf")
        self.assertIsNone(rez["eroare"])
        self.assertEqual(self.ultim(), max(reg))

    def test_fara_frontiera_fara_onrc(self):
        fals = AnafFals(registru(self.F))
        rez = self.ruleaza(fals)
        self.assertTrue(rez["eroare"].startswith("Eroare: nu știu de unde să încep"), rez["eroare"])
        self.assertEqual(fals.cereri, [])

    def test_prima_rulare_cu_onrc(self):
        reg = registru(self.F, sub=0)
        pagina = [onrc.Intrare("X", c, "Cluj", "") for c in sorted(reg)[-100:]]
        rez = self.ruleaza(AnafFals(reg), onrc_patch=mock.patch.object(onrc, "descarca", lambda *a, **k: pagina))
        self.assertIsNone(rez["eroare"])
        self.assertEqual(self.ultim(), max(reg))

    def test_eroare_neprevazuta_ajunge_in_jurnal(self):
        self.stare("ultim_cui", self.F)

        def strica(*a, **k):
            raise KeyError("scenariu")
        with mock.patch.object(mesaj, "compune", strica):
            with self.assertRaises(KeyError):
                self.ruleaza(AnafFals(registru(self.F)), sursa="anaf")
        j = self.jurnal()
        self.assertTrue(j["eroare"].startswith("Eroare: eroare neprevăzută (KeyError)"), j["eroare"])
        self.assertEqual(self.ultim(), self.F)               # tranzactia s-a anulat: frontiera pe loc
        self.assertEqual(self.citeste("SELECT cui FROM prospecti"), [])

    def test_firmeapi_local_nimic_de_la_frontiera_in_jos(self):
        reg = registru(self.F)
        a = sorted(c for c, f in reg.items() if f.caen == "9622")
        sub = [c for c in a if c <= self.F][-3:]              # ex. firme sterse de Felix din panou
        peste = [c for c in a if c > self.F][:3]
        self.stare("ultim_cui", self.F)
        gasite = {c: reg[c] for c in sub + peste}
        with mock.patch.object(pipeline, "_descopera_firmeapi", lambda *a, **k: dict(gasite)):
            rez = self.ruleaza(AnafFals({}), sursa="firmeapi")
        self.assertEqual({x["cui"] for x in self.citeste("SELECT cui FROM prospecti")}, set(peste))
        self.assertEqual(rez["gasite"], 3)
        self.assertEqual(self.ultim(), self.F)               # frontiera nu se muta din firmeapi
        self.assertIn("3 firme sub ultimul CUI stiut, sarite", self.stdout)
        self.fara_date_personale(self.stdout)

    def test_firmeapi_fara_frontiera_foloseste_ultimul_prospect(self):
        reg = registru(self.F)
        a = sorted(c for c, f in reg.items() if f.caen == "9622")
        with db.conexiune() as con:
            db.upsert_prospect(con, {"cui": self.F, "denumire": "FIRMA TEST X SRL", "status": "nou", "data_colectare": AZI})
        gasite = {c: reg[c] for c in a[-2:] + [c for c in a if c < self.F][-2:]}
        with mock.patch.object(pipeline, "_descopera_firmeapi", lambda *a, **k: dict(gasite)):
            self.ruleaza(AnafFals({}), sursa="firmeapi")
        p = {x["cui"] for x in self.citeste("SELECT cui FROM prospecti")}
        self.assertEqual(p, {self.F, *a[-2:]})

    def test_firmeapi_din_actions_se_ignora(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["A"], "sursa": "anaf"})
        apeluri = []
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                mock.patch.object(pipeline, "_descopera_firmeapi", lambda *a, **k: apeluri.append(1) or {}):
            rez = self.ruleaza(AnafFals(registru(self.F)), din_setari=True, tiers=None, sursa="firmeapi")
        self.assertEqual(apeluri, [])
        self.assertEqual(self.onrc_cereri, 0)                # sursa din panou: anaf
        self.assertEqual(rez["eroare"], "Atenție: am ignorat valori greșite date la pornire (sursa)")
        self.assertIn("sursa anaf", self.stdout)

    def test_firme_vechi_si_radiate_sar(self):
        reg = registru(self.F)
        noi = sorted(c for c in reg if c > self.F and reg[c].caen == "9622")
        reg[noi[0]].data_inregistrare = "2020-01-01"
        reg[noi[1]].stare = "RADIERE din data 01.10.2026"
        reg[noi[2]].denumire = "ASOCIATIA TEST"
        self.stare("ultim_cui", self.F)
        self.ruleaza(AnafFals(reg), sursa="anaf")
        p = {x["cui"] for x in self.citeste("SELECT cui FROM prospecti")}
        self.assertFalse(p & set(noi[:3]))
        self.assertIn(noi[3], p)


class TestSetariInColectare(BazaColectare):
    def test_din_setari_din_panou(self):
        reg = registru(self.F)
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["B"], "max_varsta": 3, "sursa": "anaf"})
        rez = self.ruleaza(AnafFals(reg), din_setari=True, tiers=None)
        self.assertEqual(self.onrc_cereri, 0)                # sursa anaf din panou
        self.assertIsNone(rez["eroare"])
        self.assertEqual({x["caen"] for x in self.citeste("SELECT caen FROM prospecti")}, {"4100"})
        self.assertIn("tier B, vechime maxima 3 zile, sursa anaf", self.stdout)

    def test_inputs_castiga_peste_panou(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["B"], "max_varsta": 3, "sursa": "anaf"})
        self.ruleaza(AnafFals(registru(self.F)), din_setari=True, tiers="A", sursa="auto")
        self.assertEqual(self.onrc_cereri, 1)
        self.assertEqual({x["caen"] for x in self.citeste("SELECT caen FROM prospecti")}, {"9622", "5611"})

    def test_fara_din_setari_panoul_nu_conteaza(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["B"], "sursa": "anaf"})
        self.ruleaza(AnafFals(registru(self.F)), tiers="A")
        self.assertEqual(self.onrc_cereri, 1)

    def test_setari_stricate(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", "{nu e json")
        rez = self.ruleaza(AnafFals(registru(self.F)), din_setari=True, tiers=None, sursa="anaf")
        self.assertIn("setările de colectare din panou au valori greșite", rez["eroare"])
        self.assertEqual({x["tier"] for x in self.citeste("SELECT tier FROM prospecti")}, {"A"})

    def test_override_caen(self):
        reg = registru(self.F)
        self.stare("ultim_cui", self.F)
        self.stare("caen_override", {"9622": {"activ": False},
                                     "6210": {"tier": "A", "denumire": "Programare", "serviciu": "Site"},
                                     "abc": {"tier": "A"}})
        rez = self.ruleaza(AnafFals(reg), sursa="anaf")
        p = self.citeste("SELECT caen, caen_denumire, serviciu_moon, tier FROM prospecti")
        self.assertEqual({x["caen"] for x in p}, {"5611", "6210"})
        r6210 = next(x for x in p if x["caen"] == "6210")
        self.assertEqual((r6210["caen_denumire"], r6210["serviciu_moon"], r6210["tier"]),
                         ("Programare", "Site", "A"))
        self.assertIn("unele coduri CAEN din panou au valori greșite", rez["eroare"])

    def test_override_schimba_tier(self):
        self.stare("ultim_cui", self.F)
        self.stare("caen_override", {"4100": {"tier": "A"}, "5611": {"tier": "C"}})
        rez = self.ruleaza(AnafFals(registru(self.F)), sursa="anaf")
        self.assertIsNone(rez["eroare"])
        self.assertEqual({x["caen"] for x in self.citeste("SELECT caen FROM prospecti")}, {"9622", "4100"})


class TestLinieDeComanda(BazaColectare):
    def main(self, fals, *args, onrc_patch=None):
        out = io.StringIO()
        with (onrc_patch or self.onrc_blocat()), mock.patch.object(anaf, "interogheaza_lot", fals), \
                redirect_stdout(out), redirect_stderr(out):
            cod = pipeline.main(["colectare", *args])
        self.stdout = out.getvalue()
        return cod

    def test_avertisment_iese_cu_0(self):
        self.stare("ultim_cui", self.F)
        self.assertEqual(self.main(AnafFals(registru(self.F)), "--din-setari"), 0)
        self.assertIn("Atenție: lista ONRC blochează GitHub (403)", self.stdout)
        self.fara_date_personale(self.stdout)

    def test_eroare_iese_cu_1(self):
        self.stare("ultim_cui", self.F)
        self.assertEqual(self.main(AnafFals(registru(self.F), pica_la=1), "--sursa", "anaf"), 1)
        self.assertIn("Eroare: ANAF nu răspunde", self.stdout)

    def test_exceptie_iese_cu_1_fara_date_in_log(self):
        self.stare("ultim_cui", self.F)

        def strica(*a, **k):
            raise ValueError(f"cui {self.F} telefon 0712345678 a@b.ro postgres.abcdefgh")
        with mock.patch.object(mesaj, "compune", strica):
            self.assertEqual(self.main(AnafFals(registru(self.F)), "--sursa", "anaf"), 1)
        self.assertIn("Colectarea a esuat: ValueError", self.stdout)
        self.fara_date_personale(self.stdout)
        self.assertNotIn("a@b.ro", self.stdout)
        self.assertNotIn("abcdefgh", self.stdout)
        self.assertTrue(self.jurnal()["eroare"].startswith("Eroare: eroare neprevăzută (ValueError)"))

    def test_eroare_de_baza_doar_tip_si_cod(self):
        # psycopg pune in mesaj randul care n-a intrat: DETAIL: Failing row contains (...)
        class NotNullViolation(Exception):
            sqlstate = "23502"
        NotNullViolation.__module__ = "psycopg.errors"
        self.stare("ultim_cui", self.F)

        def strica(*a, **k):
            raise NotNullViolation('null value in column "telefon" of relation "prospecti" violates not-null constraint\n'
                                   f'DETAIL:  Failing row contains ({self.F}, FIRMA EXEMPLU SRL, J12/1/2026, Str. Lungă nr. 5, Cluj).')
        with mock.patch.object(db, "upsert_prospect", strica):
            self.assertEqual(self.main(AnafFals(registru(self.F)), "--sursa", "anaf"), 1)
        self.assertIn("Colectarea a esuat: baza de date: NotNullViolation, cod 23502", self.stdout)
        for x in ("Failing row", "FIRMA EXEMPLU", "Lungă", "Cluj", "telefon", "not-null"):
            self.assertNotIn(x, self.stdout)
        self.fara_date_personale(self.stdout)
        self.assertEqual(self.jurnal()["eroare"], "Eroare: baza de date a dat eroare (NotNullViolation, cod 23502)")

    def test_eroare_sqlite_doar_tip_si_cod(self):
        import sqlite3
        self.stare("ultim_cui", self.F)

        def strica(*a, **k):
            c = sqlite3.connect(":memory:")
            c.execute("CREATE TABLE t (x NOT NULL)")
            c.execute("INSERT INTO t VALUES (NULL)")
        with mock.patch.object(db, "upsert_prospect", strica):
            self.assertEqual(self.main(AnafFals(registru(self.F)), "--sursa", "anaf"), 1)
        self.assertIn("Colectarea a esuat: baza de date: IntegrityError", self.stdout)
        self.assertNotIn("NOT NULL constraint failed", self.stdout)
        self.assertTrue(self.jurnal()["eroare"].startswith("Eroare: baza de date a dat eroare (IntegrityError"), self.jurnal()["eroare"])
        self.assertNotIn("constraint failed", self.jurnal()["eroare"])

    def test_alta_exceptie_masca_stricta(self):
        self.stare("ultim_cui", self.F)

        def strica(*a, **k):
            raise ValueError("nu pot citi 'FIRMA EXEMPLU SRL' din JUD. CLUJ, STR. LUNGA NR. 5 (tel 0722 111 222)\nal doilea rand Popescu")
        with mock.patch.object(mesaj, "compune", strica):
            self.assertEqual(self.main(AnafFals(registru(self.F)), "--sursa", "anaf"), 1)
        self.assertIn("Colectarea a esuat: ValueError: nu pot citi", self.stdout)
        for x in ("FIRMA EXEMPLU", "CLUJ", "LUNGA", "111", "Popescu", "al doilea rand"):
            self.assertNotIn(x, self.stdout)

    def test_inputs_goale_inseamna_panou(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["B"], "sursa": "anaf"})
        self.assertEqual(self.main(AnafFals(registru(self.F)), "--din-setari", "--tiers", ""), 0)
        self.assertEqual({x["caen"] for x in self.citeste("SELECT caen FROM prospecti")}, {"4100"})

    def test_input_gresit_nu_opreste_rularea(self):
        self.stare("ultim_cui", self.F)
        self.stare("colectare_setari", {"tiers": ["A"], "sursa": "anaf"})
        cod = self.main(AnafFals(registru(self.F)), "--din-setari", "--max-varsta", "7 zile", "--sursa", "xyz")
        self.assertEqual(cod, 0)
        self.assertEqual(self.jurnal()["eroare"],
                         "Atenție: am ignorat valori greșite date la pornire (max_varsta, sursa)")
        self.assertEqual(self.onrc_cereri, 0)

    def test_pauza_nu_coboara_sub_1s(self):
        self.stare("ultim_cui", self.F)
        vazut = []

        def fals(lot, **kw):
            vazut.append(kw.get("pauza"))
            return {}
        self.main(fals, "--sursa", "anaf", "--pauza", "0.1")
        self.assertTrue(vazut and min(vazut) >= 1.0)


# ------------------------------------------------------------------ logurile publice
class TestLogPublic(unittest.TestCase):
    def test_masca_stricta(self):
        m = pipeline._ascunde_strict(
            'null value in column "denumire" violates not-null constraint\nDETAIL:  Failing row contains (55600123, X SRL)')
        self.assertEqual(m, "null value in column … violates not-null constraint")
        self.assertEqual(pipeline._ascunde_strict("invalid literal for int() with base 10: 'SC ALFA SRL'"),
                         "invalid literal for int(…) with base 10: …")
        self.assertEqual(pipeline._ascunde_strict("firma ALFA BETA SRL, tel 0722 111 222, a@b.ro"), "firma …, tel … … …, …@…")
        self.assertNotIn("abc", pipeline._ascunde_strict("Forbidden for url: https://x.ro/api?cui=1&nume=abc"))
        self.assertEqual(pipeline._ascunde_strict(""), "")
        self.assertLessEqual(len(pipeline._ascunde_strict("x" * 1000)), 200)

    def test_cod_baza_doar_valori_sigure(self):
        class E(Exception):
            sqlstate = "23502"
        E.__module__ = "psycopg.errors"
        self.assertEqual(pipeline._mesaj_neprevazut(E("DETAIL: Failing row contains (X)")),
                         "baza de date a dat eroare (E, cod 23502)")

        class F(Exception):
            sqlstate = "nu e cod (FIRMA SRL)"
        F.__module__ = "psycopg.errors"
        self.assertEqual(pipeline._eroare_de_baza(F("x")), "F")

    def test_psycopg_real(self):
        try:
            from psycopg import errors
        except ImportError:
            self.skipTest("fara psycopg")
        e = errors.NotNullViolation("null value ...\nDETAIL:  Failing row contains (1, FIRMA SRL)")
        self.assertEqual(pipeline._descrie_exceptia(e), "baza de date: NotNullViolation, cod 23502")

    def test_firmeapi_eroare_fara_textul_ei(self):
        r = requests.Response()
        r.status_code = 401
        e = requests.exceptions.HTTPError("401 Client Error for url: https://www.firmeapi.ro/api/v1/firme?caen=9622&cheie=SECRET", response=r)
        linii = []
        with mock.patch.object(firmeapi, "interogheaza_brut", mock.Mock(side_effect=e)):
            firmeapi.firme_noi(["9622"], date.today(), verbose=linii.append, pauza=0)
        self.assertIn("     CAEN 9622: HTTPError (HTTP 401)", linii)
        self.assertFalse(any("SECRET" in x or "firmeapi.ro" in x for x in linii), linii)


# ------------------------------------------------------------------ workflow-ul
class TestWorkflow(unittest.TestCase):
    def setUp(self):
        self.text = (RADACINA / ".github" / "workflows" / "colectare.yml").read_text(encoding="utf-8")

    def test_setari_si_inputs(self):
        self.assertIn("--din-setari", self.text)
        for x in ("tiers:", "max_varsta:", "sursa:"):
            self.assertIn(x, self.text)
        self.assertIn("actions/checkout@v5", self.text)
        self.assertIn("actions/setup-python@v6", self.text)

    def test_inputs_nu_ajung_direct_in_shell(self):
        # ${{ inputs.X }} lipit direct in `run:` = injectie de shell. Doar prin env.
        run = self.text.split("run: |", 1)[-1]
        self.assertNotIn("${{", run)

    def test_yaml_valid(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("fara PyYAML")
        w = yaml.safe_load(self.text)
        on = w.get("on", w.get(True))
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"tiers", "max_varsta", "sursa"})
        for i in on["workflow_dispatch"]["inputs"].values():
            self.assertEqual(i.get("default", ""), "")      # gol = setarile din panou


if __name__ == "__main__":
    unittest.main()
