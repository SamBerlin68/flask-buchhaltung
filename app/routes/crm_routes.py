from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    request,
)
from app.db import get_db

crm = Blueprint("crm", __name__)


@crm.route("/crm")
def dashboard():
    with get_db() as conn:
        kontakte = conn.execute("SELECT COUNT(*) FROM kontakte").fetchone()[0]

        kunden = conn.execute(
            "SELECT COUNT(*) FROM kontakte WHERE ist_kunde = 1"
        ).fetchone()[0]

    return render_template("crm_dashboard.html", kontakte=kontakte, kunden=kunden)


@crm.route("/crm/newsletter")
def newsletter_liste():
    from flask import request

    search = request.args.get("search", "")

    with get_db() as conn:
        if search:
            rows = conn.execute(
                """
                SELECT * FROM newsletter_kontakte
                WHERE name LIKE ?
                   OR email LIKE ?
                   OR ort LIKE ?
                ORDER BY name
            """,
                (f"%{search}%", f"%{search}%", f"%{search}%"),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM newsletter_kontakte ORDER BY name"
            ).fetchall()

    return render_template("newsletter_liste.html", kontakte=rows, search=search)


@crm.route("/crm/newsletter/<int:kontakt_id>/bearbeiten", methods=["GET", "POST"])
def newsletter_bearbeiten(kontakt_id):
    with get_db() as conn:

        if request.method == "POST":
            conn.execute(
                """
                UPDATE newsletter_kontakte
                SET name = ?, email = ?, telefon = ?, ort = ?
                WHERE id = ?
            """,
                (
                    request.form.get("name"),
                    request.form.get("email"),
                    request.form.get("telefon"),
                    request.form.get("ort"),
                    kontakt_id,
                ),
            )
            conn.commit()

            return redirect(url_for("crm.newsletter_liste"))

        kontakt = conn.execute(
            "SELECT * FROM newsletter_kontakte WHERE id = ?", (kontakt_id,)
        ).fetchone()

    return render_template("newsletter_bearbeiten.html", kontakt=kontakt)


@crm.route("/crm/newsletter/<int:kontakt_id>/loeschen")
def newsletter_loeschen(kontakt_id):
    with get_db() as conn:
        conn.execute("DELETE FROM newsletter_kontakte WHERE id = ?", (kontakt_id,))

        # optional: Logs mitlöschen
        conn.execute("DELETE FROM newsletter_logs WHERE kontakt_id = ?", (kontakt_id,))

        conn.commit()

    return redirect(url_for("crm.newsletter_liste"))


@crm.route("/crm/newsletter/neu", methods=["GET", "POST"])
def newsletter_neu():
    errors = []

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        telefon = request.form.get("telefon", "").strip()
        ort = request.form.get("ort", "").strip()

        # 🔍 VALIDIERUNG
        if not email:
            errors.append("Email ist erforderlich.")

        elif "@" not in email:
            errors.append("Email ist ungültig.")

        if not name:
            errors.append("Name darf nicht leer sein.")

        # 👉 nur speichern wenn keine Fehler
        if not errors:
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO newsletter_kontakte (name, email, telefon, ort)
                    VALUES (?, ?, ?, ?)
                """,
                    (name, email, telefon, ort),
                )
                conn.commit()

            return redirect(url_for("crm.newsletter_liste"))

    return render_template("newsletter_neu.html", errors=errors)
