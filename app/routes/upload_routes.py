from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
)

import io
import csv

from app.db import get_db
from app.services.csv_service import (
    detect_encoding,
    parse_date_any,
    parse_amount,
)

upload = Blueprint("upload", __name__)


@upload.route("/upload", methods=["GET", "POST"])
def upload_csv():
    if "user_id" not in session:
        flash("⛔ Du musst eingeloggt sein.")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        try:
            file = request.files.get("csv_file")
            if not file or file.filename == "":
                flash("❌ Keine Datei ausgewählt.")
                return redirect(url_for("upload.upload_csv"))

            raw = file.read()
            if not raw:
                flash("❌ Datei ist leer.")
                return redirect(url_for("upload.upload_csv"))

            enc = detect_encoding(raw)
            try:
                content = raw.decode(enc, errors="replace")
            except Exception:
                flash(f"❌ Fehler beim Dekodieren (Encoding: {enc}).")
                return redirect(url_for("upload.upload_csv"))

            # Delimiter automatisch erkennen (Fallback ';')
            try:
                sample = "\n".join(content.splitlines()[:5])
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                delimiter = dialect.delimiter
            except Exception:
                delimiter = ";"

            def norm(s: str) -> str:
                s = (s or "").strip().lower()
                repl = str.maketrans(
                    {
                        "ä": "ae",
                        "ö": "oe",
                        "ü": "ue",
                        "ß": "ss",
                        " ": "",
                        "/": "",
                        "-": "",
                        ".": "",
                        ":": "",
                    }
                )
                return s.translate(repl)

            header_map = {
                "buchungsdatum": "date",
                "datum": "date",
                "wertstellung": "date",
                "empfaenger": "payee",
                "empfänger": "payee",
                "beguenstigter": "payee",
                "verwendungszweck": "purpose",
                "verwendungszweckprimanota": "purpose",
                "verwendungszweck1": "purpose",
                "betrag": "amount",
                "umsatz": "amount",
            }

            stream = io.StringIO(content)
            reader = csv.DictReader(stream, delimiter=delimiter)
            if not reader.fieldnames:
                flash("❌ CSV hat keine Kopfzeile.")
                return redirect(url_for("upload.upload_csv"))

            fields_norm = {norm(fn): fn for fn in reader.fieldnames}

            def get(row: dict, key_alias: str) -> str:
                for norm_name, original in fields_norm.items():
                    if header_map.get(norm_name) == key_alias:
                        return (row.get(original) or "").strip()
                return ""

            count = 0
            with get_db() as conn:
                for row in reader:
                    try:
                        datum_raw = get(row, "date")
                        empfaenger = get(row, "payee")
                        verwendungszweck = get(row, "purpose")
                        betrag_raw = get(row, "amount")

                        if not (datum_raw and betrag_raw):
                            continue

                        datum = parse_date_any(datum_raw)
                        betrag = parse_amount(betrag_raw)

                        conn.execute(
                            """
                                INSERT INTO import_transaktionen (user_id, datum, empfaenger, verwendungszweck, betrag, verarbeitet)
                                VALUES (?,?,?,?,?,0)
                                """,
                            (
                                session["user_id"],
                                datum,
                                empfaenger,
                                verwendungszweck,
                                betrag,
                            ),
                        )
                        count += 1
                    except Exception as e:
                        print(f"⚠️ Fehler beim Verarbeiten einer Zeile: {e}")
                        continue

            if count == 0:
                flash(
                    "ℹ️ Es konnten keine Transaktionen importiert werden. Bitte Delimiter/Spalten prüfen."
                )
            else:
                flash(
                    f"✅ {count} Transaktionen wurden importiert. Jetzt kannst du sie zuordnen."
                )
            return redirect(url_for("zuordnung.zuordnen"))

        except Exception as e:
            print(f"❌ Upload-Fehler: {e}")
            flash(f"❌ Unerwarteter Fehler beim Upload: {e}")
            return redirect(url_for("upload.upload_csv"))

    return render_template("upload.html")
