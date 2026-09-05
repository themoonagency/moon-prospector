# Mutarea in cloud — Supabase + Streamlit Cloud

Rezultat: dashboard-ul merge de pe orice telefon sau laptop, colectarea zilnica
ruleaza singura din GitHub Actions, iar Mac mini-ul nu mai trebuie sa fie pornit.

---

## Pasul 1 — Supabase (baza de date)

1. Intra pe **supabase.com**, cont gratuit, **New project**
   - Nume: `moon-prospector`
   - Regiune: **Frankfurt (eu-central-1)** — cea mai apropiata de Romania
   - Parola bazei: genereaz-o si **salveaz-o**, o folosesti imediat
2. Dupa ce proiectul e gata: **Connect** (butonul de sus) → sectiunea
   **Connection string** → alege **Session pooler** (NU „Direct connection")
3. Copiaza sirul. Arata asa:
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```
4. Inlocuieste `[YOUR-PASSWORD]` cu parola de la pasul 1 si adauga la final
   `?sslmode=require`

> **De ce Session pooler si nu Direct connection:** conexiunea directa Supabase
> merge doar pe IPv6, iar GitHub Actions si Streamlit Cloud folosesc IPv4.
> Cu „Direct connection" ai primi erori de conectare.

## Pasul 2 — muta datele existente

In `.env`, pe langa cheia Google, adauga linia:

```
DATABASE_URL=postgresql://postgres.xxxx:PAROLA@aws-0-eu-central-1.pooler.supabase.com:6543/postgres?sslmode=require
```

Apoi:

```bash
source .venv/bin/activate
python migreaza_in_supabase.py
```

Iti muta prospectii si blacklist-ul. Baza locala ramane neatinsa, ca rezerva.

## Pasul 3 — GitHub

```bash
git init
git add .
git commit -m "MOON Prospector"
```

Creeaza un repository **privat** pe github.com si urmeaza instructiunile lui
de „push an existing repository".

> Verifica inainte de push: `git status` NU trebuie sa listeze `.env`.
> E in `.gitignore`, dar merita privit o data.

### Secretele pentru colectarea automata

In repo: **Settings → Secrets and variables → Actions → New repository secret**

| Nume | Valoare |
|---|---|
| `DATABASE_URL` | sirul de conexiune Supabase |
| `GOOGLE_PLACES_API_KEY` | cheia Google |
| `FIRMEAPI_KEY` | doar daca o folosesti |

Optional, la **Variables**: `MOON_TIERS` = `A` (sau `A,B`).

Workflow-ul ruleaza automat in fiecare zi lucratoare la 08:00, ora Romaniei.
Poti sa-l pornesti si manual din tabul **Actions → Colectare firme noi → Run workflow**.

## Pasul 4 — Streamlit Cloud

1. **share.streamlit.io** → **New app** → alege repo-ul
   - Main file path: `app.py`
2. **Advanced settings → Secrets**, lipeste:
   ```toml
   DATABASE_URL = "postgresql://...?sslmode=require"
   GOOGLE_PLACES_API_KEY = "AIza..."
   ```
3. **Deploy**

### Ca sa fie doar al tau

**Settings → Sharing** → scoate accesul public si adauga adresa ta de email.
Cand vrei sa dai acces cuiva, adaugi acolo inca un email.

---

## Cum arata rutina dupa mutare

| Cand | Ce se intampla | Cine |
|---|---|---|
| 08:00, luni-vineri | Colectare + verificare pe Google, direct in Supabase | automat |
| cand ai timp | Deschizi dashboard-ul, aprobi, trimiti pe WhatsApp | tu |

Aplicatia adoarme dupa 12 ore fara trafic si se trezeste in ~30 de secunde la
primul click. **Colectarea nu e afectata** — ruleaza in GitHub Actions, nu in
aplicatie. Daca cele 30 de secunde ajung sa deranjeze, se muta pe Render
(~7 $/luna, fara adormire) fara nicio modificare de cod.

## Daca ceva nu merge

| Simptom | Cauza |
|---|---|
| `connection refused` / timeout | Ai folosit Direct connection in loc de Session pooler |
| `password authentication failed` | N-ai inlocuit `[YOUR-PASSWORD]` in sir |
| Dashboard gol dupa deploy | Secretul `DATABASE_URL` lipseste din Streamlit |
| Actions esueaza | Verifica secretele din repo, nu doar cele din Streamlit |
