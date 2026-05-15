from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import sqlite3

from app.db import get_db
from app.extensions import bcrypt

auth = Blueprint("auth", __name__)


@auth.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not username or not password:
            flash("❌ Bitte Benutzername und Passwort angeben.")
            return redirect(url_for("auth.register"))

        if password != password_confirm:
            flash("❌ Die Passwörter stimmen nicht überein!")
            return redirect(url_for("auth.register"))

        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

        try:
            with get_db() as conn:

                conn.execute(
                    """
                    INSERT INTO users (username, password)
                    VALUES (?, ?)
                    """,
                    (username, hashed_pw),
                )

            flash("✅ Registrierung erfolgreich!")
            return redirect(url_for("auth.login"))

        except sqlite3.IntegrityError:
            flash("❌ Benutzername bereits vergeben.")
            return redirect(url_for("auth.register"))

    return render_template("register.html")


@auth.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_db() as conn:

            user = conn.execute(
                """
                SELECT id, username, password
                FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()

        if user and bcrypt.check_password_hash(user["password"], password):

            session["user_id"] = user["id"]

            flash("👋 Willkommen zurück!")
            return redirect(url_for("main.dashboard"))

        flash("❌ Login fehlgeschlagen.")
        return redirect(url_for("auth.login"))

    return render_template("login.html")


@auth.route("/logout")
def logout():

    session.clear()

    flash("🚪 Du wurdest ausgeloggt.")

    return redirect(url_for("auth.login"))