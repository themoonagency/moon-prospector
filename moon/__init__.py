"""MOON Prospector - flux de prospectare firme nou infiintate."""
__version__ = "2.3.0"

# Incarca automat cheile din fisierul .env de langa proiect, daca exista.
try:
    from pathlib import Path as _Path
    from dotenv import load_dotenv as _load
    _load(_Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
