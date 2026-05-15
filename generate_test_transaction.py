import sqlite3
from datetime import date, timedelta
import random

DB = "users.db"
EMPFAENGER = [
    "AOK Nordost",
    "Konzertkasse Hamburg",
    "Buchhandlung Blattgold",
    "STRATO GmbH",
    "Adobe Systems",
    "Telefonica",
    "Büromarkt Böttcher",
    "Google Ireland",
    "VHS Berlin",
    "Postbank"
]

def generate_testdaten(user_id=1, count=10):
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        today = date.today()

        for i in range(count):
            empfaenger = random.choice(EMPFAENGER)
            verwendung = f"Rechnung {random.randint(1000, 9999)}"
            datum = today - timedelta(days=random.randint(1, 120))
            betrag = round(random.uniform(-1000, 1000), 2)

            conn.execute("""
                INSERT INTO import_transaktionen (user_id, datum, empfaenger, verwendungszweck, betrag, verarbeitet)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (user_id, datum.isoformat(), empfaenger, verwendung, betrag))

        conn.commit()
        print(f"✅ {count} Testtransaktionen für user_id={user_id} eingefügt.")

if __name__ == "__main__":
    generate_testdaten()
