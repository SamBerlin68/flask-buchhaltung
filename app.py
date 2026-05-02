from __future__ import annotations

import os
import io
import csv
import sqlite3
from datetime import datetime
from datetime import date
import pandas as pd
import json


# später absichern


from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    url_for,
    flash,
)
from flask_bcrypt import Bcrypt

# chardet ist optional – wenn nicht installiert, fallback auf utf-8
try:
    import chardet  # type: ignore
except Exception:
    chardet = None  # type: ignore


# -----------------------------------------------------------------------------
# App Factory
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)
    secret = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["SECRET_KEY"] = secret
    if secret == "dev-secret-change-me":
        print("⚠️ Warnung: FLASK_SECRET_KEY nicht gesetzt – nutze unsicheren Dev-Default.")
    app.config["DATABASE"] = os.getenv("DB_PATH", os.path.abspath("users.db"))

    bcrypt = Bcrypt(app)

    @app.template_filter("euro")
    def format_euro(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "0,00 €"

        formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
        
        if value < 0:
            return f'<span class="text-danger">−{formatted}</span>'

        return formatted


    @app.template_filter("percent_de")
    def format_percent(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "0,0 %"

        return f"{value:.1f}".replace(".", ",") + " %"

    # -------------------------------------------------------------------------
    # DB Helpers
    # -------------------------------------------------------------------------
    def get_db() -> sqlite3.Connection:
        conn = sqlite3.connect(app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        return conn

    def init_db() -> None:
        with get_db() as conn:
            # Debug-Ausgabe der SQLs (auskommentieren, wenn zu laut)
            # conn.set_trace_callback(print)

            # users
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
                """
            )

            # konten – inkl. kontonummer (DATEV) und optionalem Standard-Steuersatz
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS konten (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    typ  TEXT NOT NULL CHECK (typ IN ('einnahme','ausgabe','neutral')),
                    kontonummer TEXT,
                    standard_steuersatz REAL
                )
                """
            )
            # Unique-Index auf kontonummer (nur wenn gesetzt)
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_konten_kontonummer
                ON konten(kontonummer) WHERE kontonummer IS NOT NULL
                """
            )
            # anfragen
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS anfragen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    email TEXT,
                    spielort TEXT,
                    telefon TEXT,
                    nachricht TEXT,
                    programm TEXT,
                    status TEXT DEFAULT 'neu',
                    raw_data TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            # kontakte
            conn.execute(   
                """
                CREATE TABLE IF NOT EXISTS kontakte (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    email TEXT,
                    telefon TEXT,
                    ort TEXT,
                    status TEXT DEFAULT 'neu',
                    raw_data TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            # regeln
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS regeln (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suchbegriff TEXT NOT NULL,
                    konto_id INTEGER NOT NULL,
                    steuersatz REAL NOT NULL DEFAULT 19.0,
                    FOREIGN KEY (konto_id) REFERENCES konten(id)
                )
                """
            )

            # Importtabelle (Rohbank)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS import_transaktionen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    datum TEXT NOT NULL,           -- ISO YYYY-MM-DD
                    empfaenger TEXT NOT NULL,
                    verwendungszweck TEXT,
                    betrag REAL NOT NULL,          -- Brutto (+Einnahme / -Ausgabe)
                    verarbeitet INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )

            # Buchungen – Netto + Steuer (separat)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS buchungen (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    datum TEXT NOT NULL,           -- ISO YYYY-MM-DD
                    empfaenger TEXT NOT NULL,
                    verwendungszweck TEXT,
                    konto_id INTEGER NOT NULL,
                    betrag_netto REAL NOT NULL,
                    steuerbetrag REAL NOT NULL DEFAULT 0.0, -- +Vorsteuer / -Umsatzsteuer
                    steuersatz REAL NOT NULL DEFAULT 0.0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    belegnummer TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (konto_id) REFERENCES konten(id)
                )
                """
            )

            # --- Migration: Spalte 'belegnummer' nachziehen (falls noch nicht vorhanden)
            # --- Migrationen für alte DBs: fehlende Spalten in "buchungen" nachziehen
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(buchungen)").fetchall()]

            if "betrag_netto" not in cols:
                conn.execute("ALTER TABLE buchungen ADD COLUMN betrag_netto REAL NOT NULL DEFAULT 0.0")

            if "steuerbetrag" not in cols:
                conn.execute("ALTER TABLE buchungen ADD COLUMN steuerbetrag REAL NOT NULL DEFAULT 0.0")

            if "steuersatz" not in cols:
                conn.execute("ALTER TABLE buchungen ADD COLUMN steuersatz REAL NOT NULL DEFAULT 0.0")

            if "created_at" not in cols:
                conn.execute("ALTER TABLE buchungen ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))")

            if "belegnummer" not in cols:
                conn.execute("ALTER TABLE buchungen ADD COLUMN belegnummer TEXT")



            # Veraltete Tabelle bereinigen (best effort)
            try:
                conn.execute("DROP TABLE IF EXISTS transactions")
            except sqlite3.Error:
                pass

            # neutrales Steuerkonto sicherstellen (explizit!)
            row = conn.execute(
                "SELECT id FROM konten WHERE name = ?",
                ("Umsatzsteuer/Vorsteuer",),
            ).fetchone()

            if row is None:
                conn.execute(
                    "INSERT INTO konten (name, typ, kontonummer) VALUES (?,?,?)",
                    ("Umsatzsteuer/Vorsteuer", "neutral", None),
                )

    # Hilfsfunktionen ----------------------------------------------------------
    
    def import_kontakte():
        df = pd.read_excel("Adressen Veranstalter Nachfass.xlsx")

        with get_db() as conn:
            for _, row in df.iterrows():
                raw = {}

                for key, value in row.items():
                    if pd.isna(value):
                        raw[key] = ""
                    elif isinstance(value, (pd.Timestamp, datetime)):
                        raw[key] = value.strftime("%Y-%m-%d")
                    else:
                        raw[key] = str(value)

                conn.execute("""
                    INSERT INTO kontakte (
                        name,
                        email,
                        telefon,
                        ort,
                        status,
                        raw_data
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                 """, (
                    str(row.get("Location", "")).strip(),
                    str(row.get("Email", "")).strip(),
                    str(row.get("Telefon", "")).strip(),
                    str(row.get("Ort", "")).strip(),
                    str(row.get("Stand", "neu")).strip(),
                    json.dumps(raw, ensure_ascii=False)
                ))

        print("✅ 1:1 Import abgeschlossen")

    @app.route("/import_kontakte")
    def import_kontakte_route():
        #import_kontakte()
        return "Import deakteviert"    

  
    
        
    def parse_date_any(s: str) -> str:
        s = (s or "").strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        raise ValueError(f"Unbekanntes Datumsformat: {s!r}")

    def detect_encoding(raw: bytes) -> str:
        if not chardet:
            return "utf-8"
        res = chardet.detect(raw) or {}
        return res.get("encoding") or "utf-8"

    def finde_vorschlag(conn: sqlite3.Connection, empfaenger: str, verwendungszweck: str) -> tuple[int | None, float | None]:
        empfaenger_l = (empfaenger or "").lower()
        verwendungszweck_l = (verwendungszweck or "").lower()
        for r in conn.execute("SELECT suchbegriff, konto_id, steuersatz FROM regeln"):
            sb = (r[0] or "").lower()
            if sb and (sb in empfaenger_l or sb in verwendungszweck_l):
                return int(r[1]), float(r[2])
        return None, None
    
    def next_belegnummer(conn: sqlite3.Connection, user_id: int, iso_date: str) -> str:
        """Erzeuge Belegnummer 'YYYYMMDD-XXX' je Datum. XXX ab 001 aufwärts."""
        yyyymmdd = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%Y%m%d")
        like = f"{yyyymmdd}-%"

        row = conn.execute(
            "SELECT belegnummer FROM buchungen WHERE user_id = ? AND belegnummer LIKE ? ORDER BY belegnummer DESC LIMIT 1",
            (user_id, like),
        ).fetchone()

        if not row or not row["belegnummer"]:
            return f"{yyyymmdd}-001"

        try:
            last = row["belegnummer"].split("-")[-1]
            nxt = int(last) + 1
        except Exception:
            nxt = 1

        return f"{yyyymmdd}-{nxt:03d}"


    # DB initialisieren
    init_db()

    # -----------------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------------

    @app.route("/")
    def home():
        return redirect(url_for("login"))

    # -------------------------- Auth -------------------------------------------
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            password_confirm = request.form.get("password_confirm", "")

            if not username or not password:
                flash("❌ Bitte Benutzername und Passwort angeben.")
                return redirect(url_for("register"))
            if password != password_confirm:
                flash("❌ Die Passwörter stimmen nicht überein!")
                return redirect(url_for("register"))

            hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO users (username, password) VALUES (?, ?)",
                        (username, hashed_pw),
                    )
                flash("✅ Registrierung erfolgreich! Bitte einloggen.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("❌ Benutzername ist bereits vergeben.")
                return redirect(url_for("register"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, username, password FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
            if user and bcrypt.check_password_hash(user[2], password):
                session["user_id"] = int(user[0])
                flash("👋 Willkommen zurück!")
                return redirect(url_for("dashboard"))
            flash("❌ Login fehlgeschlagen. Benutzername oder Passwort ist falsch.")
            return redirect(url_for("login"))
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("🚪 Du wurdest ausgeloggt.")
        return redirect(url_for("login"))

    # -------------------------- Dashboard --------------------------------------
    @app.route("/dashboard")
    def dashboard():
        if "user_id" not in session:
            flash("⛔ Du musst eingeloggt sein.")
            return redirect(url_for("login"))
        return render_template("dashboard.html")

    # -------------------------- Konten -----------------------------------------
    @app.route("/konten", methods=["GET", "POST"])
    def konten():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            if request.method == "POST":
                name = request.form.get("name", "").strip()
                typ = request.form.get("typ", "").strip()
                kontonummer = (request.form.get("kontonummer") or "").strip() or None
                if name and typ in {"einnahme", "ausgabe", "neutral"}:
                    try:
                        conn.execute(
                            "INSERT INTO konten (name, typ, kontonummer) VALUES (?,?,?)",
                            (name, typ, kontonummer),
                        )
                        flash(f"✅ Konto '{name}' wurde hinzugefügt.")
                        return redirect(url_for("konten"))
                    except sqlite3.IntegrityError:
                        flash("❌ Konto-Name oder -Nummer bereits vorhanden.")
                else:
                    flash("❌ Bitte gültigen Namen und Typ angeben.")
            konten_liste = conn.execute(
                "SELECT * FROM konten ORDER BY name"
            ).fetchall()
        return render_template("konten.html", konten=konten_liste)

    @app.route("/konten/<int:konto_id>")
    def konto_details(konto_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        heute = date.today()

        zeitraum = request.args.get("zeitraum", "monat")  # monat | quartal | jahr
        jahr = int(request.args.get("jahr", heute.year))
        monat = int(request.args.get("monat", heute.month))
        quartal = int(request.args.get("quartal", (heute.month - 1) // 3 + 1))
        # Monat vor / zurück berechnen
        prev_monat = monat - 1
        prev_jahr = jahr
        if prev_monat == 0:
           prev_monat = 12
           prev_jahr -= 1

        next_monat = monat + 1
        next_jahr = jahr
        if next_monat == 13:
           next_monat = 1
           next_jahr += 1

        with get_db() as conn:
            # Konto laden
            konto = conn.execute(
                """
                SELECT * 
                FROM konten 
                WHERE id = ? 
                """,
                (konto_id,),
            ).fetchone()

            if not konto:
                flash("❌ Konto nicht gefunden")
                return redirect(url_for("konten"))

            # Prüfen: Steuerkonto?
            ist_steuerkonto = (konto["name"] == "Umsatzsteuer/Vorsteuer")
           
            if zeitraum == "monat":
               von = date(jahr, monat, 1)
               if monat == 12:
                 bis = date(jahr + 1, 1, 1)
               else:
                bis = date(jahr, monat + 1, 1)

            elif zeitraum == "quartal":
               start_monat = (quartal - 1) * 3 + 1
               von = date(jahr, start_monat, 1)
               if quartal == 4:
                  bis = date(jahr + 1, 1, 1)
               else:
                  bis = date(jahr, start_monat + 3, 1)

            else:  # jahr
               von = date(jahr, 1, 1)
               bis = date(jahr + 1, 1, 1)
            
            # Quartal vor / zurück
            prev_quartal = quartal - 1
            prev_q_jahr = jahr
            if prev_quartal == 0:
               prev_quartal = 4
               prev_q_jahr -= 1

            next_quartal = quartal + 1
            next_q_jahr = jahr
            if next_quartal == 5:
               next_quartal = 1
               next_q_jahr += 1
            

            # Buchungen für dieses Konto laden
            buchungen = conn.execute(
                """
                SELECT *
                FROM buchungen
                WHERE konto_id = ?
                  AND user_id = ?
                  AND datum >= ?
                  AND datum < ?
                ORDER BY datum DESC, id DESC
                """,
                (konto_id, session["user_id"], von.isoformat(), bis.isoformat()),
            ).fetchall()

            # Steuer-Saldo nur für Steuerkonto berechnen
            steuer_saldo = None

            if ist_steuerkonto:
                row = conn.execute(
                    """
                    SELECT ROUND(COALESCE(SUM(steuerbetrag), 0), 2) AS saldo
                    FROM buchungen
                    WHERE konto_id = ?
                      AND user_id = ?
                      AND datum >= ?
                      AND datum < ?
                    """,
                    (konto_id, session["user_id"], von.isoformat(), bis.isoformat()),
                ).fetchone()

                steuer_saldo = row["saldo"]
            
            jahre = conn.execute(
                """
                SELECT DISTINCT strftime('%Y', datum) AS jahr
                FROM buchungen
                WHERE user_id = ?
                ORDER BY jahr DESC
                """,
               (session["user_id"],),
            ).fetchall()

            jahre = [int(r["jahr"]) for r in jahre if r["jahr"] is not None]
        


        return render_template(
           "konto_details.html",
            konto=konto,
            buchungen=buchungen,
            ist_steuerkonto=ist_steuerkonto,
            steuer_saldo=steuer_saldo,
            zeitraum=zeitraum,
            jahr=jahr,
            monat=monat,
            quartal=quartal,
            prev_monat=prev_monat,
            prev_jahr=prev_jahr,
            next_monat=next_monat,
            next_jahr=next_jahr,
            jahre=jahre,
            prev_quartal=prev_quartal,
            prev_q_jahr=prev_q_jahr,
            next_quartal=next_quartal,
            next_q_jahr=next_q_jahr,
            )

    @app.route("/konten/<int:konto_id>/bearbeiten", methods=["GET", "POST"])
    def konto_bearbeiten(konto_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            konto = conn.execute(
                "SELECT * FROM konten WHERE id = ?", (konto_id,)
            ).fetchone()
            if not konto:
                flash("❌ Konto nicht gefunden.")
                return redirect(url_for("konten"))
            if request.method == "POST":
                neuer_name = request.form.get("name", "").strip()
                neuer_typ = request.form.get("typ", "").strip()
                neue_nr = (request.form.get("kontonummer") or "").strip() or None
                if not neuer_name or neuer_typ not in {"einnahme", "ausgabe", "neutral"}:
                    flash("❌ Ungültige Eingabe.")
                    return redirect(url_for("konto_bearbeiten", konto_id=konto_id))
                try:
                    conn.execute(
                        "UPDATE konten SET name = ?, typ = ?, kontonummer = ? WHERE id = ?",
                        (neuer_name, neuer_typ, neue_nr, konto_id),
                    )
                    flash("✅ Konto wurde aktualisiert.")
                    return redirect(url_for("konto_details", konto_id=konto_id))
                except sqlite3.IntegrityError:
                    flash("❌ Kontonummer bereits vergeben.")
                    return redirect(url_for("konto_bearbeiten", konto_id=konto_id))
        return render_template("konto_bearbeiten.html", konto=konto)

    @app.route("/konten/<int:konto_id>/loeschen", methods=["POST"])
    def konto_loeschen(konto_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM buchungen WHERE konto_id = ?",
                (konto_id,),
            ).fetchone()[0]
            if count:
                flash("❌ Konto kann nicht gelöscht werden – es existieren noch Buchungen.")
                return redirect(url_for("konto_details", konto_id=konto_id))
            conn.execute("DELETE FROM konten WHERE id = ?", (konto_id,))
            flash("✅ Konto wurde gelöscht.")
        return redirect(url_for("konten"))

    # -------------------------- Regeln -----------------------------------------
    @app.route("/regeln", methods=["GET", "POST"])
    def regeln():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            if request.method == "POST":
                suchbegriff = request.form.get("suchbegriff", "").strip()
                konto_id = request.form.get("konto_id")
                steuersatz = request.form.get("steuersatz")
                if not suchbegriff or not konto_id:
                    flash("❌ Bitte Suchbegriff und Konto angeben.")
                    return redirect(url_for("regeln"))
                try:
                    conn.execute(
                        "INSERT INTO regeln (suchbegriff, konto_id, steuersatz) VALUES (?,?,?)",
                        (suchbegriff, int(konto_id), float(steuersatz or 19.0)),
                    )
                    flash("✅ Regel hinzugefügt.")
                except Exception as e:
                    flash(f"❌ Konnte Regel nicht speichern: {e}")
                return redirect(url_for("regeln"))
            eintraege = conn.execute(
                """
                SELECT r.id, r.suchbegriff, r.steuersatz, k.name AS konto
                FROM regeln r JOIN konten k ON r.konto_id = k.id
                ORDER BY r.id DESC
                """
            ).fetchall()
            konten_liste = conn.execute(
                "SELECT id, name FROM konten ORDER BY name"
            ).fetchall()
        return render_template("regeln.html", regeln=eintraege, konten=konten_liste)

    @app.route("/regeln/<int:regel_id>/loeschen", methods=["POST"])
    def regel_loeschen(regel_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            conn.execute("DELETE FROM regeln WHERE id = ?", (regel_id,))
        flash("🗑️ Regel wurde gelöscht.")
        return redirect(url_for("regeln"))

    @app.route("/regeln/<int:regel_id>/bearbeiten", methods=["GET", "POST"])
    def regel_bearbeiten(regel_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            if request.method == "POST":
                suchbegriff = request.form.get("suchbegriff", "").strip()
                konto_id = int(request.form.get("konto_id"))
                steuersatz = float(request.form.get("steuersatz") or 19.0)
                conn.execute(
                    "UPDATE regeln SET suchbegriff=?, konto_id=?, steuersatz=? WHERE id=?",
                    (suchbegriff, konto_id, steuersatz, regel_id),
                )
                flash("✅ Regel wurde aktualisiert.")
                return redirect(url_for("regeln"))
            regel = conn.execute(
                "SELECT * FROM regeln WHERE id = ?", (regel_id,)
            ).fetchone()
            konten_liste = conn.execute(
                "SELECT * FROM konten ORDER BY name"
            ).fetchall()
        return render_template("regel_bearbeiten.html", regel=regel, konten=konten_liste)

    # -------------------------- Upload -----------------------------------------
    @app.route("/upload", methods=["GET", "POST"])
    def upload():
        if "user_id" not in session:
            flash("⛔ Du musst eingeloggt sein.")
            return redirect(url_for("login"))

        if request.method == "POST":
            try:
                file = request.files.get("csv_file")
                if not file or file.filename == "":
                    flash("❌ Keine Datei ausgewählt.")
                    return redirect(url_for("upload"))

                raw = file.read()
                if not raw:
                    flash("❌ Datei ist leer.")
                    return redirect(url_for("upload"))

                enc = detect_encoding(raw)
                try:
                    content = raw.decode(enc, errors="replace")
                except Exception:
                    flash(f"❌ Fehler beim Dekodieren (Encoding: {enc}).")
                    return redirect(url_for("upload"))

                # Delimiter automatisch erkennen (Fallback ';')
                try:
                    sample = "\n".join(content.splitlines()[:5])
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    delimiter = dialect.delimiter
                except Exception:
                    delimiter = ";"

                def norm(s: str) -> str:
                    s = (s or "").strip().lower()
                    repl = str.maketrans({
                        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                        " ": "", "/": "", "-": "", ".": "", ":": "",
                    })
                    return s.translate(repl)

                header_map = {
                    "buchungsdatum": "date",
                    "datum": "date",
                    "wertstellung": "date",
                    "empfaenger": "payee",
                    "empfänger": "payee",
                    "beguenstigter": "payee",
                    "verwendungszweck": "purpose",
                    "verwendungszweckprimanota": "purpose",
                    "verwendungszweck1": "purpose",
                    "betrag": "amount",
                    "umsatz": "amount",
                }

                stream = io.StringIO(content)
                reader = csv.DictReader(stream, delimiter=delimiter)
                if not reader.fieldnames:
                    flash("❌ CSV hat keine Kopfzeile.")
                    return redirect(url_for("upload"))

                fields_norm = {norm(fn): fn for fn in reader.fieldnames}

                def get(row: dict, key_alias: str) -> str:
                    for norm_name, original in fields_norm.items():
                        if header_map.get(norm_name) == key_alias:
                            return (row.get(original) or "").strip()
                    return ""

                def parse_amount(text: str) -> float:
                    t = (text or "").strip()
                    neg = t.endswith("-")
                    if neg:
                        t = t[:-1]
                    t = (
                        t.replace("€", "")
                        .replace(" ", "")
                        .replace(".", "")
                        .replace(",", ".")
                    )
                    try:
                        val = float(t)
                    except ValueError:
                        raise ValueError(f"Betrag unlesbar: {text!r}")
                    return -val if neg else val

                count = 0
                with get_db() as conn:
                    for row in reader:
                        try:
                            datum_raw = get(row, "date")
                            empfaenger = get(row, "payee")
                            verwendungszweck = get(row, "purpose")
                            betrag_raw = get(row, "amount")

                            if not (datum_raw and betrag_raw):
                                continue

                            datum = parse_date_any(datum_raw)
                            betrag = parse_amount(betrag_raw)

                            conn.execute(
                                """
                                INSERT INTO import_transaktionen (user_id, datum, empfaenger, verwendungszweck, betrag, verarbeitet)
                                VALUES (?,?,?,?,?,0)
                                """,
                                (
                                    session["user_id"],
                                    datum,
                                    empfaenger,
                                    verwendungszweck,
                                    betrag,
                                ),
                            )
                            count += 1
                        except Exception as e:
                            print(f"⚠️ Fehler beim Verarbeiten einer Zeile: {e}")
                            continue

                if count == 0:
                    flash("ℹ️ Es konnten keine Transaktionen importiert werden. Bitte Delimiter/Spalten prüfen.")
                else:
                    flash(f"✅ {count} Transaktionen wurden importiert. Jetzt kannst du sie zuordnen.")
                return redirect(url_for("zuordnen"))

            except Exception as e:
                print(f"❌ Upload-Fehler: {e}")
                flash(f"❌ Unerwarteter Fehler beim Upload: {e}")
                return redirect(url_for("upload"))

        return render_template("upload.html")
    
    @app.route("/zuordnung_loeschen", methods=["POST"])
    def zuordnung_loeschen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        
        with get_db() as conn:
            cur = conn.execute(
                "DELETE FROM import_transaktionen WHERE user_id = ? AND verarbeitet = 0",
                (session["user_id"],),
            )
            deleted = cur.rowcount or 0

        flash(f"🧹 Zuordnungsdaten (offene Importe) wurden gelöscht ({deleted}). Buchungen bleiben erhalten.")
        return redirect(url_for("dashboard"))

    # -------------------------- Zuordnen ----------------------------------------
    @app.route("/zuordnen", methods=["GET", "POST"])
    def zuordnen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            if request.method == "POST":
                # ------- NUR POST -------
                trans_id = request.form.get("trans_id")
                konto_id = request.form.get("konto_id")
                steuersatz = request.form.get("steuersatz")
                speichere_regel = request.form.get("regel_speichern") == "1"
                reverse_charge = request.form.get("reverse_charge") == "1"
                teilbetrag_raw = (request.form.get("teilbetrag") or "").strip()

                if not trans_id or not konto_id:
                    flash("❌ Ungültige Eingabe.")
                    return redirect(url_for("zuordnen"))

                trans = conn.execute(
                    "SELECT * FROM import_transaktionen WHERE id = ? AND user_id = ? AND verarbeitet = 0",
                    (trans_id, session["user_id"]),
                ).fetchone()

                
                
                if not trans:
                    flash("❌ Transaktion nicht gefunden oder schon verarbeitet.")
                    return redirect(url_for("zuordnen"))
                
                belegnummer = (request.form.get("belegnummer") or "").strip()
                if not belegnummer:
                    belegnummer = next_belegnummer(conn, session["user_id"], trans["datum"])


                ERLAUBTE_NEUTRALE_KONTEN = (
                    "Privatentnahmen",
                    "Privateinlagen",
                    "Umsatzsteuer/Vorsteuer",
                    "Durchlaufende Posten",
                )   

                konto = conn.execute(
                    "SELECT id, typ, name FROM konten WHERE id = ?",
                    (int(konto_id),),
                ).fetchone()

                if not konto:
                    flash("❌ Konto nicht gefunden.")
                    return redirect(url_for("zuordnen"))

                if konto["typ"] == "neutral" and konto["name"] not in ERLAUBTE_NEUTRALE_KONTEN:
                    flash("❌ Dieses neutrale Konto kann hier nicht gebucht werden.")
                    return redirect(url_for("zuordnen"))
                
                if konto["typ"] == "neutral":
                    steuersatz_f = 0.0
                else:
                    steuersatz_f = float(steuersatz or 0.0)

                brutto = float(trans["betrag"])

                # Teilbetrag (optional)
                if teilbetrag_raw:
                    try:
                        teilbetrag = float(teilbetrag_raw.replace(",", "."))
                    except ValueError:
                        flash("❌ Teilbetrag unlesbar.")
                        return redirect(url_for("zuordnen"))
                    if (
                        teilbetrag == 0.0
                        or (brutto > 0 and teilbetrag < 0)
                        or (brutto < 0 and teilbetrag > 0)
                        or abs(teilbetrag) > abs(brutto) + 1e-9
                    ):
                        flash("❌ Ungültiger Teilbetrag.")
                        return redirect(url_for("zuordnen"))
                    betrag_to_book = teilbetrag
                else:
                    betrag_to_book = brutto

                # Steuerkonto (Umsatzsteuer/Vorsteuer) explizit holen
                steuer_row = conn.execute(
                    "SELECT id FROM konten WHERE name = ?",
                    ("Umsatzsteuer/Vorsteuer",),
                ).fetchone()

                if not steuer_row:
                    conn.execute(
                        """
                        INSERT INTO konten (name, typ, kontonummer)
                        VALUES (?,?,?)
                    """,
                ("Umsatzsteuer/Vorsteuer", "neutral", None),
                )
                steuer_row = conn.execute(
                    "SELECT id FROM konten WHERE name = ?",
                    ("Umsatzsteuer/Vorsteuer",),
                ).fetchone()

                steuer_konto_id = int(steuer_row["id"])

                # ---------- Reverse-Charge ----------
                if reverse_charge:
                    netto = betrag_to_book
                    rc_steuer = round(abs(netto) * (steuersatz_f / 100.0), 2) if steuersatz_f > 0 else 0.0

                    # Hauptbuchung (nur Netto)
                    conn.execute(
                        """
                        INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            session["user_id"],
                            trans["datum"],
                            trans["empfaenger"],
                            trans["verwendungszweck"],
                            int(konto_id),
                            round(netto, 2),
                            0.0,
                            steuersatz_f,
                            belegnummer,
                        ),
                    )

                    # +VSt und -USt aufs Steuerkonto
                    if rc_steuer > 0:
                        conn.execute(
                            """
                            INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                            VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                session["user_id"],
                                trans["datum"],
                                "Vorsteuer §13b (RC)",
                                trans["verwendungszweck"],
                                steuer_konto_id,
                                0.0,
                                rc_steuer,
                                steuersatz_f,
                                belegnummer,
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                            VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                session["user_id"],
                                trans["datum"],
                                "Umsatzsteuer §13b (RC)",
                                trans["verwendungszweck"],
                                steuer_konto_id,
                                0.0,
                                -rc_steuer,
                                steuersatz_f,
                                belegnummer,
                            ),
                        )

                else:
                    # ---------- Normalfall ----------
                    if steuersatz_f > 0:
                        netto = betrag_to_book / (1 + steuersatz_f / 100)
                        steuer = betrag_to_book - netto
                    else:
                        netto, steuer = betrag_to_book, 0.0

                    steuer_abs = round(abs(steuer), 2)
                    steuerbetrag_steuerkonto = (
                        steuer_abs if konto["typ"] == "ausgabe" else -steuer_abs
                    )

                    # Hauptbuchung (nur Netto)
                    conn.execute(
                        """
                        INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            session["user_id"],
                            trans["datum"],
                            trans["empfaenger"],
                            trans["verwendungszweck"],
                            int(konto_id),
                            round(netto, 2),
                            0.0,
                            steuersatz_f,
                            belegnummer,
                        ),
                    )

                    # Steuer separat aufs Steuerkonto
                    if steuer_abs > 0:
                        conn.execute(
                            """
                            INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                            VALUES (?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                session["user_id"],
                                trans["datum"],
                                "Vorsteuer (auto)" if steuerbetrag_steuerkonto > 0 else "Umsatzsteuer (auto)",
                                trans["verwendungszweck"],
                                steuer_konto_id,
                                0.0,                       # ✅ WICHTIG: NOT NULL erfüllen
                                steuerbetrag_steuerkonto,
                                steuersatz_f,
                                belegnummer,
                            ),
                        )

                # Restbetrag / verarbeitet-Flag
                if betrag_to_book != brutto:
                    rest = round(brutto - betrag_to_book, 2)
                    if abs(rest) < 0.01:
                        conn.execute(
                            "UPDATE import_transaktionen SET verarbeitet = 1 WHERE id = ?",
                            (trans["id"],),
                        )
                    else:
                        conn.execute(
                            "UPDATE import_transaktionen SET betrag = ?, verarbeitet = 0 WHERE id = ?",
                            (rest, trans["id"]),
                        )
                else:
                    conn.execute(
                        "UPDATE import_transaktionen SET verarbeitet = 1 WHERE id = ?",
                        (trans["id"],),
                    )

                if speichere_regel:
                    conn.execute(
                        "INSERT INTO regeln (suchbegriff, konto_id, steuersatz) VALUES (?,?,?)",
                        (trans["empfaenger"], int(konto_id), steuersatz_f),
                    )
                    flash("💾 Regel gespeichert.")
                else:
                    flash("✅ Reverse-Charge-Buchung erfasst." if reverse_charge else "✅ Transaktion wurde gebucht.")

                return redirect(url_for("zuordnen"))

            # ------- GET: Liste -------
            transaktionen = conn.execute(
                "SELECT * FROM import_transaktionen WHERE user_id = ? AND verarbeitet = 0 ORDER BY datum DESC",
                (session["user_id"],),
            ).fetchall()
            konten_liste = conn.execute(
                "SELECT * FROM konten ORDER BY name"
            ).fetchall()

            vorschlaege: dict[int, dict] = {}
            beleg_cache: dict[str, str] = {}

            for t in transaktionen:
                konto_id_suggest, vat_suggest = finde_vorschlag(conn, t["empfaenger"], t["verwendungszweck"])

                d = t["datum"]
                if d not in beleg_cache:
                    beleg_cache[d] = next_belegnummer(conn, session["user_id"], d)

                vorschlaege[t["id"]] = {
                    "konto_id": konto_id_suggest,
                    "steuersatz": (vat_suggest if vat_suggest is not None else 19.0),
                    "belegnummer": beleg_cache[d],
                }

        return render_template(
            "zuordnen.html",
            transaktionen=transaktionen,
            konten=konten_liste,
            vorschlaege=vorschlaege,
        )
    
    #-----------------------------Kontakt---------------------------------------
    @app.route("/kontakte")
    def kontakte():
        if "user_id" not in session:
            return redirect(url_for("login"))

        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM kontakte
                ORDER BY created_at DESC
            """).fetchall()

        return render_template("kontakte.html", kontakte=rows)

    # -------------------------- Buchungen --------------------------------------
    @app.route("/buchungen", methods=["GET"])
    def buchungen_liste():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT b.*, k.name AS konto_name, k.typ AS konto_typ, k.kontonummer AS konto_nummer
                FROM buchungen b
                JOIN konten k ON b.konto_id = k.id
                WHERE b.user_id = ?
                ORDER BY b.datum DESC, b.id DESC
                """,
                (session["user_id"],),
            ).fetchall()
        return render_template("buchungen.html", buchungen=rows)

    @app.route("/buchungen/loeschen", methods=["POST"], endpoint="buchungen_loeschen")
    def buchungen_loeschen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        ids = request.form.getlist("ids")
        if not ids:
            flash("ℹ️ Es wurden keine Buchungen ausgewählt.")
            return redirect(url_for("buchungen_liste"))

        try:
            id_ints = [int(x) for x in ids]
        except ValueError:
            flash("❌ Ungültige Auswahl.")
            return redirect(url_for("buchungen_liste"))

        placeholders = ",".join("?" for _ in id_ints)
        params = tuple(id_ints) + (session["user_id"],)

        with get_db() as conn:
            conn.execute(
                f"DELETE FROM buchungen WHERE id IN ({placeholders}) AND user_id = ?",
                params,
            )

        flash(f"🗑️ {len(id_ints)} Buchung(en) gelöscht.")
        return redirect(url_for("buchungen_liste"))
    
    @app.route("/buchungen/<int:buchung_id>/bearbeiten", methods=["GET", "POST"])
    def buchung_bearbeiten(buchung_id):
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            # Bestehende Buchung laden
            buchung = conn.execute(
                """
                SELECT b.*, k.name AS konto_name
                FROM buchungen b
                JOIN konten k ON k.id = b.konto_id
                WHERE b.id = ? AND b.user_id = ?
                """,
                (buchung_id, session["user_id"]),
            ).fetchone()

            if not buchung:
                flash("❌ Buchung nicht gefunden.")
                return redirect(url_for("buchungen_liste"))

            if request.method == "POST":
                datum = request.form.get("datum")
                empfaenger = request.form.get("empfaenger", "").strip()
                verwendungszweck = request.form.get("verwendungszweck", "").strip()
                konto_id = int(request.form.get("konto_id"))
                steuersatz = float(request.form.get("steuersatz") or 0.0)
                belegnummer = request.form.get("belegnummer", "").strip()
                # Netto aus Formular
                netto = float(request.form.get("betrag_netto") or 0.0)
                

                 # Kontotyp & -name laden
                konto = conn.execute(
                    "SELECT typ, name FROM konten WHERE id = ?",
                        (konto_id,),
                ).fetchone()

                if not konto:
                    flash("❌ Konto nicht gefunden.")
                    return redirect(url_for("buchungen_liste"))
                
            # ==========================
            # STEUERLOGIK (ZENTRAL)
            # ==========================

                if konto["name"] == "Umsatzsteuer/Vorsteuer":
                    # Steuerkonto:
                    # Netto immer 0, Steuer bleibt Steuer
                    netto = 0.0
                    steuersatz = 0.0 
                    steuerbetrag = abs(float(request.form.get("steuerbetrag") or 0.0))

                elif konto["typ"] == "neutral":
                    # Andere neutrale Konten (Privatentnahmen etc.)
                    netto = 0.0
                    steuersatz = 0.0
                    steuerbetrag = 0.0

                else:
                    # Einnahme / Ausgabe
                    steuerbetrag = round(abs(netto) * steuersatz / 100, 2)

                    # Vorzeichenlogik:
                    # Ausgabe (netto < 0)  → Vorsteuer positiv
                    # Einnahme (netto > 0) → Umsatzsteuer negativ
                    steuerbetrag = steuerbetrag if netto < 0 else -steuerbetrag    

                # ==========================
                # UPDATE
                # ==========================

                conn.execute(
                    """
                    UPDATE buchungen
                    SET
                        datum = ?,
                        empfaenger = ?,
                        verwendungszweck = ?,
                        konto_id = ?,
                        betrag_netto = ?,
                        steuerbetrag = ?,
                        steuersatz = ?,
                        belegnummer = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        datum,
                        empfaenger,
                        verwendungszweck,
                        konto_id,
                        netto,
                        steuerbetrag,
                        steuersatz,
                        belegnummer,
                        buchung_id,
                        session["user_id"],
                    ),
                )

                flash("✅ Buchung wurde aktualisiert.")
                return redirect(url_for("buchungen_liste"))
            
            # Konten für Dropdown laden
            konten = conn.execute(
                """
                SELECT id, name, typ
                FROM konten
                WHERE typ != 'neutral'
                OR name IN ('Privatentnahmen', 'Privateinlagen', 'Umsatzsteuer/Vorsteuer')
                ORDER BY typ, name
                """
            ).fetchall()

            return render_template(
                "buchung_bearbeiten.html",
                buchung=buchung,
                konten=konten,
            )
    @app.route("/buchungen/<int:buchung_id>/steuer-korrigieren", methods=["POST"])
    def steuerzahlung_korrigieren(buchung_id):
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            buchung = conn.execute(
            """
            SELECT b.id, b.betrag_netto, b.steuerbetrag, k.name AS konto_name
            FROM buchungen b
            JOIN konten k ON b.konto_id = k.id
            WHERE b.id = ? AND b.user_id = ?
            """,
            (buchung_id, session["user_id"]),
        ).fetchone()

        if not buchung:
            flash("❌ Buchung nicht gefunden.")
            return redirect(url_for("buchungen_liste"))

        if buchung["konto_name"] != "Umsatzsteuer/Vorsteuer":
            flash("❌ Diese Funktion ist nur für Steuerzahlungen.")
            return redirect(url_for("buchungen_liste"))


        # Nur korrigieren, wenn Steuerzahlung fälschlich im Netto steht
        if buchung["betrag_netto"] != 0 and buchung["steuerbetrag"] == 0:
            steuerbetrag = abs(buchung["betrag_netto"])
        else:
            flash("ℹ️ Diese Buchung muss nicht korrigiert werden.")
            return redirect(url_for("buchungen_liste"))

        conn.execute(
            """
            UPDATE buchungen
            SET betrag_netto = 0.0,
                steuerbetrag = ?,
                steuersatz = 0.0
            WHERE id = ? AND user_id = ?
            """,
            (steuerbetrag, buchung_id, session["user_id"]),
        )

        flash("♻️ Steuerzahlung wurde korrekt zugeordnet.")
        return redirect(url_for("buchungen_liste"))
    
    @app.route("/buchungen/neu", methods=["GET", "POST"])
    def buchung_neu():
        if "user_id" not in session:
           flash("⛔ Bitte einloggen.")
           return redirect(url_for("login"))

        with get_db() as conn:
            konten = conn.execute(
            """
            SELECT id, name, typ
            FROM konten
            WHERE typ != 'neutral'
               OR name IN ('Privateinlagen', 'Privatentnahmen')
            ORDER BY typ, name
            """
           ).fetchall()

        if request.method == "POST":
           datum = request.form["datum"]
           empfaenger = request.form.get("empfaenger", "").strip()
           verwendungszweck = request.form.get("verwendungszweck", "").strip()
           konto_id = int(request.form["konto_id"])
           netto = float(request.form["betrag_netto"])
           steuersatz = float(request.form.get("steuersatz", 0))
           belegnummer = request.form.get("belegnummer", "").strip()

           with get_db() as conn:
            konto = conn.execute(
                "SELECT typ FROM konten WHERE id = ?",
                (konto_id,),
            ).fetchone()

            # ==============================
            # 1️⃣ HAUPTBUCHUNG (nur Netto!)
            # ==============================
            conn.execute(
                """
                INSERT INTO buchungen
                (user_id, datum, empfaenger, verwendungszweck,
                 konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    session["user_id"],
                    datum,
                    empfaenger,
                    verwendungszweck,
                    konto_id,
                    netto,
                    0.0,           # ✅ IMMER 0 im Sachkonto
                    steuersatz,
                    belegnummer,
                ),
            )

            # ======================================
            # 2️⃣ STEUERBUCHUNG INS STEUERKONTO
            # ======================================
            if steuersatz > 0 and konto["typ"] != "neutral":

                steuer_abs = round(abs(netto) * steuersatz / 100, 2)

                if konto["typ"] == "ausgabe":
                   # Vorsteuer immer positiv
                   steuerbetrag_steuerkonto = steuer_abs
                   steuer_text = "Vorsteuer (manuell)"
                else:
                    # Umsatzsteuer immer negativ
                    steuerbetrag_steuerkonto = -steuer_abs
                    steuer_text = "Umsatzsteuer (manuell)"

                steuer_konto = conn.execute(
                    "SELECT id FROM konten WHERE name = 'Umsatzsteuer/Vorsteuer'",
                ).fetchone()

                if steuer_konto:
                    conn.execute(
                        """
                        INSERT INTO buchungen
                        (user_id, datum, empfaenger, verwendungszweck,
                         konto_id, betrag_netto, steuerbetrag, steuersatz, belegnummer)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            session["user_id"],
                            datum,
                            steuer_text,
                            verwendungszweck,
                            steuer_konto["id"],
                            0.0,                          # ✅ Netto immer 0
                            steuerbetrag_steuerkonto,     # ✅ korrektes Vorzeichen
                            steuersatz,
                            belegnummer,
                        ),
                    )
            flash("✅ Buchung wurde manuell erfasst.")
            return redirect(url_for("buchungen_liste"))

        return render_template(
        "buchung_neu.html",
        konten=konten,
        )   

    @app.route("/ustva")
    def ustva():
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("login"))

        jahr = int(request.args.get("jahr", date.today().year))
        quartal = int(request.args.get("quartal", 1))

        start_monat = (quartal - 1) * 3 + 1
        von = date(jahr, start_monat, 1)
        if start_monat + 3 > 12:
            bis = date(jahr + 1, 1, 1)
        else:
            bis = date(jahr, start_monat + 3, 1)

        with get_db() as conn:

            steuer_konto = conn.execute(
            "SELECT id FROM konten WHERE name = 'Umsatzsteuer/Vorsteuer'"
            ).fetchone()

            if not steuer_konto:
                flash("❌ Steuerkonto nicht gefunden.")
                return redirect(url_for("dashboard"))

            steuer_id = steuer_konto["id"]

            row = conn.execute(
                """
                SELECT COALESCE (SUM(steuerbetrag), 0)
                FROM buchungen
                WHERE user_id = ?
                    AND konto_id = ?
                    AND datum >= ?
                    AND datum < ?
                    AND steuersatz = 19
                    AND steuerbetrag < 0
                """,
                (session["user_id"], steuer_id, von.isoformat(), bis.isoformat()),
            ).fetchone()


            ust_19 = float(row[0]) if row and row[0] is not None else 0.0
            ust_19 = abs(ust_19)

            # Umsatzsteuer 7 %
            row = conn.execute(
                """
                SELECT COALESCE (SUM(steuerbetrag), 0)
                FROM buchungen
                WHERE user_id = ?
                    AND konto_id = ?
                    AND datum >= ?
                    AND datum < ?
                    AND steuersatz = 7
                    AND steuerbetrag < 0
                """,
                (session["user_id"], steuer_id, von.isoformat(), bis.isoformat()),
            ).fetchone()

            ust_7 = float(row[0]) if row and row[0] is not None else 0.0
            ust_7 = abs(ust_7)

            # Vorsteuer 19 %
            vorsteuer_19 = conn.execute(
                """
                SELECT COALESCE(SUM(steuerbetrag), 0)
                FROM buchungen
                WHERE user_id = ?
                AND konto_id = ?
                AND datum >= ?
                AND datum < ?
                AND steuersatz = 19
                AND steuerbetrag > 0
                """,
                (session["user_id"], steuer_id, von.isoformat(), bis.isoformat()),
            ).fetchone()[0]

            # Vorsteuer 7 %
            vorsteuer_7 = conn.execute(
                """
                SELECT COALESCE(SUM(steuerbetrag), 0)
                FROM buchungen
                WHERE user_id = ?
                AND konto_id = ?
                AND datum >= ?
                AND datum < ?
                AND steuersatz = 7
                AND steuerbetrag > 0
                """,
                (session["user_id"], steuer_id, von.isoformat(), bis.isoformat()),
            ).fetchone()[0]

            # Umsatzsteuer positiv darstellen
            ust_7 = abs(ust_7)

            gesamt_ust = ust_19 + ust_7
            gesamt_vorsteuer = vorsteuer_19 + vorsteuer_7
            zahllast = gesamt_ust - gesamt_vorsteuer

        return render_template(
            "ustva.html",
            jahr=jahr,
            quartal=quartal,
            ust_19=ust_19,
            ust_7=ust_7,
            vorsteuer_19=vorsteuer_19,
            vorsteuer_7=vorsteuer_7,
            gesamt_ust=gesamt_ust,
            gesamt_vorsteuer=gesamt_vorsteuer,
            zahllast=zahllast,
    )

    @app.route("/bwa")
    def bwa():
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("login"))

        heute = date.today()

        zeitraum = request.args.get("zeitraum", "monat")  # monat | quartal | jahr
        jahr = int(request.args.get("jahr", heute.year))
        monat = int(request.args.get("monat", heute.month))
        quartal = int(request.args.get("quartal", (heute.month - 1) // 3 + 1))

    # Zeitraum berechnen
        if zeitraum == "monat":
            von = date(jahr, monat, 1)
            bis = date(jahr + (monat // 12), (monat % 12) + 1, 1)

        elif zeitraum == "quartal":
            start_monat = (quartal - 1) * 3 + 1
            von = date(jahr, start_monat, 1)
            if quartal == 4:
                bis = date(jahr + 1, 1, 1)
            else:
                bis = date(jahr, start_monat + 3, 1)

        else:  # jahr
            von = date(jahr, 1, 1)
            bis = date(jahr + 1, 1, 1)

        with get_db() as conn:

            rows = conn.execute(
                """
                SELECT 
                    k.name,
                    k.typ,
                    ROUND(COALESCE(SUM(b.betrag_netto),0),2) AS summe
                FROM buchungen b
                JOIN konten k ON b.konto_id = k.id
                WHERE b.user_id = ?
                  AND b.datum >= ?
                  AND b.datum < ?
                  AND k.typ IN ('einnahme','ausgabe')
                GROUP BY k.id
                ORDER BY k.typ DESC, summe DESC
                """,
                (session["user_id"], von.isoformat(), bis.isoformat()),
            ).fetchall()

        einnahmen = []
        ausgaben = []

        sum_einnahmen = 0.0
        sum_ausgaben = 0.0

        for r in rows:
            betrag = float(r["summe"])

            if r["typ"] == "einnahme":
                einnahmen.append({
                    "name": r["name"],
                    "betrag": betrag
                })
                sum_einnahmen += betrag

            elif r["typ"] == "ausgabe":
                betrag_pos = abs(betrag)   # 🔥 hier wichtig

                ausgaben.append({
                    "name": r["name"],
                    "betrag": betrag_pos
                })

                sum_ausgaben += betrag_pos

        #sum_ausgaben_abs = abs(sum_ausgaben)

        ergebnis = sum_einnahmen - sum_ausgaben

        # Prozentanteile berechnen
        for e in einnahmen:
            e["anteil"] = (e["betrag"] / sum_einnahmen * 100) if sum_einnahmen else 0

        for a in ausgaben:
            a["anteil"] = (a["betrag"] / sum_ausgaben * 100) if sum_ausgaben else 0

        marge = (ergebnis / sum_einnahmen * 100) if sum_einnahmen else 0
        gewinn_pos = ergebnis >= 0

        return render_template(
            "bwa.html",
            zeitraum=zeitraum,
            jahr=jahr,
            monat=monat,
            quartal=quartal,
            einnahmen=einnahmen,
            ausgaben=ausgaben,
            sum_einnahmen=sum_einnahmen,
            sum_ausgaben=sum_ausgaben,
            ergebnis=ergebnis,
            marge=marge,
            gewinn_pos=gewinn_pos,
        )
    
    @app.route("/anfragen")
    def anfragen():
        if "user_id" not in session:
            return redirect(url_for("login"))

        with get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM anfragen
                ORDER BY created_at DESC
            """).fetchall()

        return render_template("anfragen.html", anfragen=rows)

    @app.route("/anfragen/status", methods=["POST"])
    def anfragen_status():
        if "user_id" not in session:
            return redirect(url_for("login"))

        anfrage_id = request.form.get("id")
        status = request.form.get("status")

        with get_db() as conn:
                conn.execute("""
                    UPDATE anfragen
                    SET status = ?
                    WHERE id = ?
                """, (status, anfrage_id))

        return redirect(url_for("anfragen"))
    
    #----------------------------Kontakte----------------------------------------
    
    # -------------------------- Debug / Tools -----------------------------------
    @app.route("/_debug/routes")
    def _debug_routes():
        rules = "\n".join(sorted(str(r) for r in app.url_map.iter_rules()))
        return f"<pre>{rules}</pre>"

    @app.route("/debug_user")
    def debug_user():
        return f"Deine User-ID: {session.get('user_id')} | DB: {app.config['DATABASE']}"

    @app.route("/reset_all", methods=["POST"], endpoint="reset_all")
    def reset_all():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            conn.execute(
                "DELETE FROM import_transaktionen WHERE user_id = ?",
                (session["user_id"],),
            )
            conn.execute(
                "DELETE FROM buchungen WHERE user_id = ?",
                (session["user_id"],),
            )
            # Optional: Regeln löschen? Dann einkommentieren:
            # conn.execute("DELETE FROM regeln")

        flash("🧹 Daten wurden zurückgesetzt.")
        return redirect(url_for("dashboard"))

    return app


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = create_app()
    print("🚀 Starte Flask auf http://127.0.0.1:5000 ...")
    app.run(debug=True)
