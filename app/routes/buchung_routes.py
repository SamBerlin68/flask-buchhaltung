from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from app.db import get_db

buchung = Blueprint("buchung", __name__)

@buchung.route("/buchungen", methods=["GET"])
def buchungen_liste():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))
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

@buchung.route("/buchungen/loeschen", methods=["POST"], endpoint="buchungen_loeschen")
def buchungen_loeschen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))

        ids = request.form.getlist("ids")
        if not ids:
            flash("ℹ️ Es wurden keine Buchungen ausgewählt.")
            return redirect(url_for("buchung.buchungen_liste"))

        try:
            id_ints = [int(x) for x in ids]
        except ValueError:
            flash("❌ Ungültige Auswahl.")
            return redirect(url_for("buchung.buchungen_liste"))

        placeholders = ",".join("?" for _ in id_ints)
        params = tuple(id_ints) + (session["user_id"],)

        with get_db() as conn:
            conn.execute(
                f"DELETE FROM buchungen WHERE id IN ({placeholders}) AND user_id = ?",
                params,
            )

        flash(f"🗑️ {len(id_ints)} Buchung(en) gelöscht.")
        return redirect(url_for("buchung.buchungen_liste"))

@buchung.route("/buchungen/<int:buchung_id>/bearbeiten", methods=["GET", "POST"])
def buchung_bearbeiten(buchung_id):
        
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("auth.login"))

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
                return redirect(url_for("buchung.buchungen_liste"))

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
                    "SELECT typ, name FROM konten WHERE id = ? AND user_id = ?",
                        (konto_id, session["user_id"],),
                ).fetchone()

                if not konto:
                    flash("❌ Konto nicht gefunden.")
                    return redirect(url_for("buchung.buchungen_liste"))
                
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
                return redirect(url_for("buchung.buchungen_liste"))
            
            # Konten für Dropdown laden
            konten = conn.execute(
                """
                SELECT id, name, typ
                FROM konten
                WHERE user_id = ?
                    AND (
                        typ != 'neutral'
                        OR name IN (
                            'Privatentnahmen',
                            'Privateinlagen',
                            'Umsatzsteuer/Vorsteuer'
                        )
                )
            ORDER BY typ, name
            """,
            (session["user_id"],),
        ).fetchall()

            return render_template(
                "buchung_bearbeiten.html",
                buchung=buchung,
                konten=konten,
            )
        
@buchung.route("/buchungen/<int:buchung_id>/steuer-korrigieren", methods=["POST"])
def steuerzahlung_korrigieren(buchung_id):
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("auth.login"))

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
            return redirect(url_for("buchung.buchungen_liste"))

        if buchung["konto_name"] != "Umsatzsteuer/Vorsteuer":
            flash("❌ Diese Funktion ist nur für Steuerzahlungen.")
            return redirect(url_for("buchung.buchungen_liste"))


        # Nur korrigieren, wenn Steuerzahlung fälschlich im Netto steht
        if buchung["betrag_netto"] != 0 and buchung["steuerbetrag"] == 0:
            steuerbetrag = abs(buchung["betrag_netto"])
        else:
            flash("ℹ️ Diese Buchung muss nicht korrigiert werden.")
            return redirect(url_for("buchung.buchungen_liste"))

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
        return redirect(url_for("buchung.buchungen_liste"))

@buchung.route("/buchungen/neu", methods=["GET", "POST"])
def buchung_neu():
    if "user_id" not in session:
        flash("⛔ Bitte einloggen.")
        return redirect(url_for("auth.login"))
    
    with get_db() as conn:
        konten = conn.execute(
            """
            SELECT id, name, typ
            FROM konten
            WHERE user_id = ?
                AND (
                    typ != 'neutral'
                    OR name IN (
                        'Privateinlagen',
                        'Privatentnahmen'
                    )
                )
            ORDER BY typ, name
            """,
            (session["user_id"],),
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
                "SELECT typ FROM konten WHERE id = ? AND user_id = ?",
                (konto_id, session["user_id"],),
            ).fetchone()
            if not konto:
                flash("❌ Konto nicht gefunden.")
                return redirect(url_for("buchung.buchungen_liste"))

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
                    """
                        SELECT id
                        FROM konten
                        WHERE name = ?
                        AND user_id = ?
                    """,
                    (
                    "Umsatzsteuer/Vorsteuer",
                    session["user_id"],
                    ),
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
            return redirect(url_for("buchung.buchungen_liste"))

        return render_template(
        "buchung_neu.html",
        konten=konten,
        ) 