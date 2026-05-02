import sqlite3

with sqlite3.connect("users.db") as conn:
    conn.execute("DROP TABLE IF EXISTS transactions")
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            verwendungszweck TEXT
        )
    """)

print("✅ Tabelle 'transactions' wurde komplett neu erstellt.")