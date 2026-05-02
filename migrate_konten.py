import sqlite3

DB_NAME = "users.db"

def migrate_konten():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()

        # Prüfen, ob die alte Tabelle bereits migriert wurde
        cursor.execute("PRAGMA table_info(konten)")
        columns = [col[1] for col in cursor.fetchall()]
        if "typ" not in columns:
            print("⚠️ Tabelle 'konten' existiert nicht.")
            return

        # Typ prüfen – ist 'neutral' schon erlaubt?
        try:
            cursor.execute("INSERT INTO konten (name, typ) VALUES (?, ?)", ("Testkonto", "neutral"))
            conn.rollback()  # rückgängig machen
            print("✅ 'neutral' ist bereits erlaubt – keine Migration nötig.")
            return
        except sqlite3.IntegrityError:
            pass  # Migration nötig

        print("🔄 Migriere Tabelle 'konten'…")

        # Schritt 1: alte Tabelle umbenennen
        cursor.execute("ALTER TABLE konten RENAME TO konten_alt")

        # Schritt 2: neue Tabelle mit erweitertem CHECK
        cursor.execute("""
            CREATE TABLE konten (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                typ TEXT NOT NULL CHECK (typ IN ('einnahme', 'ausgabe', 'neutral'))
            )
        """)

        # Schritt 3: alte Daten übernehmen, steuer → neutral
        cursor.execute("""
            INSERT INTO konten (id, name, typ)
            SELECT id, name,
                   CASE WHEN typ = 'steuer' THEN 'neutral' ELSE typ END
            FROM konten_alt
        """)

        # Schritt 4: alte Tabelle löschen
        cursor.execute("DROP TABLE konten_alt")

        conn.commit()
        print("✅ Migration erfolgreich abgeschlossen.")

if __name__ == "__main__":
    migrate_konten()
