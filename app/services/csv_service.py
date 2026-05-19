# app/services/csv_service.py

from datetime import datetime

# chardet optional
try:
    import chardet
except Exception:
    chardet = None

def detect_encoding(raw: bytes) -> str:
    if not chardet:
        return "utf-8"
    res = chardet.detect(raw) or {}
    return res.get("encoding") or "utf-8"

def parse_date_any(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
                continue
    raise ValueError(f"Unbekanntes Datumsformat: {s!r}")

def parse_amount(text: str) -> float:
                    t = (text or "").strip()
                    neg = t.endswith("-")
                    if neg:
                        t = t[:-1]
                    t = (
                        t.replace("€", "")
                        .replace(" ", "")
                        .replace(".", "")
                        .replace(",", ".")
                    )
                    try:
                        val = float(t)
                    except ValueError:
                        raise ValueError(f"Betrag unlesbar: {text!r}")
                    return -val if neg else val