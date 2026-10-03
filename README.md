# MOON Prospector — pasul 1: colectare

Colecteaza automat firmele nou infiintate din Romania, le filtreaza pe lista
alba CAEN si le salveaza cu telefon, gata de contactat pe WhatsApp.

## Ce face

```
ultimul CUI stiut         ->  frontiera din baza (stare.ultim_cui)
        v
enumerare CUI-uri valide  ->  de la frontiera in sus, 100 pe cerere
        v
API public ANAF           ->  denumire, adresa, CAEN, TELEFON        [gratis, fara cont]
                              pana la 3 cereri goale la rand = capatul zilei
        v
filtru lista alba CAEN    ->  doar nisele care cumpara servicii Moon
        v
deduplicare pe telefon    ->  un om cu 3 firme e contactat o singura data
        v
Google Places             ->  are fisa? are site? cate recenzii?      [optional]
        v
SQLite / Postgres         ->  prospecti cu status, gata de dashboard
```

**Trucul care conteaza:** CUI-urile se aloca secvential. Stiind cifra de control oficiala, generam toate
CUI-urile valide dintre cel mai mic si cel mai mare CUI al zilei si le intrebam
pe ANAF direct. Intr-o zi reala testata: pagina arata 100 de firme, intervalul
continea **260**. Deci vezi de 2,6 ori mai multe firme decat concurenta care
doar citeste pagina.

**Fara lista ONRC (din oct 2026).** new.firme-on-line.ro blocheaza GitHub (403).
Lista dadea oricum doar capatul zilei, asa ca acum il gasim intreband ANAF lot
cu lot de la ultimul CUI stiut, pana la 3 loturi goale la rand (maxim 80 de
cereri). In modul `auto` lista se mai incearca intai; daca nu merge, rularea
merge mai departe pe ANAF si lasa un „Atenție:" in jurnal.

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
python -m moon.pipeline colectare --sursa anaf  # fara lista ONRC
python -m moon.pipeline colectare --din-setari  # setarile din panou
python -m moon.pipeline colectare --sursa firmeapi
python -m moon.pipeline test-firmeapi          # verifica cheia si raspunsul brut
python -m unittest discover -s teste           # testele
```

## Setari din panou

Pe prospect.themoonagency.ro, la Setari: tier-urile, vechimea maxima, sursa
(`auto` / `anaf`) si lista CAEN (alt tier, cod oprit, cod nou). Se salveaza in
tabela `stare` (`colectare_setari`, `caen_override`). Colectarea de la 08:00 le
foloseste; butonul „Colectează acum" poate trimite valori doar pentru o rulare.
CSV-ul ramane baza; schimbarile din panou se pun peste el.

## Chei optionale

Sistemul merge complet fara ele. Vezi `.env.example`.

| Variabila | Ce activeaza |
|---|---|
| `GOOGLE_PLACES_API_KEY` | Verificarea reala pe Google. Fara ea mesajul nu pretinde ca a cautat. |
| `FIRMEAPI_KEY` | A doua sursa de firme noi (API in loc de scraping, filtrat pe CAEN + telefon) - merge pe **planul gratuit**, 1.000 credite/luna. Administratorul si emailul cer plan platit. |
| `DATABASE_URL` | Postgres/Supabase in loc de SQLite local. |

Fiecare rulare porneste de la ultimul CUI gasit la ANAF, deci nu reinteroghezi
ce ai deja: o zi lucratoare inseamna ~10-15 cereri ANAF, sub un minut.

Rulare automata: `.github/workflows/colectare.yml`, luni-vineri la 08:00 ora
Romaniei (07:00 iarna). Fiecare rulare, reusita sau nu, lasa un rand in `jurnal`.

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
`stare` — ultimul CUI gasit (frontiera) + setarile din panou.
`jurnal` — istoricul rularilor; `eroare` incepe cu „Atenție:" (a mers, cu o observatie)
sau „Eroare:" (a picat ori a ramas la jumatate).

Implicit SQLite (`moon_prospector.db`). Pentru Streamlit Cloud, schema e
compatibila cu Postgres/Supabase — se schimba doar stratul de conexiune din `moon/db.py`.

## Module

| Fisier | Rol |
|---|---|
| `moon/cui.py` | cifra de control CUI + enumerarea intervalului |
| `moon/anaf.py` | client API ANAF (loturi de 100, 1 req/sec) + normalizare telefon |
| `moon/onrc.py` | citeste lista publica de firme noi (doar capatul zilei) |
| `moon/sondare.py` | gaseste capatul zilei intreband ANAF lot cu lot |
| `moon/setari.py` | setarile si lista CAEN din panou |
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
- Limita ANAF e 1 request/secunda. O zi lucratoare (~1.000 de CUI-uri valide) inseamna
  ~10 cereri; dupa o pauza lunga, maxim 80 pe rulare, restul la rularea urmatoare.
- O firma care ar intra la ANAF cu intarziere, cu CUI sub frontiera, nu se mai prinde
  (rar: frontiera e ultimul CUI gasit la ANAF, nu capatul paginii ONRC).

## Manual complet

`MOON-Prospector-Manual.pdf` — instalare, rutina zilnica, mesaje, lista CAEN,
automatizare, ce faci cand raspunde cineva, depanare.

## Urmatorii pasi

1. Al doilea flux: firme existente cu site prost, auditate cu motorul de pe /audit-ai/
2. Mutarea bazei de date pe Supabase, pentru dashboard din orice browser
3. WhatsApp Business API oficial, daca volumul depaseste trimiterea manuala
