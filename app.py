from __future__ import annotations

import os
import sqlite3
from datetime import datetime
import pandas as pd
import json

from app.db import get_db
from app.extensions import bcrypt
from app.routes.auth_routes import auth
from app.routes.debug_routes import debug
from app.routes.upload_routes import upload
from app.routes.konto_routes import konto
from app.routes.buchung_routes import buchung
from app.routes.regel_routes import regel
from app.routes.report_routes import report
from app.routes.zuordnung_routes import zuordnung
from app.routes.kunden_routes import kunden
from app.routes.crm_routes import crm

# später absichern


from flask import (
    Flask,
    redirect,
    session,
    url_for,
    flash,
)

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
        print(
            "⚠️ Warnung: FLASK_SECRET_KEY nicht gesetzt – nutze unsicheren Dev-Default."
        )
    app.config["DATABASE"] = os.getenv("DB_PATH", os.path.abspath("users.db"))

    bcrypt.init_app(app)
    app.register_blueprint(auth)

    @app.template_filter("euro")
    def format_euro(value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return "0,00 €"

        formatted = (
            f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            + " €"
        )

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

    def init_db() -> None:
        with get_db() as conn:
            # Debug-Ausgabe der SQLs (auskommentieren, wenn zu laut)
            # conn.set_trace_callback(print)

            # users
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL
                )
                """)

            # konten – inkl. kontonummer (DATEV) und optionalem Standard-Steuersatz
            conn.execute("""
                CREATE TABLE IF NOT EXISTS konten (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    typ  TEXT NOT NULL CHECK (typ IN ('einnahme','ausgabe','neutral')),
                    kontonummer TEXT,
                    standard_steuersatz REAL
                )
                """)
            # Unique-Index auf kontonummer (nur wenn gesetzt)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ux_konten_kontonummer
                ON konten(kontonummer) WHERE kontonummer IS NOT NULL
                """)
            # anfragen
            conn.execute("""
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
                """)
            # kontakte
            conn.execute("""
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
                """)
            # regeln
            conn.execute("""
                CREATE TABLE IF NOT EXISTS regeln (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    suchbegriff TEXT NOT NULL,
                    konto_id INTEGER NOT NULL,
                    steuersatz REAL NOT NULL DEFAULT 19.0,
                    FOREIGN KEY (konto_id) REFERENCES konten(id)
                )
                """)

            # Importtabelle (Rohbank)
            conn.execute("""
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
                """)

            # Buchungen – Netto + Steuer (separat)
            conn.execute("""
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
                """)

            # --- Migration: Spalte 'belegnummer' nachziehen (falls noch nicht vorhanden)
            # --- Migrationen für alte DBs: fehlende Spalten in "buchungen" nachziehen
            cols = [
                r["name"]
                for r in conn.execute("PRAGMA table_info(buchungen)").fetchall()
            ]

            if "betrag_netto" not in cols:
                conn.execute(
                    "ALTER TABLE buchungen ADD COLUMN betrag_netto REAL NOT NULL DEFAULT 0.0"
                )

            if "steuerbetrag" not in cols:
                conn.execute(
                    "ALTER TABLE buchungen ADD COLUMN steuerbetrag REAL NOT NULL DEFAULT 0.0"
                )

            if "steuersatz" not in cols:
                conn.execute(
                    "ALTER TABLE buchungen ADD COLUMN steuersatz REAL NOT NULL DEFAULT 0.0"
                )

            if "created_at" not in cols:
                conn.execute(
                    "ALTER TABLE buchungen ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))"
                )

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

                conn.execute(
                    """
                    INSERT INTO kontakte (
                        name,
                        email,
                        telefon,
                        ort,
                        status,
                        raw_data
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                 """,
                    (
                        str(row.get("Location", "")).strip(),
                        str(row.get("Email", "")).strip(),
                        str(row.get("Telefon", "")).strip(),
                        str(row.get("Ort", "")).strip(),
                        str(row.get("Stand", "neu")).strip(),
                        json.dumps(raw, ensure_ascii=False),
                    ),
                )

        print("✅ 1:1 Import abgeschlossen")

    @app.route("/import_kontakte")
    def import_kontakte_route():
        # import_kontakte()
        return "Import deakteviert"

    # DB initialisieren
    init_db()

    # -------------------------- Debug / Tools -----------------------------------

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

    from app.routes.main_routes import main

    app.register_blueprint(main)
    app.register_blueprint(debug)
    app.register_blueprint(upload)
    app.register_blueprint(konto)
    app.register_blueprint(buchung)
    app.register_blueprint(regel)
    app.register_blueprint(report)
    app.register_blueprint(zuordnung)
    app.register_blueprint(kunden)
    app.register_blueprint(crm)
    return app


# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = create_app()
    print("🚀 Starte Flask auf http://127.0.0.1:5000 ...")
    app.run(debug=True)
