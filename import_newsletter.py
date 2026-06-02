import pandas as pd
import sqlite3

# Excel laden
df = pd.read_excel("Adressen Veranstalter Serienmails.xlsx")

conn = sqlite3.connect("users.db")
cursor = conn.cursor()

for _, row in df.iterrows():

    name = str(row.get("Location", "")).strip()
    email = str(row.get("Email", "")).strip()

    if not email:
        continue

    cursor.execute(
        """
        INSERT INTO newsletter_kontakte (
            name, email, telefon, ort, plz, ansprechpartner, anrede
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            name,
            email,
            str(row.get("Telefon", "")).strip(),
            str(row.get("Ort", "")).strip(),
            str(row.get("PLZ", "")).strip(),
            str(row.get("Ansprechpartner", "")).strip(),
            str(row.get("Anrede 1", "")).strip(),
        ),
    )

    kontakt_id = cursor.lastrowid

    # 🔥 Newsletter-Historie erkennen
    for col in df.columns:
        if "Newsletter" in col:
            value = str(row.get(col, "")).strip().lower()

            if value == "x":
                datum = col.replace("Newsletter", "").strip()

                cursor.execute(
                    """
                    INSERT INTO newsletter_logs (kontakt_id, datum)
                    VALUES (?, ?)
                """,
                    (kontakt_id, datum),
                )

conn.commit()
conn.close()

print("✅ Import abgeschlossen")
