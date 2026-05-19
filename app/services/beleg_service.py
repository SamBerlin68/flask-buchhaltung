import sqlite3
from datetime import datetime

def next_belegnummer(conn: sqlite3.Connection, user_id: int, iso_date: str) -> str:
        """Erzeuge Belegnummer 'YYYYMMDD-XXX' je Datum. XXX ab 001 aufwärts."""
        yyyymmdd = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%Y%m%d")
        like = f"{yyyymmdd}-%"

        row = conn.execute(
            "SELECT belegnummer FROM buchungen WHERE user_id = ? AND belegnummer LIKE ? ORDER BY belegnummer DESC LIMIT 1",
            (user_id, like),
        ).fetchone()

        if not row or not row["belegnummer"]:
            return f"{yyyymmdd}-001"

        try:
            last = row["belegnummer"].split("-")[-1]
            nxt = int(last) + 1
        except Exception:
            nxt = 1

        return f"{yyyymmdd}-{nxt:03d}"