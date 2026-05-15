#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", os.path.abspath("users.db"))

KONTEN = [
    ("Umsatzsteuer/Vorsteuer", "neutral", None),

    # Erlöse
    ("Provisionserlöse / Vermittlung",        "einnahme", "4810"),
    ("Erlöse 19 %",                           "einnahme", "4400"),
    ("Erlöse 7 %",                            "einnahme", "4401"),
    ("Weiterberechnung Transport",            "einnahme", "4841"),
    ("Weiterberechnung Künstlercatering",     "einnahme", "4840"),
    ("Weiterberechnung Sonstiges",            "einnahme", "4842"),

    # Aufwendungen
    ("Bewirtungskosten (70 % abzugsfähig)",   "ausgabe",  "4650"),
    ("Nicht abzugsfähige Bewirtung (30 %)",   "ausgabe",  "4654"),
    ("Software-Abonnements / EDV-Dienstl.",   "ausgabe",  "4930"),
    ("Webhosting / Domain (Strato, 19 %)",    "ausgabe",  "4931"),
    ("Künstlerverpflegung (vertraglich)",     "ausgabe",  "3125"),
    ("Vorsteuer 19 %",   "neutral", "1576"),
    ("Vorsteuer 7 %",    "neutral", "1571"),
    ("Umsatzsteuer 19 %","neutral", "1776"),
    ("Umsatzsteuer 7 %", "neutral", "1771"),
    ("Fahrtkosten Bahn/ÖPNV (Deutschlandticket)", "ausgabe", "4670"),
    ("Fortbildungskosten (allgemein)", "ausgabe", "4960"),
    ("Privatentnahmen", "neutral", "1800"),
    ("Privateinlagen",  "neutral", "1890"),
]

STANDARD_STEUERSAETZE = {
    "4810": 19.0,
    "4400": 19.0,
    "4401": 7.0,
    "4841": 19.0,
    "4840": 7.0,
    "4842": 19.0,
    "4930": 19.0,
    "4931": 19.0,
    "4650": 19.0,
    "4654": 19.0,
}

DDL_KONTEN = """
CREATE TABLE IF NOT EXISTS konten (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    typ  TEXT NOT NULL CHECK (typ IN ('einnahme','ausgabe','neutral')),
    kontonummer TEXT,
    standard_steuersatz REAL
);
"""

# WICHTIG: volle UNIQUE-Indizes (ohne WHERE)
IDX_UNIQ_NAME    = "CREATE UNIQUE INDEX IF NOT EXISTS ux_konten_name ON konten(name);"
IDX_UNIQ_KONTONR = "CREATE UNIQUE INDEX IF NOT EXISTS ux_konten_kontonummer ON konten(kontonummer);"

UPSERT_BY_NR = """
INSERT INTO konten (name, typ, kontonummer)
VALUES (?, ?, ?)
ON CONFLICT(kontonummer) DO UPDATE SET
    name = excluded.name,
    typ  = excluded.typ;
"""

UPSERT_BY_NAME = """
INSERT INTO konten (name, typ, kontonummer)
VALUES (?, ?, ?)
ON CONFLICT(name) DO UPDATE SET
    typ         = excluded.typ,
    kontonummer = excluded.kontonummer;
"""

def drop_partial_unique_if_any(con: sqlite3.Connection) -> None:
    """
    Droppe partielle Unique-Indizes auf konten(kontonummer) (WHERE ...),
    weil UPSERT nur mit vollen UNIQUE-Constraints/Indizes funktioniert.
    """
    rows = con.execute("PRAGMA index_list('konten')").fetchall()
    # rows: seq, name, unique, origin, partial
    for _, idx_name, is_unique, origin, is_partial in rows:
        if int(is_unique) == 1 and int(is_partial) == 1:
            # Prüfen, ob der Index auf 'kontonummer' liegt
            cols = con.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
            # cols: seqno, cid, name
            colnames = [c[2] for c in cols]
            if colnames == ["kontonummer"]:
                try:
                    con.execute(f"DROP INDEX IF EXISTS {idx_name}")
                except sqlite3.Error:
                    pass  # best effort

def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(DDL_KONTEN)
    # fehlende Spalten nachziehen (best effort)
    try:
        con.execute("ALTER TABLE konten ADD COLUMN kontonummer TEXT")
    except sqlite3.Error:
        pass
    try:
        con.execute("ALTER TABLE konten ADD COLUMN standard_steuersatz REAL")
    except sqlite3.Error:
        pass

    # erst partielle Unique-Indizes entfernen
    drop_partial_unique_if_any(con)

    # dann volle (nicht-partielle) UNIQUE-Indizes sicherstellen
    con.execute(IDX_UNIQ_NAME)
    con.execute(IDX_UNIQ_KONTONR)

def upsert_konten(con: sqlite3.Connection) -> None:
    for name, typ, nr in KONTEN:
        if nr:
            con.execute(UPSERT_BY_NR, (name, typ, nr))
        else:
            con.execute(UPSERT_BY_NAME, (name, typ, None))
        print(f"✔︎ {nr or '—':>6}  {name} ({typ})")

def apply_default_vats(con: sqlite3.Connection) -> None:
    for nr, vat in STANDARD_STEUERSAETZE.items():
        con.execute(
            "UPDATE konten SET standard_steuersatz = ? WHERE kontonummer = ?",
            (float(vat), nr),
        )

def main() -> None:
    db_file = Path(DB_PATH)
    print(f"📚 Verbinde mit DB: {db_file}")

    con = sqlite3.connect(str(db_file))
    con.execute("PRAGMA foreign_keys = ON;")

    with con:
        ensure_schema(con)
        upsert_konten(con)
        apply_default_vats(con)
        # neutrales Steuerkonto-Name vereinheitlichen (falls Altbestände)
        con.execute("UPDATE konten SET name = 'Umsatzsteuer/Vorsteuer' WHERE typ = 'neutral'")

    con.close()
    print("✅ Fertig. Starte die App und prüfe /konten.")

if __name__ == "__main__":
    main()
