from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

from datetime import date

from app.db import get_db

report = Blueprint("report", __name__)

@report.route("/ustva")
def ustva():
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("auth.login"))

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
                return redirect(url_for("main.dashboard"))

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

@report.route("/bwa")
def bwa():
        if "user_id" not in session:
            flash("⛔ Bitte einloggen.")
            return redirect(url_for("auth.login"))

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

