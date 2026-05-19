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
from app.services.beleg_service import next_belegnummer
from app.services.regel_service import finde_vorschlag

zuordnung = Blueprint("zuordnung", __name__)

@zuordnung.route("/zuordnen", methods=["GET", "POST"])
def zuordnen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))

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
                    return redirect(url_for("zuordnung.zuordnen"))

                trans = conn.execute(
                    "SELECT * FROM import_transaktionen WHERE id = ? AND user_id = ? AND verarbeitet = 0",
                    (trans_id, session["user_id"]),
                ).fetchone()

                
                
                if not trans:
                    flash("❌ Transaktion nicht gefunden oder schon verarbeitet.")
                    return redirect(url_for("zuordnung.zuordnen"))
                
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
                    return redirect(url_for("zuordnung.zuordnen"))

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
                        return redirect(url_for("zuordnung.zuordnen"))
                    if (
                        teilbetrag == 0.0
                        or (brutto > 0 and teilbetrag < 0)
                        or (brutto < 0 and teilbetrag > 0)
                        or abs(teilbetrag) > abs(brutto) + 1e-9
                    ):
                        flash("❌ Ungültiger Teilbetrag.")
                        return redirect(url_for("zuordnung.zuordnen"))
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

                return redirect(url_for("zuordnung.zuordnen"))

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

@zuordnung.route("/zuordnung_loeschen", methods=["POST"])
def zuordnung_loeschen():
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))
        
        with get_db() as conn:
            cur = conn.execute(
                "DELETE FROM import_transaktionen WHERE user_id = ? AND verarbeitet = 0",
                (session["user_id"],),
            )
            deleted = cur.rowcount or 0

        flash(f"🧹 Zuordnungsdaten (offene Importe) wurden gelöscht ({deleted}). Buchungen bleiben erhalten.")
        return redirect(url_for("main.dashboard"))
