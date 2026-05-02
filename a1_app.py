from __future__ import annotations

import os
import csv
import io
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

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

try:
    import chardet
except Exception:  # pragma: no cover
    chardet = None

###############################################################################
# Flask App Setup
###############################################################################

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    app.config["DATABASE"] = os.getenv("DB_PATH", os.path.abspath("users.db"))

    bcrypt = Bcrypt(app)

    # ---------------------------------------------------------------------
    # DB Helpers
    # ---------------------------------------------------------------------
    def get_db():
        conn = sqlite3.connect(app.config["DATABASE"])  # type: ignore[arg-type]
        conn.row_factory = sqlite3.Row
        return conn

    def init_db():
        with get_db() as conn:
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

            # konten – typ: einnahme | ausgabe | neutral (Steuer)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS konten (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    typ TEXT NOT NULL CHECK (typ IN ('einnahme','ausgabe','neutral'))
                )
                """
            )

            # regeln – suchbegriff (LIKE-Match), zugeordnetes konto & steuersatz
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

            # Roh-Import (z. B. CSV der Bank)
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

            # Buchungen (vereinheitlicht) – Netto + Steuer separat
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
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (konto_id) REFERENCES konten(id)
                )
                """
            )

            # Optional: Alte/inkonsistente Tabellen still entsorgen
            try:
                conn.execute("DROP TABLE IF EXISTS transactions")
            except sqlite3.Error:
                pass

            # Mindestens ein Steuerkonto anlegen, falls keins existiert
            row = conn.execute("SELECT id FROM konten WHERE typ='neutral' LIMIT 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO konten (name, typ) VALUES (?,?)",
                    ("Umsatzsteuer/Vorsteuer", "neutral"),
                )

    def parse_date_any(s: str) -> str:
        """Return ISO date YYYY-MM-DD from various inputs, or raise ValueError."""
        s = (s or "").strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        # Let it raise – we want to learn about unexpected formats early
        raise ValueError(f"Unbekanntes Datumsformat: {s!r}")

    def detect_encoding(raw: bytes) -> str:
        if not chardet:
            return "utf-8"
        res = chardet.detect(raw) or {}
        return res.get("encoding") or "utf-8"

    def finde_vorschlag(
        konn: sqlite3.Connection, empfaenger: str, verwendungszweck: str
    ) -> Tuple[Optional[int], float]:
        """Grobe Regel-Matching-Logik: enthält suchbegriff in Empfänger oder Verwendungszweck?"""
        empfaenger_l = (empfaenger or "").lower()
        verwendungszweck_l = (verwendungszweck or "").lower()
        for r in konn.execute("SELECT suchbegriff, konto_id, steuersatz FROM regeln"):
            sb = (r[0] or "").lower()
            if sb and (sb in empfaenger_l or sb in verwendungszweck_l):
                return int(r[1]), float(r[2])
        return None, 19.0  # Fallback-Steuersatz

    # Initialize DB on boot
    init_db()

    ###############################################################################
    # Routes
    ###############################################################################

    @app.route("/")
    def home():
        return redirect(url_for("login"))

    # -------------------------- Auth ----------------------------------------

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

    # -------------------------- Dashboard -----------------------------------

    @app.route("/dashboard")
    def dashboard():
        if "user_id" not in session:
            flash("⛔ Du musst eingeloggt sein.")
            return redirect(url_for("login"))
        return render_template("dashboard.html")

    # -------------------------- Konten --------------------------------------

    @app.route("/konten", methods=["GET", "POST"])
    def konten():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            if request.method == "POST":
                name = request.form.get("name", "").strip()
                typ = request.form.get("typ", "").strip()
                if name and typ in {"einnahme", "ausgabe", "neutral"}:
                    try:
                        conn.execute("INSERT INTO konten (name, typ) VALUES (?, ?)", (name, typ))
                        flash(f"✅ Konto '{name}' wurde hinzugefügt.")
                        return redirect(url_for("konten"))
                    except sqlite3.IntegrityError:
                        flash("❌ Konto-Name bereits vorhanden.")
                else:
                    flash("❌ Bitte gültigen Namen und Typ angeben.")
            konten_liste = conn.execute("SELECT * FROM konten ORDER BY name").fetchall()
        return render_template("konten.html", konten=konten_liste)

    @app.route("/konten/<int:konto_id>")
    def konto_details(konto_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            konto = conn.execute("SELECT * FROM konten WHERE id = ?", (konto_id,)).fetchone()
            if not konto:
                flash("❌ Konto nicht gefunden.")
                return redirect(url_for("konten"))
            buchungen = conn.execute(
                """
                SELECT * FROM buchungen
                WHERE user_id = ? AND konto_id = ?
                ORDER BY datum DESC
                """,
                (session["user_id"], konto_id),
            ).fetchall()
        return render_template("konto_details.html", konto=konto, buchungen=buchungen)

    @app.route("/konten/<int:konto_id>/bearbeiten", methods=["GET", "POST"])
    def konto_bearbeiten(konto_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            konto = conn.execute("SELECT * FROM konten WHERE id = ?", (konto_id,)).fetchone()
            if not konto:
                flash("❌ Konto nicht gefunden.")
                return redirect(url_for("konten"))
            if request.method == "POST":
                neuer_name = request.form.get("name", "").strip()
                neuer_typ = request.form.get("typ", "").strip()
                if not neuer_name or neuer_typ not in {"einnahme", "ausgabe", "neutral"}:
                    flash("❌ Ungültige Eingabe.")
                    return redirect(url_for("konto_bearbeiten", konto_id=konto_id))
                conn.execute(
                    "UPDATE konten SET name = ?, typ = ? WHERE id = ?",
                    (neuer_name, neuer_typ, konto_id),
                )
                flash("✅ Konto wurde aktualisiert.")
                return redirect(url_for("konto_details", konto_id=konto_id))
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

    # -------------------------- Regeln --------------------------------------

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
                except sqlite3.Error as e:
                    flash(f"❌ Konnte Regel nicht speichern: {e}")
                return redirect(url_for("regeln"))
            eintraege = conn.execute(
                """
                SELECT r.id, r.suchbegriff, r.steuersatz, k.name AS konto
                FROM regeln r JOIN konten k ON r.konto_id = k.id
                ORDER BY r.id DESC
                """
            ).fetchall()
            konten_liste = conn.execute("SELECT id, name FROM konten ORDER BY name").fetchall()
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
            regel = conn.execute("SELECT * FROM regeln WHERE id = ?", (regel_id,)).fetchone()
            konten_liste = conn.execute("SELECT * FROM konten ORDER BY name").fetchall()
        return render_template("regel_bearbeiten.html", regel=regel, konten=konten_liste)

    # -------------------------- Upload & Zuordnung ---------------------------

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
                except UnicodeDecodeError:
                    flash(f"❌ Fehler beim Dekodieren (Encoding: {enc}).")
                    return redirect(url_for("upload"))

                # Delimiter automatisch erkennen (Fallback ';')
                try:
                    sample = "\n".join(content.splitlines()[:5])
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    delimiter = dialect.delimiter
                except csv.Error:
                    delimiter = ";"

                def norm(s: str) -> str:
                    s = (s or "").strip().lower()
                    repl = str.maketrans(
                        {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", " ": "", "/": "", "-": "", ".": "", ":": ""}
                    )
                    return s.translate(repl)

                # Header-Mapping
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

                # Build normalized fieldname mapping
                fields_norm = {norm(fn): fn for fn in reader.fieldnames}

                def get(row, key_alias: str) -> str:
                    # Find first matching normalized header among aliases mapping to key_alias
                    for norm_name, original in fields_norm.items():
                        if header_map.get(norm_name) == key_alias:
                            return (row.get(original) or "").strip()
                    return ""

                def parse_amount(text: str) -> float:
                    t = (text or "").strip()
                    # Handle trailing minus (e.g., 1.234,56-)
                    neg = t.endswith("-")
                    if neg:
                        t = t[:-1]
                    t = t.replace("€", "").replace(" ", "").replace(".", "").replace(",", ".")
                    try:
                        val = float(t)
                    except ValueError as exc:
                        raise ValueError(f"Betrag unlesbar: {text!r}") from exc
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
                                # überspringen, wenn essentielle Felder fehlen
                                continue

                            datum = parse_date_any(datum_raw)
                            betrag = parse_amount(betrag_raw)

                            conn.execute(
                                """
                                INSERT INTO import_transaktionen (user_id, datum, empfaenger, verwendungszweck, betrag, verarbeitet)
                                VALUES (?,?,?,?,?,0)
                                """,
                                (session["user_id"], datum, empfaenger, verwendungszweck, betrag),
                            )
                            count += 1
                        except Exception as e:  # Zeilenfehler protokollieren, aber weiter
                            print(f"⚠️ Fehler beim Verarbeiten einer Zeile: {e}")
                            continue

                if count == 0:
                    flash(
                        "ℹ️ Es konnten keine Transaktionen importiert werden. Bitte Delimiter/Spalten prüfen."
                    )
                else:
                    flash(
                        f"✅ {count} Transaktionen wurden importiert. Jetzt kannst du sie zuordnen."
                    )
                return redirect(url_for("zuordnen"))

            except Exception as e:
                # Catch-all, um 500er zu vermeiden und Feedback zu geben
                print(f"❌ Upload-Fehler: {e}")
                flash(f"❌ Unerwarteter Fehler beim Upload: {e}")
                return redirect(url_for("upload"))

        return render_template("upload.html")

    @app.route("/zuordnen", methods=["GET", "POST"])
    def zuordnen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            if request.method == "POST":
                trans_id = request.form.get("trans_id")
                konto_id = request.form.get("konto_id")
                steuersatz = request.form.get("steuersatz")
                speichere_regel = request.form.get("regel_speichern") == "1"

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

                konto = conn.execute(
                    "SELECT id, typ FROM konten WHERE id = ?",
                    (int(konto_id),),
                ).fetchone()
                if not konto:
                    flash("❌ Konto nicht gefunden.")
                    return redirect(url_for("zuordnen"))

                steuersatz_f = float(steuersatz or 0.0)
                brutto = float(trans["betrag"])  # + Einnahme / - Ausgabe

                # 1) Netto/Steuer aus Brutto ermitteln
                if steuersatz_f > 0:
                    netto = brutto / (1 + steuersatz_f / 100)
                    steuer = brutto - netto
                else:
                    netto = brutto
                    steuer = 0.0

                # 2) Kein Steuerkonto als Hauptkonto zulassen
                if konto["typ"] == "neutral":
                    flash("❌ Bitte ein Einnahme- oder Ausgabekonto auswählen (kein Steuerkonto).")
                    return redirect(url_for("zuordnen"))

                # 3) Steuerkonto (neutral) ermitteln/sicherstellen
                steuer_row = conn.execute("SELECT id FROM konten WHERE typ='neutral' LIMIT 1").fetchone()
                if not steuer_row:
                    conn.execute(
                        "INSERT INTO konten (name, typ) VALUES (?,?)",
                        ("Umsatzsteuer/Vorsteuer", "neutral"),
                    )
                    steuer_row = conn.execute("SELECT id FROM konten WHERE typ='neutral' LIMIT 1").fetchone()
                steuer_konto_id = int(steuer_row["id"]) if isinstance(steuer_row, sqlite3.Row) else int(steuer_row[0])

                # 4) Steuer-Vorzeichen bestimmen: Ausgaben -> Vorsteuer (+), Einnahmen -> USt (-)
                steuer_abs = round(abs(steuer), 2)
                steuerbetrag_steuerkonto = steuer_abs if konto["typ"] == "ausgabe" else -steuer_abs

                # 5) Hauptbuchung: nur Netto auf das gewählte Konto, Steuer = 0
                conn.execute(
                    """
                    INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz)
                    VALUES (?,?,?,?,?,?,?,?)
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
                    ),
                )

                # 6) Steuerbuchung separat aufs Steuerkonto
                if steuer_abs > 0:
                    conn.execute(
                        """
                        INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz)
                        VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            session["user_id"],
                            trans["datum"],
                            f"{'Vorsteuer' if steuerbetrag_steuerkonto > 0 else 'Umsatzsteuer'} (auto)",
                            trans["verwendungszweck"],
                            steuer_konto_id,
                            0.0,
                            steuerbetrag_steuerkonto,
                            steuersatz_f,
                        ),
                    )

                # 7) Importzeile als verarbeitet markieren
                conn.execute(
                    "UPDATE import_transaktionen SET verarbeitet = 1 WHERE id = ?",
                    (trans["id"],),
                )

                if speichere_regel:
                    conn.execute(
                        "INSERT INTO regeln (suchbegriff, konto_id, steuersatz) VALUES (?,?,?)",
                        (trans["empfaenger"], int(konto_id), steuersatz_f),
                    )
                    # auto-buchen gleicher Empfänger
                    weitere = conn.execute(
                        "SELECT * FROM import_transaktionen WHERE user_id = ? AND verarbeitet = 0 AND empfaenger = ?",
                        (session["user_id"], trans["empfaenger"]),
                    ).fetchall()
                    gebucht = 1
                    for t in weitere:
                        brutto2 = float(t["betrag"])
                        if steuersatz_f > 0:
                            netto2 = brutto2 / (1 + steuersatz_f / 100)
                            steuer2 = brutto2 - netto2
                        else:
                            netto2, steuer2 = brutto2, 0.0

                        steuer_abs2 = round(abs(steuer2), 2)
                        steuerbetrag_steuerkonto2 = (
                            steuer_abs2 if konto["typ"] == "ausgabe" else -steuer_abs2
                        )

                        # Hauptbuchung (nur Netto)
                        conn.execute(
                            """
                            INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz)
                            VALUES (?,?,?,?,?,?,?,?)
                            """,
                            (
                                session["user_id"],
                                t["datum"],
                                t["empfaenger"],
                                t["verwendungszweck"],
                                int(konto_id),
                                round(netto2, 2),
                                0.0,
                                steuersatz_f,
                            ),
                        )

                        # Steuerbuchung aufs Steuerkonto
                        if steuer_abs2 > 0:
                            conn.execute(
                                """
                                INSERT INTO buchungen (user_id, datum, empfaenger, verwendungszweck, konto_id, betrag_netto, steuerbetrag, steuersatz)
                                VALUES (?,?,?,?,?,?,?,?)
                                """,
                                (
                                    session["user_id"],
                                    t["datum"],
                                    f"{'Vorsteuer' if steuerbetrag_steuerkonto2 > 0 else 'Umsatzsteuer'} (auto)",
                                    t["verwendungszweck"],
                                    steuer_konto_id,
                                    0.0,
                                    steuerbetrag_steuerkonto2,
                                    steuersatz_f,
                                ),
                            )
                        conn.execute(
                            "UPDATE import_transaktionen SET verarbeitet = 1 WHERE id = ?",
                            (t["id"],),
                        )
                        gebucht += 1
                    flash(f"💾 Regel gespeichert. {gebucht} Transaktion(en) automatisch gebucht.")
                else:
                    flash("✅ Transaktion wurde gebucht.")

                return redirect(url_for("zuordnen"))

            # GET: unbearbeitete Transaktionen + Konten + Regelvorschlag
            transaktionen = conn.execute(
                "SELECT * FROM import_transaktionen WHERE user_id = ? AND verarbeitet = 0 ORDER BY datum DESC",
                (session["user_id"],),
            ).fetchall()
            konten_liste = conn.execute("SELECT * FROM konten ORDER BY name").fetchall()

            # Vorschläge vorbereiten (optional: im Template anzeigen)
            vorschlaege = {}
            for t in transaktionen:
                konto_id_suggest, vat_suggest = finde_vorschlag(
                    conn, t["empfaenger"], t["verwendungszweck"]
                )
                vorschlaege[t["id"]] = {
                    "konto_id": konto_id_suggest,
                    "steuersatz": vat_suggest,
                }

        return render_template(
            "zuordnen.html",
            transaktionen=transaktionen,
            konten=konten_liste,
            vorschlaege=vorschlaege,
        )

    # -------------------------- Buchungen-Übersicht --------------------------

    @app.route("/buchungen", methods=["GET"]) 
    def buchungen_liste():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT b *, k.name AS konto_name, k.typ AS konto_typ
                FROM buchungen b
                JOIN konten k ON b.konto_id = k.id
                WHERE b.user_id = ?
                ORDER BY b.datum DESC, b.id DESC
                """.replace("b *", "b.*"),
                (session["user_id"],),
            ).fetchall()
        return render_template("buchungen.html", buchungen=rows)

    # -------------------------- Debug ---------------------------------------

    @app.route("/_debug/routes")
    def _debug_routes():
        rules = "\n".join(sorted(str(r) for r in app.url_map.iter_rules()))
        return f"<pre>{rules}</pre>"

    @app.route("/debug_user")
    def debug_user():
        return f"Deine User-ID: {session.get('user_id')} | DB: {app.config['DATABASE']}"

    return app


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
