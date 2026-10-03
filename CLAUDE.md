# MOON Prospector

Flux de prospectare pentru THE MOON Agency: colectează firmele nou înființate din România,
le filtrează pe CAEN, verifică dacă au prezență online, generează mesaje WhatsApp și le
urmărește într-un dashboard Streamlit.

`README.md` = manualul de utilizare. `DEPLOY.md` = mutarea pe Supabase + Streamlit Cloud.
Acest fișier acoperă doar lucrul la cod.

## Rulare

Singurul proiect al lui Felix care e **repo git** (`origin`: `github.com/themoonagency/moon-prospector`)
și are **venv propriu** (`.venv/`). Toate scripturile `.sh` fac singure `source .venv/bin/activate`
— folosește-le, nu rula `python` direct.

```bash
./instaleaza.sh      # creează .venv + instalează requirements
./dashboard.sh       # streamlit run app.py
./colecteaza.sh A    # python -m moon.pipeline colectare --tiers A
./verifica.sh        # re-verifică prospecții existenți
./diag.sh            # descarcă HTML brut de la ONRC în test/ (când scraperul se rupe)
./cheie.sh           # scrie GOOGLE_PLACES_API_KEY în .env
./secrete.sh         # afișează secretele pentru Streamlit Cloud
./supabase.sh        # migrare SQLite -> Postgres
.venv/bin/python -m unittest discover -s teste   # testele (fara retea, SQLite temporar)
```

Testele stau în `teste/` (urmărit de git, doar date inventate). `test/` e ignorat de git: acolo
sunt doar fișiere brute reale (HTML ONRC, răspunsuri ANAF), nu le comite. Cu
`TEST_DATABASE_URL=postgresql://...` (o bază goală de test) testele rulează și pe Postgres.

CLI-ul complet: `python -m moon.pipeline {colectare|verifica|sumar|statistici|test-google|test-firmeapi}`.

## Arhitectură

Fluxul (docstring-ul din `moon/pipeline.py`):
**frontiera CUI (`stare.ultim_cui`) → ANAF lot cu lot → filtru CAEN → baza de date**

Din 30 sept 2026 lista ONRC (new.firme-on-line.ro) dă 403 pe IP-urile GitHub. Ea dădea oricum
doar capătul zilei — firmele vin TOATE de la ANAF. Acum (`moon/sondare.py`): de la frontieră în
sus, câte 100 de CUI-uri valide pe cerere (= 1.000 de numere), până la **3 loturi goale la rând**
(`K_GOALE`) sau **80 de cereri** (`PLAFON_LOTURI`). Primul lot începe chiar cu frontiera (probă
că ANAF răspunde). Nimic de la frontieră în jos nu se mai adaugă: ce a șters Felix din panou nu
revine. Frontiera nouă = cel mai mare CUI găsit la ANAF, nu capătul de pe pagina ONRC. Alegerea lui K și a plafonului e explicată în `moon/sondare.py`.
`--sursa auto` (implicit) încearcă întâi lista ONRC (sub capătul ei nu ne oprim pe loturi goale),
iar dacă nu merge cade pe ANAF direct; `anaf` = doar ANAF; `onrc` = fără rezervă; `firmeapi` = API plătit.
`onrc` și `firmeapi` merg doar local; în GitHub Actions (și din panou) doar `auto`/`anaf`. `firmeapi` nu adaugă nimic de la frontieră în jos.

