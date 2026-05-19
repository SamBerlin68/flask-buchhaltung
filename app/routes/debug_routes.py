from flask import Blueprint, session, current_app
debug = Blueprint("debug", __name__)

@debug.route("/debug_user")
def debug_user():

    return (
        f"Deine User-ID: {session.get('user_id')} | "
        f"DB: {current_app.config['DATABASE']}"
    )