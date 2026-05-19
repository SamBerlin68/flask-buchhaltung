from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    session,
    flash,
)

main = Blueprint("main", __name__)


@main.route("/")
def home():
    return redirect(url_for("auth.login"))


@main.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        flash("⛔ Bitte zuerst einloggen.")
        return redirect(url_for("auth.login"))

    return render_template("dashboard.html")


@main.route("/_debug/routes")
def debug_routes():
    from flask import current_app

    rules = "\n".join(
        sorted(str(r) for r in current_app.url_map.iter_rules())
    )

    return f"<pre>{rules}</pre>"