import sqlite3

with sqlite3.connect("users.db") as conn:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS import_transaktionen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            empfaenger TEXT NOT NULL,
            verwendungszweck TEXT,
            betrag REAL NOT NULL,
            verarbeitet INTEGER DEFAULT 0
        )
    """)
    print("✅ Tabelle 'import_transaktionen' wurde erfolgreich erstellt.")