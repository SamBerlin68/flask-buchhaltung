from flask import Flask

from app.routes.auth_routes import auth
from app.routes.main_routes import main


def create_app():

    app = Flask(__name__)

    app.config["SECRET_KEY"] = "dev"

    app.register_blueprint(auth)
    app.register_blueprint(main)

    return app