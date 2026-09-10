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
```

CLI-ul complet: `python -m moon.pipeline {colectare|verifica|sumar|statistici|test-google|test-firmeapi}`.

## Arhitectură

Fluxul (docstring-ul din `moon/pipeline.py`):
**ONRC → enumerare CUI → ANAF → filtru CAEN → baza de date**

| Modul | Rol |
|---|---|
| `moon/onrc.py` | sursa de start: lista publică a firmelor nou înființate (scraping HTML) |
| `moon/cui.py` | validare + enumerare CUI-uri românești |
| `moon/anaf.py` | client pentru serviciul web public ANAF |
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
`caen_whitelist_moon.csv`, nu în cod.

## Reguli care nu se încalcă

- **ANAF: 1 request/secundă.** `MOON_ANAF_SLEEP = 1.2` e marja de politețe. Nu o coborî.
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
  **expiră pe 9 decembrie 2026** — după data aia butonul dă eroare 401 și trebuie făcut altul.
  Colectarea programată nu depinde de el.
- Streamlit (`app.py`) mai merge în paralel, ca rezervă. Dacă adaugi coloane în `db.py`,
  migrarea rulează doar când pornește Python — worker-ul nu o poate face.