| Modul | Rol |
|---|---|
| `moon/onrc.py` | lista publică a firmelor noi (scraping HTML) — doar indiciu pentru capătul zilei |
| `moon/cui.py` | validare + enumerare CUI-uri românești |
| `moon/anaf.py` | client ANAF: un lot = o cerere, reîncercări la 5xx/429/timeout, `AnafIndisponibil` / `AnafRefuza` |
| `moon/sondare.py` | găsește capătul șirului de CUI-uri întrebând ANAF lot cu lot |
| `moon/setari.py` | citește și curăță setările din panou (`colectare_setari`, `caen_override`) |
| `moon/caen.py` | lista albă CAEN Rev. 3 — decide pe cine contactăm și ce îi propunem |
| `moon/firmeapi.py` | sursă alternativă de firme (API firmeapi.ro), în loc de scraping |
| `moon/places.py` | verificare Google Places: are fișă GBP? are site? câte recenzii? |
| `moon/contacte.py` | îmbogățire opțională: administrator + email (firmeapi plan plătit) |
| `moon/mesaj.py` | generator de mesaje WhatsApp |
| `moon/db.py` | SQLite implicit, Postgres/Supabase dacă există `DATABASE_URL` |
| `moon/pipeline.py` | orchestrarea + CLI |
| `app.py` | dashboard Streamlit |
| `moon_auth.py` | poartă cu parolă + cookie semnat HMAC („rămâi logat" 30 zile) |

Tabele: `prospecti`, `blacklist`, `stare`, `jurnal`. Lista albă CAEN e în
`caen_whitelist_moon.csv`, nu în cod; peste ea se pun schimbările din panou.

## Setările din panou (prospect.themoonagency.ro → Setări)

Le scrie workerul (`../prospect-worker/src/colectare.js`) în `stare`, ca JSON:
- `colectare_setari` = `{"tiers": ["A"], "max_varsta": 7, "sursa": "auto"|"anaf"}`. Rularea
  programată le folosește (`--din-setari` în `colectare.yml`). La `workflow_dispatch`, inputurile
  completate câștigă; cele goale = din panou. Ordinea: linia de comandă > panou > `MOON_TIERS` /
  `MOON_MAX_AGE_DAYS` > implicit.
- `caen_override` = `{"<cod>": {"tier", "activ", "denumire", "serviciu"}}` peste CSV — alt tier,
  cod oprit, cod nou (doar cu tier + denumire). Se aplică MEREU, și la rulările locale.
- Valorile greșite se sar (cu avertisment în jurnal), nu opresc colectarea.
- Workflow-ul instalează doar `requirements-colectare.txt` (fără Streamlit). Un import nou în
  `moon/` → adaugă-l și acolo.
- Dacă schimbi CSV-ul: `node genereaza-caen.mjs` în `../prospect-worker` (workerul are o copie,
  `src/caen-lista.js`; `test-colectare.mjs` pică dacă a rămas în urmă).

## Jurnalul și logurile

- **Fiecare rulare scrie un rând în `jurnal`**, și când pică (try/finally în `colectare()`).
  `jurnal.eroare`: NULL = totul bine; începe cu `Atenție: ` = a mers, dar e ceva de știut (ex.
  „lista ONRC blochează GitHub (403) — am folosit ANAF direct", plafon atins, nicio firmă nouă);
  începe cu `Eroare: ` = a eșuat sau a rămas la jumătate (ex. „ANAF nu răspunde (HTTP 503)").
  Dashboardul și alertele Telegram citesc de aici — păstrează prefixele.
- Erorile așteptate (ANAF căzut, ONRC blocat) nu aruncă: `colectare()` le pune în `rez["eroare"]`,
  iar CLI-ul iese cu 1 la `Eroare:` (Actions arată rularea roșie) și cu 0 la `Atenție:`.
- **Logurile GitHub Actions sunt PUBLICE.** La stdout doar cifre: fără nume, telefoane, CUI-uri.
  Excepțiile: la baza de date doar tipul și codul (`sqlstate`), fără textul erorii (are rândul cu date);
  restul prin `_ascunde_strict()` (primul rând, fără ghilimele/paranteze/cifre/majuscule), plus fișier:rând.

## Reguli care nu se încalcă

- **ANAF: 1 request/secundă.** `MOON_ANAF_SLEEP = 1.2` e marja de politețe. Nu o coborî
  (codul nu coboară oricum sub 1 s, orice ar zice `--pauza`).
- **`ORE_OK = 9..17` și `ZILE_OK = luni-vineri`** (`app.py`, `moment_bun()`) — dashboard-ul
  avertizează când nu e moment bun de sunat. E o regulă de business, nu o limitare tehnică.
- **Sistemul merge complet fără chei.** Google Places și firmeapi sunt opționale; fără ele
  mesajul **nu mai pretinde** că a căutat pe Google. Orice modificare trebuie să păstreze
  această degradare onestă — vezi comentariile din `.env.example`.
- **Secretele:** `.env` local, `st.secrets` pe Streamlit Cloud. `moon_auth.py` citește parola
  din ambele. `.gitignore` acoperă `.env`, `.streamlit/secrets.toml`, `*.db` — verificat, `.env`
  nu e urmărit de git. Nu comite niciodată nimic din ele.

## Convenții

- **Codul e în română fără diacritice** (`colectare`, `verifica`, `denumire`, `varsta`),
  interfața cu diacritice. Păstrează stilul — nu „traduce" identificatorii în engleză.
- Streamlit: fiecare acțiune face `st.rerun()`; starea trăiește în DB, nu în `st.session_state`.
- Mesajele de commit sunt în română.
- `_to_delete/` (în directorul părinte `~/MOON Prospector/`) e gunoi git rămas dintr-o
  operație eșuată — se poate șterge, nu e parte din proiect.

## Dashboardul e pe Cloudflare, nu pe Streamlit (din 10 sept 2026)

- Interfața nouă: `../prospect-worker` → **prospect.themoonagency.ro** (worker Cloudflare, stilul
  panoului MOON Chat). `./deploy.sh` din acel folder, rulat de Felix din Terminal.
- Worker-ul NU rulează Python. Citește din Supabase prin Data API cu cheia `sb_secret_...`
  (antetul `apikey` DOAR — cheile noi nu sunt JWT-uri, `Authorization: Bearer` le strică).
- **De aceea mesajele se pre-generează aici, la colectare**: `mesaj_draft`, `mesaj_fu3`,
  `mesaj_fu7`, cu `{{semnatura}}` ca substituent. Dacă schimbi `mesaj.py`, prospecții vechi
  rămân cu textul vechi până rulezi `python -m moon.pipeline regenereaza`.
- Butonul „Colectează acum" din dashboard face `workflow_dispatch` pe `colectare.yml`. Tokenul
  GitHub din worker (`GITHUB_TOKEN`, fine-grained, doar acest repo, Actions read/write)
  e **fără expirare**, ales de Felix. Dacă butonul dă 401, tokenul a fost revocat, nu expirat.
  Colectarea programată nu depinde de el.
- Streamlit (`app.py`) mai merge în paralel, ca rezervă. Dacă adaugi coloane în `db.py`,
  migrarea rulează doar când pornește Python — worker-ul nu o poate face.
