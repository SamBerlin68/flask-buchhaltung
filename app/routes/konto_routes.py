from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

import sqlite3

from datetime import date

from app.db import get_db

konto = Blueprint("konto", __name__)


@konto.route("/konten", methods=["GET", "POST"])
def konten():
    if "user_id" not in session:
        flash("⛔ Bitte zuerst einloggen.")
        return redirect(url_for("auth.login"))
    with get_db() as conn:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            typ = request.form.get("typ", "").strip()
            kontonummer = (request.form.get("kontonummer") or "").strip() or None
            if name and typ in {"einnahme", "ausgabe", "neutral"}:
                try:
                    conn.execute(
                        "INSERT INTO konten (user_id, name, typ, kontonummer) VALUES (?,?,?,?)",
                        (session["user_id"], name, typ, kontonummer),
                    )
                    flash(f"✅ Konto '{name}' wurde hinzugefügt.")
                    return redirect(url_for("konto.konten"))
                except sqlite3.IntegrityError:
                    flash("❌ Konto-Name oder -Nummer bereits vorhanden.")
            else:
                flash("❌ Bitte gültigen Namen und Typ angeben.")
        konten_liste = conn.execute(
            "SELECT * FROM konten WHERE user_id = ? ORDER BY name",
            (session["user_id"],),
        ).fetchall()
    return render_template("konten.html", konten=konten_liste)


@konto.route("/konten/<int:konto_id>")
def konto_details(konto_id: int):
    if "user_id" not in session:
        flash("⛔ Bitte zuerst einloggen.")
        return redirect(url_for("auth.login"))
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
                    AND user_id = ?
                """,
            (konto_id, session["user_id"]),
        ).fetchone()

        if not konto:
            flash("❌ Konto nicht gefunden")
            return redirect(url_for("konto.konten"))

        # Prüfen: Steuerkonto?
        ist_steuerkonto = konto["name"] == "Umsatzsteuer/Vorsteuer"

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


@konto.route("/konten/<int:konto_id>/bearbeiten", methods=["GET", "POST"])
def konto_bearbeiten(konto_id: int):
    if "user_id" not in session:
        flash("⛔ Bitte zuerst einloggen.")
        return redirect(url_for("auth.login"))
    with get_db() as conn:
        konto = conn.execute(
            "SELECT * FROM konten WHERE id = ? AND user_id = ?",
            (konto_id, session["user_id"]),
        ).fetchone()
        if not konto:
            flash("❌ Konto nicht gefunden.")
            return redirect(url_for("konto.konten"))
        if request.method == "POST":
            neuer_name = request.form.get("name", "").strip()
            neuer_typ = request.form.get("typ", "").strip()
            neue_nr = (request.form.get("kontonummer") or "").strip() or None
            if not neuer_name or neuer_typ not in {"einnahme", "ausgabe", "neutral"}:
                flash("❌ Ungültige Eingabe.")
                return redirect(url_for("konto.konto_bearbeiten", konto_id=konto_id))
            try:
                conn.execute(
                    "UPDATE konten SET name = ?, typ = ?, kontonummer = ? WHERE id = ? AND user_id = ?",
                    (neuer_name, neuer_typ, neue_nr, konto_id, session["user_id"]),
                )
                flash("✅ Konto wurde aktualisiert.")
                return redirect(url_for("konto.konto_details", konto_id=konto_id))
            except sqlite3.IntegrityError:
                flash("❌ Kontonummer bereits vergeben.")
                return redirect(url_for("konto.konto_bearbeiten", konto_id=konto_id))
    return render_template("konto_bearbeiten.html", konto=konto)


@konto.route("/konten/<int:konto_id>/loeschen", methods=["POST"])
def konto_loeschen(konto_id: int):

    if "user_id" not in session:
        flash("⛔ Bitte zuerst einloggen.")
        return redirect(url_for("auth.login"))

    with get_db() as conn:
        # Prüfen, ob noch Buchungen existieren
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM buchungen WHERE konto_id = ? AND user_id = ?",
            (konto_id, session["user_id"]),
        ).fetchone()[0]

        if count:
            flash("❌ Konto kann nicht gelöscht werden – es existieren noch Buchungen.")
            return redirect(url_for("konto.konto_details", konto_id=konto_id))

        # Konto löschen
        conn.execute(
            "DELETE FROM konten WHERE id = ? AND user_id = ?",
            (konto_id, session["user_id"]),
        )

        flash("✅ Konto wurde gelöscht.")

    return redirect(url_for("konto.konten"))
