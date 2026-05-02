import sqlite3

# Hilfsfunktion: Konto anlegen, wenn noch nicht vorhanden
def konto_anlegen(name, typ):
    with sqlite3.connect("users.db") as conn:
        cursor = conn.execute("SELECT id FROM konten WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return row[0]
        conn.execute("INSERT INTO konten (name, typ) VALUES (?, ?)", (name, typ))
        print(f"✅ Konto '{name}' ({typ}) wurde angelegt.")
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

# Regel hinzufügen
def regel_anlegen(suchbegriff, konto_id):
    with sqlite3.connect("users.db") as conn:
        conn.execute("INSERT INTO regeln (suchbegriff, konto_id) VALUES (?, ?)", (suchbegriff, konto_id))
        print(f"✅ Regel für '{suchbegriff}' → Konto-ID {konto_id} wurde hinzugefügt.")

# Beispiel: Konten & Regeln definieren
def beispielregeln():
    hosting_id = konto_anlegen("Hosting", "ausgabe")
    einnahme_id = konto_anlegen("Einnahmen", "einnahme")
    steuer_id = konto_anlegen("Umsatzsteuer", "steuer")

    regel_anlegen("STRATO", hosting_id)
    regel_anlegen("AMAZON", einnahme_id)
    regel_anlegen("UST", steuer_id)

beispielregeln()
