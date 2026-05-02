import sqlite3

DB = "users.db"
STANDARD_KONTO_ID = 1  # ← Falls du ein spezielles Standardkonto willst, hier anpassen

def migrate_transactions_table():
    with sqlite3.connect(DB) as conn:
        cursor = conn.cursor()

        # 1. Prüfen, ob die Spalte konto_id bereits existiert
        cursor.execute("PRAGMA table_info(transactions)")
        spalten = [spalte[1] for spalte in cursor.fetchall()]
        
        if "konto_id" in spalten:
            print("✅ Spalte 'konto_id' existiert bereits.")
        else:
            print("➕ Füge Spalte 'konto_id' hinzu…")
            cursor.execute("ALTER TABLE transactions ADD COLUMN konto_id INTEGER")
            print("✅ Spalte 'konto_id' wurde hinzugefügt.")

        # 2. Leere Felder mit Standardkonto befüllen (nur NULL)
        print("🔄 Aktualisiere bestehende Buchungen mit Standardkonto…")
        cursor.execute("""
            UPDATE transactions
            SET konto_id = ?
            WHERE konto_id IS NULL OR konto_id = ''
        """, (STANDARD_KONTO_ID,))
        updated = cursor.rowcount
        print(f"✅ {updated} Buchungen aktualisiert.")

        conn.commit()

if __name__ == "__main__":
    migrate_transactions_table()
