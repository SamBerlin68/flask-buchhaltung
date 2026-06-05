from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    request,
    session,
    flash,
)
from app.db import get_db

crm = Blueprint("crm", __name__)


@crm.route("/crm")
def dashboard():
    if "user_id" not in session:
        flash("⛔ Bitte einloggen.")
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]

    with get_db() as conn:
        kontakte = conn.execute(
            """
        SELECT COUNT(*)
        FROM kontakte
        WHERE user_id = ?
        """,
            (user_id,),
        ).fetchone()[0]

        kunden = conn.execute(
            """
            SELECT COUNT(*)
            FROM kontakte
            WHERE user_id = ?
            AND ist_kunde = 1
            """,
            (user_id,),
        ).fetchone()[0]

        interessenten = conn.execute(
            """
            SELECT COUNT(*)
            FROM kontakte
            WHERE user_id = ?
              AND ist_kunde = 0
            """,
            (user_id,),
        ).fetchone()[0]

        offene_aufgaben = conn.execute(
            """
            SELECT COUNT(*)
            FROM kontakte
            WHERE user_id = ?
              AND aufgabe IS NOT NULL
              AND aufgabe != ''
              AND COALESCE(aufgabe_erledigt,0) = 0
            """,
            (user_id,),
        ).fetchone()[0]

        ueberfaellige_aufgaben = conn.execute(
            """
            SELECT COUNT(*)
            FROM kontakte
            WHERE user_id = ?
              AND faellig_am IS NOT NULL
              AND faellig_am != ''
              AND faellig_am < DATE('now')
              AND aufgabe IS NOT NULL
              AND aufgabe != ''
              AND COALESCE(aufgabe_erledigt,0) = 0
            """,
            (user_id,),
        ).fetchone()[0]

        naechste_aufgaben = conn.execute(
            """
            SELECT
                id,
                name,
                aufgabe,
                faellig_am
            FROM kontakte
            WHERE user_id = ?
              AND aufgabe IS NOT NULL
              AND aufgabe != ''
              AND COALESCE(aufgabe_erledigt,0) = 0
            ORDER BY faellig_am ASC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()

    return render_template(
        "crm_dashboard.html",
        kontakte=kontakte,
        kunden=kunden,
        interessenten=interessenten,
        offene_aufgaben=offene_aufgaben,
        ueberfaellige_aufgaben=ueberfaellige_aufgaben,
        naechste_aufgaben=naechste_aufgaben,
    )


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
