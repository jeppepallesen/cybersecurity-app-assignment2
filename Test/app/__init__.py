import os
from datetime import timedelta

from flask import Flask, g, redirect, request, session, url_for
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
server_sessions = Session()
_session_interface = None


def _init_server_sessions(app):
    global _session_interface
    if _session_interface is None:
        server_sessions.init_app(app)
        _session_interface = app.session_interface
    else:
        app.session_interface = _session_interface
        if app.config["SESSION_CLEANUP_N_REQUESTS"]:
            app.before_request(_session_interface._cleanup_n_requests)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        AUTO_CREATE_SCHEMA=os.environ.get("AUTO_CREATE_SCHEMA", "false").lower() == "true",
        SEED_DEMO_DATA=os.environ.get("SEED_DEMO_DATA", "false").lower() == "true",
        APP_ENV=os.environ.get("APP_ENV", "production").lower(),
        DEBUG=False,
        ENABLE_HSTS=os.environ.get("ENABLE_HSTS", "false").lower() == "true",
        SESSION_TYPE="sqlalchemy",
        SESSION_SQLALCHEMY=db,
        SESSION_SQLALCHEMY_TABLE="flask_sessions",
        SESSION_USE_SIGNER=False,
        SESSION_PERMANENT=True,
        SESSION_CLEANUP_N_REQUESTS=100,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true",
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    if app.config["APP_ENV"] != "development":
        app.config["DEBUG"] = False
    if not app.config["SECRET_KEY"] or (
        not os.environ.get("SECRET_KEY") and not app.config.get("TESTING")
    ):
        raise RuntimeError(
            "SECRET_KEY must be set in the environment; test configuration may provide it when TESTING is enabled"
        )
    if not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("DATABASE_URL must be set in the environment")
    if not app.config["SESSION_COOKIE_SECURE"] and not (
        app.config["TESTING"] or app.config["APP_ENV"] == "development"
    ):
        raise RuntimeError(
            "SESSION_COOKIE_SECURE may be disabled only for testing or development"
        )

    db.init_app(app)
    _init_server_sessions(app)
    csrf.init_app(app)

    from app.models import User
    from app.routes import main

    app.register_blueprint(main)

    @app.before_request
    def load_user():
        user_id = session.get("user_id")
        g.user = db.session.get(User, user_id) if user_id else None
        if user_id and g.user is None:
            session.clear()

    @app.before_request
    def require_login():
        if request.endpoint and request.endpoint not in {"main.login", "static"}:
            if g.get("user") is None:
                return redirect(url_for("main.login"))

    @app.after_request
    def apply_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:",
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        if app.config["ENABLE_HSTS"] and request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000",
            )
        return response

    with app.app_context():
        if app.config["TESTING"] or app.config["AUTO_CREATE_SCHEMA"]:
            db.create_all()
        if app.config["SEED_DEMO_DATA"]:
            from app.seed import seed_demo_data

            seed_demo_data()

    return app