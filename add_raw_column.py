import sqlite3

conn = sqlite3.connect("users.db")
cursor = conn.cursor()

cursor.execute("""
ALTER TABLE newsletter_kontakte ADD COLUMN raw_data TEXT
""")

conn.commit()
conn.close()

print("✅ raw_data ergänzt")
