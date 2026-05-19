import sqlite3


def finde_vorschlag(conn: sqlite3.Connection, empfaenger: str, verwendungszweck: str) -> tuple[int | None, float | None]:
        empfaenger_l = (empfaenger or "").lower()
        verwendungszweck_l = (verwendungszweck or "").lower()
        for r in conn.execute("SELECT suchbegriff, konto_id, steuersatz FROM regeln"):
            sb = (r[0] or "").lower()
            if sb and (sb in empfaenger_l or sb in verwendungszweck_l):
                return int(r[1]), float(r[2])
        return None, None