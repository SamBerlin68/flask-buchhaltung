import sqlite3

with sqlite3.connect("users.db") as conn:
    # Tabelle für Konten
    conn.execute("""
        CREATE TABLE IF NOT EXISTS konten (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            typ TEXT CHECK(typ IN ('einnahme', 'ausgabe', 'steuer')) NOT NULL
        )
    """)

    # Tabelle für Regeln
    conn.execute("""
        CREATE TABLE IF NOT EXISTS regeln (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suchbegriff TEXT NOT NULL,
            konto_id INTEGER NOT NULL,
            FOREIGN KEY (konto_id) REFERENCES konten(id)
        )
    """)

    # Neue Buchungstabelle
    conn.execute("""
        CREATE TABLE IF NOT EXISTS buchungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            empfaenger TEXT NOT NULL,
            verwendungszweck TEXT,
            betrag REAL NOT NULL,
            steueranteil REAL,
            konto_id INTEGER,
            FOREIGN KEY (konto_id) REFERENCES konten(id)
        )
    """)

print("✅ Neue Tabellen 'konten', 'regeln' und 'buchungen' wurden angelegt.")
