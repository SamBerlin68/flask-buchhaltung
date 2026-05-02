# app.py
import os
import io
import csv
import re
import sqlite3
from datetime import datetime
from datetime import timedelta



from flask import (
    Flask,
    request,
    session,
    redirect,
    url_for,
    render_template,
    render_template_string,
    flash,
    g,
    jsonify,
)
from werkzeug.exceptions import BadRequest
from jinja2 import TemplateNotFound

# -----------------------------------------------------------------------------
# DB
# -----------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(os.getenv("DB_PATH", os.path.abspath("users.db")))
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
EU_AMOUNT = re.compile(r"\s*([+-]?)\s*([0-9.\s]*[0-9]),([0-9]{2})\s*€?\s*$")


def parse_eu_amount(text: str) -> float:
    """EU-Zahlenformat robust parsen, inkl. (1.234,56), -1.234,56, 1.234,56-"""
    if text is None:
        raise ValueError("Leerer Betrag")
    t = text.strip()

    paren_neg = t.startswith("(") and t.endswith(")")
    if paren_neg:
        t = t[1:-1].strip()

    trailing_neg = t.endswith("-")
    if trailing_neg:
        t = t[:-1].strip()

    lead_neg = t.startswith("-")
    if lead_neg or t.startswith("+"):
        t = t[1:].strip()

    t = t.replace("€", "").replace(" ", "")

    m = EU_AMOUNT.fullmatch(t.replace(".", ""))
    if m:
        _, intpart, cents = m.groups()
        val = float(f"{intpart}.{cents}")
    else:
        t2 = t.replace(".", "").replace(",", ".")
        val = float(t2)

    neg = paren_neg or trailing_neg or lead_neg
    return -val if neg else val


def parse_date(d: str) -> str:
    """Akzeptiert 31.12.2024, 31.12.24, 2024-12-31 (+ Varianten mit Uhrzeit) → YYYY-MM-DD"""
    if d is None or not str(d).strip():
        raise ValueError("Leeres Datum")
    s = str(d).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Ungültiges Datum: {d!r}")

