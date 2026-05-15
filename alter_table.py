import sqlite3

with sqlite3.connect("users.db") as conn:
    try:
        conn.execute("ALTER TABLE regeln ADD COLUMN steuersatz REAL DEFAULT 19.0")
        print("✅ Spalte 'steuersatz' erfolgreich hinzugefügt.")
    except sqlite3.OperationalError as e:
        print("⚠️ Spalte existiert vielleicht schon:", e)
