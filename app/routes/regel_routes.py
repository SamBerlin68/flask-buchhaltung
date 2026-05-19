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

regel = Blueprint("regel", __name__)

@regel.route("/regeln", methods=["GET", "POST"])
def regeln():
        
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))
        with get_db() as conn:
            if request.method == "POST":
                suchbegriff = request.form.get("suchbegriff", "").strip()
                konto_id = request.form.get("konto_id")
                steuersatz = request.form.get("steuersatz")
                if not suchbegriff or not konto_id:
                    flash("❌ Bitte Suchbegriff und Konto angeben.")
                    return redirect(url_for("regel.regeln"))
                try:
                    conn.execute(
                        "INSERT INTO regeln (suchbegriff, konto_id, steuersatz) VALUES (?,?,?)",
                        (suchbegriff, int(konto_id), float(steuersatz or 19.0)),
                    )
                    flash("✅ Regel hinzugefügt.")
                except Exception as e:
                    flash(f"❌ Konnte Regel nicht speichern: {e}")
                return redirect(url_for("regel.regeln"))
            eintraege = conn.execute(
                """
                SELECT r.id, r.suchbegriff, r.steuersatz, k.name AS konto
                FROM regeln r JOIN konten k ON r.konto_id = k.id
                ORDER BY r.id DESC
                """
            ).fetchall()
            konten_liste = conn.execute(
                "SELECT id, name FROM konten ORDER BY name"
            ).fetchall()
        return render_template("regeln.html", regeln=eintraege, konten=konten_liste)

@regel.route("/regeln/<int:regel_id>/loeschen", methods=["POST"])
def regel_loeschen(regel_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))
        with get_db() as conn:
            conn.execute("DELETE FROM regeln WHERE id = ?", (regel_id,))
        flash("🗑️ Regel wurde gelöscht.")
        return redirect(url_for("regel.regeln"))

@regel.route("/regeln/<int:regel_id>/bearbeiten", methods=["GET", "POST"])
def regel_bearbeiten(regel_id: int):
        if "user_id" not in session:
            flash("⛔ Bitte zuerst einloggen.")
            return redirect(url_for("auth.login"))
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
                return redirect(url_for("regel.regeln"))
            regel = conn.execute(
                "SELECT * FROM regeln WHERE id = ?", (regel_id,)
            ).fetchone()
            konten_liste = conn.execute(
                "SELECT * FROM konten ORDER BY name"
            ).fetchall()
        return render_template("regel_bearbeiten.html", regel=regel, konten=konten_liste)