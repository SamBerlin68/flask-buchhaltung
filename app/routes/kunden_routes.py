from flask import (
    Blueprint,
    render_template,
    session,
    redirect,
    url_for,
    flash,
    request,
)
from app.db import get_db
from datetime import date, datetime, timedelta

kunden = Blueprint("kunden", __name__)


@kunden.route("/kunden")
def kunden_liste():

    if "user_id" not in session:
        flash("⛔ Bitte einloggen.")
        return redirect(url_for("auth.login"))

    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "name")
    filter_typ = request.args.get("filter", "alle")
    seite = request.args.get("seite", 1, type=int)

    order_map = {
        "name": "name",
        "ort": "ort",
        "status": "status",
        "aufgabe": "aufgabe",
        "faellig": "faellig_am",
    }

    order_by = order_map.get(sort, "name")

    with get_db() as conn:

        sql = """
            SELECT
                id,
                name,
                ort,
                email,
                telefon,
                ansprechpartner,
                funktion,
                interesse,
                status,
                aufgabe,
                aufgabe_erledigt,
                faellig_am,
                notizen
            FROM kontakte
            WHERE user_id = ?
        """

        params = [session["user_id"]]

        if q:
            sql += """
                AND (
                    name LIKE ?
                    OR ort LIKE ?
                    OR ansprechpartner LIKE ?
                    OR funktion LIKE ?
                    OR interesse LIKE ?
                    OR aufgabe LIKE ?
                    OR notizen LIKE ?
                )
            """

            suchwert = f"%{q}%"
            params.extend([suchwert] * 7)

        sql += f" ORDER BY {order_by} COLLATE NOCASE"

        kontakte = conn.execute(sql, params).fetchall()

    heute = date.today()

    kontakte_liste = []
    gesamt_kontakte = len(kontakte)
    gesamt_offene = 0
    gesamt_ueberfaellig = 0

    for k in kontakte:
        k = dict(k)
        k["faellig_status"] = "none"

        if k.get("aufgabe") and not k.get("aufgabe_erledigt"):
            gesamt_offene += 1

        if k.get("faellig_am"):
            try:
                d = datetime.strptime(k["faellig_am"][:10], "%Y-%m-%d").date()

                if d < heute and not k.get("aufgabe_erledigt"):
                    k["faellig_status"] = "ueberfaellig"
                    gesamt_ueberfaellig += 1

                elif d <= heute + timedelta(days=7):
                    k["faellig_status"] = "diese_woche"

                else:
                    k["faellig_status"] = "spaeter"

            except ValueError:
                pass

        if filter_typ == "ueberfaellig":
            if k["faellig_status"] != "ueberfaellig":
                continue

        elif filter_typ == "offen":
            if not k.get("aufgabe") or k.get("aufgabe_erledigt"):
                continue

        kontakte_liste.append(k)

    # Pagination
    pro_seite = 12
    gesamt = len(kontakte_liste)
    seiten = max(1, (gesamt + pro_seite - 1) // pro_seite)

    start_idx = (seite - 1) * pro_seite
    kontakte_liste = kontakte_liste[start_idx : start_idx + pro_seite]

    return render_template(
        "kunden_liste.html",
        kontakte=kontakte_liste,
        ueberfaellig=gesamt_ueberfaellig,
        offene_aufgaben=gesamt_offene,
        gesamt_kontakte=gesamt_kontakte,
        filter_typ=filter_typ,
        seite=seite,
        seiten=seiten,
    )


@kunden.route("/kunden/<int:kunden_id>/bearbeiten", methods=["GET", "POST"])
def kunden_bearbeiten(kunden_id):

    if "user_id" not in session:
        flash("⛔ Bitte einloggen.")
        return redirect(url_for("auth.login"))

    with get_db() as conn:

        kontakt = conn.execute(
            """
            SELECT *
            FROM kontakte
            WHERE id = ?
              AND user_id = ?
            """,
            (
                kunden_id,
                session["user_id"],
            ),
        ).fetchone()

        if not kontakt:
            flash("❌ Kontakt nicht gefunden.")
            return redirect(url_for("kunden.kunden_liste"))

        if request.method == "POST":

            conn.execute(
                """
                UPDATE kontakte
                SET
                    name = ?,
                    email = ?,
                    telefon = ?,
                    ort = ?,
                    plz = ?,
                    strasse = ?,
                    ansprechpartner = ?,
                    funktion = ?,
                    location = ?,
                    plaetze = ?,
                    besonderheiten = ?,
                    status = ?,
                    aufgabe = ?,
                    interesse = ?,
                    vertrag = ?,
                    notizen = ?
                WHERE id = ?
                  AND user_id = ?
                """,
                (
                    request.form.get("name"),
                    request.form.get("email"),
                    request.form.get("telefon"),
                    request.form.get("ort"),
                    request.form.get("plz"),
                    request.form.get("strasse"),
                    request.form.get("ansprechpartner"),
                    request.form.get("funktion"),
                    request.form.get("location"),
                    request.form.get("plaetze"),
                    request.form.get("besonderheiten"),
                    request.form.get("status"),
                    request.form.get("aufgabe"),
                    request.form.get("interesse"),
                    request.form.get("vertrag"),
                    request.form.get("notizen"),
                    kunden_id,
                    session["user_id"],
                ),
            )

            conn.commit()

            flash("✅ Kontakt gespeichert.")

            return redirect(url_for("kunden.kunden_liste"))

    return render_template(
        "kunden_bearbeiten.html",
        kontakt=kontakt,
    )


@kunden.route("/kunden/neu", methods=["GET", "POST"])
def kunden_neu():

    if "user_id" not in session:
        flash("⛔ Bitte einloggen.")
        return redirect(url_for("auth.login"))

    if request.method == "POST":

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO kontakte (
                    name, email, telefon, ort,
                    plz, strasse, ansprechpartner, funktion,
                    location, plaetze, besonderheiten,
                    status, aufgabe, interesse, vertrag, notizen,
                    user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    request.form.get("name"),
                    request.form.get("email"),
                    request.form.get("telefon"),
                    request.form.get("ort"),
                    request.form.get("plz"),
                    request.form.get("strasse"),
                    request.form.get("ansprechpartner"),
                    request.form.get("funktion"),
                    request.form.get("location"),
                    request.form.get("plaetze"),
                    request.form.get("besonderheiten"),
                    request.form.get("status"),
                    request.form.get("aufgabe"),
                    request.form.get("interesse"),
                    request.form.get("vertrag"),
                    request.form.get("notizen"),
                    session["user_id"],
                ),
            )

            conn.commit()

        flash("✅ Neuer Kontakt angelegt.")
        return redirect(url_for("kunden.kunden_liste"))

    # 👉 DAS ist entscheidend
    return render_template("kunden_bearbeiten.html", kontakt={})
