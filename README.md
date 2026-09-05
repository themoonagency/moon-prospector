# MOON Prospector — pasul 1: colectare

Colecteaza automat firmele nou infiintate din Romania, le filtreaza pe lista
alba CAEN si le salveaza cu telefon, gata de contactat pe WhatsApp.

## Ce face

```
new.firme-on-line.ro      ->  ultimele ~100 firme (nume, CUI, CAEN)  [gratis]
        v
enumerare CUI-uri valide  ->  toate firmele din intervalul zilei     [~2.6x mai multe]
        v
API public ANAF           ->  denumire, adresa, CAEN, TELEFON        [gratis, fara cont]
        v
filtru lista alba CAEN    ->  doar nisele care cumpara servicii Moon
        v
deduplicare pe telefon    ->  un om cu 3 firme e contactat o singura data
        v
Google Places             ->  are fisa? are site? cate recenzii?      [optional]
        v
SQLite / Postgres         ->  prospecti cu status, gata de dashboard
```

**Trucul care conteaza:** pagina publica arata doar ultimele ~100 de firme, dar
CUI-urile se aloca secvential. Stiind cifra de control oficiala, generam toate
CUI-urile valide dintre cel mai mic si cel mai mare CUI al zilei si le intrebam
pe ANAF direct. Intr-o zi reala testata: pagina arata 100 de firme, intervalul
continea **260**. Deci vezi de 2,6 ori mai multe firme decat concurenta care
doar citeste pagina.

## Instalare

```bash
pip install -r requirements.txt
cp .env.example .env     # optional, valorile implicite merg ca atare
```

## Rulare

```bash
streamlit run app.py                           # dashboard (aprobare + WhatsApp)
python -m moon.pipeline colectare              # tier A + B
python -m moon.pipeline colectare --tiers A    # doar prioritate maxima
python -m moon.pipeline colectare --max-varsta 3
python -m moon.pipeline sumar
python -m moon.pipeline statistici             # rate de raspuns pe nisa/tier/varianta
python -m moon.pipeline colectare --sursa firmeapi
python -m moon.pipeline test-firmeapi          # verifica cheia si raspunsul brut
```

## Chei optionale

Sistemul merge complet fara ele. Vezi `.env.example`.

| Variabila | Ce activeaza |
|---|---|
| `GOOGLE_PLACES_API_KEY` | Verificarea reala pe Google. Fara ea mesajul nu pretinde ca a cautat. |
| `FIRMEAPI_KEY` | A doua sursa de firme noi (API in loc de scraping, filtrat pe CAEN + telefon) - merge pe **planul gratuit**, 1.000 credite/luna. Administratorul si emailul cer plan platit. |
| `DATABASE_URL` | Postgres/Supabase in loc de SQLite local. |

Prima rulare acopera intervalul zilei. Urmatoarele pornesc de la ultimul CUI
procesat, deci nu reinteroghezi ce ai deja — rularea zilnica dureaza secunde.

Rulare automata: `.github/workflows/colectare.yml`, zilnic la 08:00 ora Romaniei.

## Ce se filtreaza automat

| Regula | Motiv |
|---|---|
| CAEN in afara listei albe | IT, agentii de publicitate, comert cu ridicata, infrastructura |
| Firma radiata sau inactiva | nu are rost |
| Inregistrata acum > N zile | pierzi fereastra de oportunitate |
| Fara numar mobil | nu poate fi contactata pe WhatsApp (`--toate-telefoanele` pastreaza si fixele) |
| Telefon deja folosit de alta firma | acelasi om, mai multe firme - il contactezi o data |
| Telefon in blacklist | a cerut sa nu fie contactat |

## Cifre reale (masurate pe 4 septembrie 2026)

| | |
|---|---|
| Firme noi pe zi lucratoare | 200–400 |
| Prezente in ANAF a doua zi | 100 % |
| Cu telefon in ANAF | 77 % |
| Din care mobil | ~96 % |
| Intra in lista alba CAEN | 55 % |
| **Prospecti calificati si contactabili** | **~42 / zi** (tier A: ~12 / zi) |
| Cost date | **0 €** |

## Baza de date

`prospecti` — cui, denumire, CAEN + denumire nisa, tier, serviciul Moon propus,
adresa, judet, localitate, telefon normalizat (+40...), status, mesaj, date.

Statusuri: `nou` -> `mesaj_generat` -> `trimis` -> `raspuns` -> `client` / `respins` / `blacklist`

`blacklist` — numere care au cerut sa nu fie contactate. Verificat automat la fiecare colectare.
`stare` — ultimul CUI procesat (frontiera).
`jurnal` — istoricul rularilor.

Implicit SQLite (`moon_prospector.db`). Pentru Streamlit Cloud, schema e
compatibila cu Postgres/Supabase — se schimba doar stratul de conexiune din `moon/db.py`.

## Module

| Fisier | Rol |
|---|---|
| `moon/cui.py` | cifra de control CUI + enumerarea intervalului |
| `moon/anaf.py` | client API ANAF (loturi de 100, 1 req/sec) + normalizare telefon |
| `moon/onrc.py` | citeste lista publica de firme noi |
| `moon/caen.py` | lista alba CAEN Rev. 3 (123 coduri, 3 tier-uri) |
| `moon/db.py` | schema + operatii |
| `moon/mesaj.py` | textele mesajelor WhatsApp (4 scenarii) + link wa.me |
| `moon/places.py` | verificarea reala pe Google (fisa, site, recenzii) |
| `moon/contacte.py` | administrator + email (optional, necesita plan firmeapi platit) |
| `moon/firmeapi.py` | sursa alternativa de firme noi prin API (plan gratuit) |
| `moon/pipeline.py` | orchestrare + CLI |
| `app.py` | dashboard Streamlit (aprobare, trimitere, follow-up) |
| `caen_whitelist_moon.csv` | lista alba — editabila direct, fara sa atingi codul |

## Limite cunoscute

- ANAF nu returneaza email sau website. Doar telefon. De aceea canalul principal e WhatsApp.
- Administratorul si emailul nu sunt publicate de nicio sursa gratuita; planul gratuit
  firmeapi.ro acopera doar lista de firme.
- Pagina sursa nu are paginare; daca intr-o zi apar peste ~100 de firme peste
  frontiera cunoscuta, ruleaza colectarea de doua ori pe zi.
- Limita ANAF e 1 request/secunda. O zi intreaga (~260 CUI-uri) inseamna 3 request-uri.

## Manual complet

`MOON-Prospector-Manual.pdf` — instalare, rutina zilnica, mesaje, lista CAEN,
automatizare, ce faci cand raspunde cineva, depanare.

## Urmatorii pasi

1. Al doilea flux: firme existente cu site prost, auditate cu motorul de pe /audit-ai/
2. Mutarea bazei de date pe Supabase, pentru dashboard din orice browser
3. WhatsApp Business API oficial, daca volumul depaseste trimiterea manuala