# -----------------------------------------------------------------------------
# App factory
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
    SESSION_COOKIE_NAME="flask_session",
    SESSION_COOKIE_SAMESITE="Lax",   # lokal reicht Lax
    SESSION_COOKIE_SECURE=False,     # lokal nur HTTP
    SESSION_COOKIE_HTTPONLY=True,
)
    app.permanent_session_lifetime = timedelta(days=7)

    @app.before_request
    def _make_session_permanent():
        session.permanent = True

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret")
    app.config["DATABASE"] = os.getenv("DB_PATH", os.path.abspath("users.db"))
    app.config["PROPAGATE_EXCEPTIONS"] = True
    app.teardown_appcontext(close_db)

    # ---------------- DB init ----------------
    def init_db():
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE,
                    password_hash TEXT
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS konten(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    typ  TEXT NOT NULL,          -- 'einnahme'|'ausgabe'|'neutral'
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS import_transaktionen(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    datum TEXT NOT NULL,
                    empfaenger TEXT NOT NULL,
                    verwendungszweck TEXT,
                    betrag REAL NOT NULL,
                    verarbeitet INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )""")
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_import_unique
                ON import_transaktionen(user_id, datum, empfaenger, betrag, IFNULL(verwendungszweck,''))
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS buchungen(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    konto_id INTEGER NOT NULL,
                    datum TEXT NOT NULL,
                    empfaenger TEXT,
                    verwendungszweck TEXT,
                    betrag_netto REAL NOT NULL,
                    steuerbetrag REAL NOT NULL,
                    steuersatz REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (konto_id) REFERENCES konten(id)
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS regeln(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suchbegriff TEXT NOT NULL,
                    konto_id INTEGER NOT NULL,
                    steuersatz REAL NOT NULL,
                    UNIQUE(suchbegriff, konto_id, steuersatz)
                )""")

    if not os.path.exists(app.config["DATABASE"]):
        db_dir = os.path.dirname(app.config["DATABASE"])
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with app.app_context():
            init_db()

    # ---------------- Dashboard / Favicon ----------------
    @app.route("/dashboard")
    def dashboard():
     if "user_id" not in session:
        return redirect(url_for("login"))
    try:
        return render_template("dashboard.html")
    except TemplateNotFound:
        return render_template_string("""
        {% extends "base.html" %}
        {% block content %}
        <div class="container py-4">
          <h1 class="mb-4">Dashboard</h1>
          <div class="row g-3">
            <div class="col-12 col-md-6 col-lg-3">
              <a class="btn btn-primary w-100" href="{{ url_for('konten') }}">Kontoverwaltung</a>
            </div>
            <div class="col-12 col-md-6 col-lg-3">
              <a class="btn btn-secondary w-100" href="{{ url_for('buchungen_liste') }}">Buchungen</a>
            </div>
            <div class="col-12 col-md-6 col-lg-3">
              <a class="btn btn-outline-primary w-100" href="{{ url_for('zuordnen') }}">Zuordnung</a>
            </div>
            <div class="col-12 col-md-6 col-lg-3">
              <form method="post" action="{{ url_for('reset_all') }}"
                    onsubmit="return confirm('⚠️ Wirklich alle Daten löschen?');">
                <button class="btn btn-danger w-100" type="submit">Reset (alle Daten löschen)</button>
              </form>
            </div>
          </div>
        </div>
        {% endblock %}
        """)


    @app.route("/favicon.ico")
    def favicon():
        return redirect(url_for("static", filename="favicon.ico"))

    # ---------------- Auth (demo) ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            session["user_id"] = 1
            flash("✅ Eingeloggt (Demo).")
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("👋 Abgemeldet.")
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        if "user_id" in session:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    # ---------------- Upload ----------------
    @app.route("/upload", methods=["GET", "POST"])
    def upload():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        if request.method == "POST":
            f = request.files.get("file")
            if not f:
                flash("❌ Keine Datei ausgewählt.")
                return redirect(url_for("upload"))
            text = f.read().decode("utf-8", errors="replace")

            header_map = {
                "buchungsdatum":"date","datum":"date","wertstellung":"date","buchungstag":"date","belegdatum":"date","valuta":"date",
                "empfaenger":"payee","empfänger":"payee","beguenstigter":"payee","begünstigter":"payee","nameauftraggeber/zahlungspflichtiger":"payee",
                "auftraggeber/name":"payee","name":"payee",
                "verwendungszweck":"purpose","verwendungszweckprimanota":"purpose","verwendungszweck1":"purpose","verwendungszweck2":"purpose",
                "buchungstext":"purpose","beschreibung":"purpose",
                "betrag":"amount","umsatz":"amount","betrag(eur)":"amount","soll/haben":"amount","betraginklammernnegativ":"amount",
            }

            sniffer = csv.Sniffer()
            sample = "\n".join(text.splitlines()[:5])
            try:
                dialect = sniffer.sniff(sample)
            except Exception:
                dialect = csv.excel
                dialect.delimiter = ";"

            reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            inserted = 0
            with get_db() as conn:
                for row in reader:
                    norm = {}
                    for k, v in row.items():
                        key = header_map.get((k or "").strip().lower(), (k or "").strip().lower())
                        norm[key] = v
                    try:
                        datum = parse_date(norm.get("date") or norm.get("datum") or norm.get("wertstellung"))
                        empfaenger = (norm.get("payee") or "").strip() or "Unbekannt"
                        verwendungszweck = (norm.get("purpose") or "").strip() or None
                        betrag = parse_eu_amount(norm.get("amount") or "")
                    except Exception as e:
                        flash(f"⚠️ Import-Zeile übersprungen: {e}")
                        continue

                    cur = conn.execute("""
                        INSERT OR IGNORE INTO import_transaktionen
                        (user_id, datum, empfaenger, verwendungszweck, betrag, verarbeitet)
                        VALUES (?,?,?,?,?,0)
                    """, (session["user_id"], datum, empfaenger, verwendungszweck, betrag))
                    if cur.rowcount:
                        inserted += 1

            flash(f"✅ Import abgeschlossen. {inserted} neue Buchungen eingelesen.")
            return redirect(url_for("zuordnen"))

        return render_template("upload.html")

    # ---------------- Zuordnen/Buchen ----------------
    @app.route("/zuordnen", methods=["GET", "POST"])
    def zuordnen():
        import logging, traceback
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            if request.method == "POST":
                try:
                    trans_id = request.form.get("trans_id") or request.form.get("id") or request.form.get("import_id")
                    konto_id = request.form.get("konto_id") or request.form.get("konto")
                    steuersatz = (request.form.get("steuersatz") or request.form.get("mwst") or
                                  request.form.get("ust") or request.form.get("tax"))
                    speichere_regel = (request.form.get("regel_speichern") == "1" or request.form.get("save_rule") == "1")

                    def _require_int(name: str, val) -> int:
                        s = "" if val is None else str(val).strip()
                        if not s: raise BadRequest(f"{name} fehlt im Formular")
                        if not re.fullmatch(r"-?\d+", s): raise BadRequest(f"{name} ist keine gültige Zahl: {val!r}")
                        return int(s)

                    trans_id = _require_int("trans_id", trans_id)
                    konto_id = _require_int("konto_id", konto_id)

                    def _parse_steuersatz(val) -> float:
                        if val is None or str(val).strip() == "": return 0.0
                        v = float(str(val).strip().replace(",", "."))
                        if v < 0 or v > 100: raise BadRequest(f"Steuersatz außerhalb des erlaubten Bereichs (0–100): {v}")
                        return v

                    steuersatz_val = _parse_steuersatz(steuersatz)

                    trans = conn.execute(
                        "SELECT * FROM import_transaktionen WHERE id = ? AND user_id = ? AND verarbeitet = 0",
                        (trans_id, session["user_id"])
                    ).fetchone()
                    if trans is None:
                        raise BadRequest("Transaktion nicht gefunden (evtl. schon verbucht?)")

                    konto = conn.execute(
                        "SELECT * FROM konten WHERE id = ? AND user_id = ?",
                        (konto_id, session["user_id"])
                    ).fetchone()
                    if konto is None:
                        raise BadRequest("Konto nicht gefunden")

                    try:
                        konto_typ = dict(konto).get("typ")
                    except Exception:
                        konto_typ = konto["typ"]
                    if konto_typ == "neutral":
                        flash("❌ Bitte ein Einnahme- oder Ausgabekonto auswählen (kein Steuerkonto).")
                        return redirect(url_for("zuordnen"))

                    brutto = float(trans["betrag"])
                    rate = steuersatz_val / 100.0
                    if rate > 0:
                        betrag_netto = round(brutto / (1.0 + rate), 2)
                        steuerbetrag = round(brutto - betrag_netto, 2)
                    else:
                        betrag_netto = brutto
                        steuerbetrag = 0.0

                    conn.execute("""
                        INSERT INTO buchungen
                          (user_id, konto_id, datum, empfaenger, verwendungszweck,
                           betrag_netto, steuerbetrag, steuersatz)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (session["user_id"], konto["id"], trans["datum"], trans["empfaenger"],
                          trans["verwendungszweck"], betrag_netto, steuerbetrag, steuersatz_val))

                    conn.execute("UPDATE import_transaktionen SET verarbeitet = 1 WHERE id = ?", (trans["id"],))

                    if speichere_regel:
                        conn.execute(
                            "INSERT OR IGNORE INTO regeln (suchbegriff, konto_id, steuersatz) VALUES (?,?,?)",
                            (trans["empfaenger"], konto["id"], steuersatz_val)
                        )

                    conn.commit()
                    flash("✅ Buchung erfasst.")
                    return redirect(url_for("zuordnen"))

                except Exception as e:
                    logging.exception("Buchen fehlgeschlagen")
                    flash(f"❌ Fehler beim Buchen: {e}")
                    import traceback
                    flash("<pre>" + traceback.format_exc() + "</pre>")
                    return redirect(url_for("zuordnen"))

            rows = conn.execute("""
                SELECT * FROM import_transaktionen
                WHERE user_id = ? AND verarbeitet = 0
                ORDER BY datum ASC, id ASC
            """, (session["user_id"],)).fetchall()

        return render_template("zuordnen.html", transaktionen=rows)

    @app.route("/buchen", methods=["POST"])
    def buchen():
        return zuordnen()

    # ---------------- Kontoverwaltung ----------------
    @app.route("/konten", methods=["GET", "POST"])
    def konten():
        if "user_id" not in session:
            return redirect(url_for("login"))
        uid = session["user_id"]

        with get_db() as conn:
            if request.method == "POST":
                name = (request.form.get("name") or "").strip()
                typ = (request.form.get("typ") or "").strip().lower()
                if not name or typ not in ("einnahme", "ausgabe", "neutral"):
                    flash("❌ Bitte Namen angeben und Typ aus {einnahme, ausgabe, neutral} wählen.")
                    return redirect(url_for("konten"))

                conn.execute(
                    "INSERT INTO konten (user_id, name, typ) VALUES (?,?,?)",
                    (uid, name, typ)
                )
                conn.commit()
                flash("✅ Konto angelegt.")
                return redirect(url_for("konten"))

            rows = conn.execute(
                "SELECT id, name, typ FROM konten WHERE user_id = ? ORDER BY name ASC, id ASC",
                (uid,)
            ).fetchall()

        try:
            return render_template("konten.html", konten=rows)
        except TemplateNotFound:
            return render_template_string("""
            {% extends "base.html" %}
            {% block content %}
            <div class="container py-4">
              <div class="d-flex justify-content-between align-items-center mb-3">
                <h1>Kontoverwaltung</h1>
                <a class="btn btn-outline-secondary" href="{{ url_for('dashboard') }}">Zurück zum Dashboard</a>
              </div>

              <form method="post" class="row gy-2 gx-2 align-items-end mb-4">
                <div class="col-12 col-md-5">
                  <label class="form-label">Name</label>
                  <input class="form-control" name="name" required>
                </div>
                <div class="col-12 col-md-4">
                  <label class="form-label">Typ</label>
                  <select class="form-select" name="typ" required>
                    <option value="einnahme">Einnahme</option>
                    <option value="ausgabe">Ausgabe</option>
                    <option value="neutral">Neutral</option>
                  </select>
                </div>
                <div class="col-12 col-md-3">
                  <button class="btn btn-primary w-100" type="submit">Konto anlegen</button>
                </div>
              </form>

              <div class="table-responsive">
                <table class="table table-sm align-middle">
                  <thead><tr><th>ID</th><th>Name</th><th>Typ</th><th class="text-end">Aktion</th></tr></thead>
                  <tbody>
                    {% for k in konten %}
                    <tr>
                      <td>{{ k.id }}</td>
                      <td>{{ k.name }}</td>
                      <td>{{ k.typ }}</td>
                      <td class="text-end">
                        <form method="post" action="{{ url_for('konto_loeschen', konto_id=k.id) }}"
                              onsubmit="return confirm('Konto wirklich löschen? (Buchungen dieses Kontos werden entfernt)');">
                          <button class="btn btn-sm btn-outline-danger">Löschen</button>
                        </form>
                      </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" class="text-center text-muted">Noch keine Konten.</td></tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
            </div>
            {% endblock %}
            """, konten=rows)

    @app.route("/konten/<int:konto_id>/loeschen", methods=["POST"])
    def konto_loeschen(konto_id):
        if "user_id" not in session:
            return redirect(url_for("login"))
        uid = session["user_id"]
        with get_db() as conn:
            # sicherheitshalber verbundene Buchungen dieses Kontos mitlöschen
            conn.execute("DELETE FROM buchungen WHERE user_id = ? AND konto_id = ?", (uid, konto_id))
            conn.execute("DELETE FROM konten WHERE user_id = ? AND id = ?", (uid, konto_id))
            conn.commit()
        flash("🗑️ Konto (und zugehörige Buchungen) gelöscht.")
        return redirect(url_for("konten"))

    # ---------------- Regeln (LISTE & LÖSCHEN) ----------------
    @app.route("/regeln", methods=["GET"])
    def regeln():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))

        with get_db() as conn:
            rows = conn.execute("""
                SELECT id, suchbegriff, konto_id, steuersatz
                FROM regeln
                ORDER BY suchbegriff ASC, id ASC
            """).fetchall()

        try:
            return render_template("regeln.html", regeln=rows)
        except TemplateNotFound:
            return render_template_string("""
                {% extends "base.html" %}
                {% block content %}
                <div class="container mt-4">
                  <h1>Regeln</h1>
                  {% if regeln %}
                    <table class="table table-sm">
                      <thead>
                        <tr><th>ID</th><th>Suchbegriff</th><th>Konto-ID</th><th>Steuersatz</th><th></th></tr>
                      </thead>
                      <tbody>
                        {% for r in regeln %}
                        <tr>
                          <td>{{ r.id }}</td>
                          <td>{{ r.suchbegriff }}</td>
                          <td>{{ r.konto_id }}</td>
                          <td>{{ "%.2f"|format(r.steuersatz) }}</td>
                          <td>
                            <form method="post" action="{{ url_for('regel_loeschen', regel_id=r.id) }}" onsubmit="return confirm('Wirklich löschen?')">
                              <button class="btn btn-sm btn-outline-danger">Löschen</button>
                            </form>
                          </td>
                        </tr>
                        {% endfor %}
                      </tbody>
                    </table>
                  {% else %}
                    <p>Keine Regeln vorhanden.</p>
                  {% endif %}
                </div>
                {% endblock %}
            """, regeln=rows)

    @app.route("/regeln/<int:regel_id>/loeschen", methods=["POST"])
    def regel_loeschen(regel_id):
        if "user_id" not in session:
            return redirect(url_for("login"))
        with get_db() as conn:
            conn.execute("DELETE FROM regeln WHERE id = ?", (regel_id,))
            conn.commit()
        flash("🗑️ Regel gelöscht.")
        return redirect(url_for("regeln"))

    # ---------------- Reset (alle Daten löschen) ----------------
    @app.route("/reset", methods=["POST"])
    def reset_all():
        if "user_id" not in session:
            return redirect(url_for("login"))
        uid = session["user_id"]
        with get_db() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM buchungen WHERE user_id = ?", (uid,))
            try:
                conn.execute("DELETE FROM regeln")  # global; anpassen, falls regeln user-spezifisch werden
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM import_transaktionen WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM konten WHERE user_id = ?", (uid,))
            conn.commit()
        flash("🧨 Alle Daten des Benutzers wurden gelöscht.")
        return redirect(url_for("dashboard"))

    # ---------------- Buchungen & Export ----------------
    @app.route("/buchungen", methods=["GET"])
    def buchungen_liste():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            rows = conn.execute("""
                SELECT b.*, k.name AS konto_name, k.typ AS konto_typ
                FROM buchungen b
                JOIN konten k ON b.konto_id = k.id
                WHERE b.user_id = ?
                ORDER BY b.datum DESC, b.id DESC
            """, (session["user_id"],)).fetchall()
        return render_template("buchungen.html", buchungen=rows)

    @app.route("/buchungen.csv", methods=["GET"])
    def buchungen_export_csv():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("login"))
        with get_db() as conn:
            rows = conn.execute("""
                SELECT
                    b.datum, b.empfaenger, b.verwendungszweck,
                    k.name AS konto, k.typ AS konto_typ,
                    b.betrag_netto, b.steuerbetrag, b.steuersatz
                FROM buchungen b
                JOIN konten k ON b.konto_id = k.id
                WHERE b.user_id = ?
                ORDER BY b.datum ASC, b.id ASC
            """, (session["user_id"],)).fetchall()
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow(["Datum","Empfänger","Verwendungszweck","Konto","Konto-Typ","Betrag Netto","Steuerbetrag","Steuersatz"])
        for r in rows:
            w.writerow([
                r["datum"], r["empfaenger"] or "", r["verwendungszweck"] or "",
                r["konto"], r["konto_typ"],
                f"{r['betrag_netto']:.2f}".replace(".", ","),
                f"{r['steuerbetrag']:.2f}".replace(".", ","),
                f"{r['steuersatz']:.2f}".replace(".", ","),
            ])
        resp = app.response_class(out.getvalue(), mimetype="text/csv; charset=utf-8")
        resp.headers["Content-Disposition"] = "attachment; filename=buchungen.csv"
        return resp

    # ---------------- Register (Stub) ----------------
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            flash("Registrierung ist in dieser Demo deaktiviert. Bitte logge dich ein.")
            return redirect(url_for("login"))
        try:
            return render_template("register.html")
        except TemplateNotFound:
            flash("Registrierung ist noch nicht eingerichtet.")
            return redirect(url_for("login"))

    # (optional) Übersicht im Terminal
    # print("Routen registriert:")
    # for r in app.url_map.iter_rules():
    #     print(" -", r.endpoint, "->", r.rule)

    @app.route("/set_test")
    def set_test():
        session["test_cookie"] = "ok"
    return "set_test: gesetzt. Weiter zu /get_test."

    @app.route("/get_test")
    def get_test():
     return f"get_test: session.get('test_cookie') = {session.get('test_cookie')!r}"


    return app

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False, threaded=True)
