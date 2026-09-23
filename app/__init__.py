"""
Flask Application Factory (create_app).
Initializes extensions, registers blueprints, configures security headers,
and sets up global error handlers and Jinja context processors.
"""
import os
import time
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, jsonify, request, redirect, url_for, g, has_request_context, send_from_directory
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.config import config_by_name, DevelopmentConfig
from app.extensions import db, migrate, login_manager, csrf, limiter
from app.models.user import User
from app.models.setting import Setting
from app.paths import get_upload_dir, get_log_dir, get_migrations_dir

_query_listeners_registered = False


def register_query_timing_listeners():
    global _query_listeners_registered
    if _query_listeners_registered:
        return
    _query_listeners_registered = True

    @event.listens_for(Engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        conn.info.setdefault("query_start_times", []).append(time.perf_counter())

    @event.listens_for(Engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        try:
            start_time = conn.info.get("query_start_times", []).pop()
            duration_ms = (time.perf_counter() - start_time) * 1000
        except (IndexError, KeyError):
            duration_ms = 0.0

        if has_request_context():
            if not hasattr(g, "sql_queries"):
                g.sql_queries = []
            g.sql_queries.append({
                "statement": " ".join(str(statement).split()),
                "parameters": parameters,
                "duration_ms": duration_ms,
            })


def create_app(config_name: str = None) -> Flask:
    register_query_timing_listeners()
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development").lower()

    app = Flask(__name__)
    config_class = config_by_name.get(config_name, DevelopmentConfig)
    app.config.from_object(config_class)

    # Resolve Database URI with descriptive startup validation
    try:
        app.config["SQLALCHEMY_DATABASE_URI"] = config_class.get_database_uri()
    except RuntimeError as exc:
        if config_name == "production":
            raise exc
        # In development/CLI mode, record error so CLI commands (e.g. db-check) can report it
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        app.config["DB_CONFIG_ERROR"] = str(exc)

    try:
        config_class.validate_required_config()
    except RuntimeError as exc:
        if config_name == "production":
            raise exc
        if not app.config.get("DB_CONFIG_ERROR"):
            app.config["DB_CONFIG_ERROR"] = str(exc)

    if app.config.get("SQLALCHEMY_DATABASE_URI", "").startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }


    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db, directory=str(get_migrations_dir()))
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Configure User Loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from sqlalchemy.orm import joinedload
        return User.query.options(joinedload(User.role)).filter(User.id == int(user_id)).first()

    # Register Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.settings import settings_bp
    from app.blueprints.items import items_bp
    from app.blueprints.pos import pos_bp
    from app.blueprints.reports import reports_bp
    from app.blueprints.stock import stock_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(items_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(stock_bp)

    # Register CLI Commands
    from app.cli import (
        db_check_command,
        backup_db_command,
        restore_db_command,
        seed_command,
        seed_demo_command,
        process_print_queue_command,
        prune_orphan_uploads_command,
    )
    app.cli.add_command(db_check_command)
    app.cli.add_command(backup_db_command)
    app.cli.add_command(restore_db_command)
    app.cli.add_command(seed_command)
    app.cli.add_command(seed_demo_command)
    app.cli.add_command(process_print_queue_command)
    app.cli.add_command(prune_orphan_uploads_command)

    def format_money(amount):
        if amount is None:
            amount = 0
        try:
            val = float(amount)
        except (ValueError, TypeError):
            val = 0.0
        from app.services.setting_service import SettingService
        symbol = SettingService.get_value("currency_symbol", "Rs.")
        return f"{symbol} {val:,.2f}"

    app.jinja_env.filters["format_money"] = format_money
    app.jinja_env.globals["format_money"] = format_money

    from app.utils.timezone import to_pakistan_time
    def pkt_time_filter(dt, fmt="%Y-%m-%d %I:%M %p"):
        if not dt:
            return ""
        pkt = to_pakistan_time(dt)
        return pkt.strftime(fmt) if pkt else ""

    app.jinja_env.filters["pkt_time"] = pkt_time_filter
    app.jinja_env.globals["pkt_time"] = pkt_time_filter

    # Global Context Processor for Dynamic Branding & Configuration (Section 13)
    @app.context_processor
    def inject_settings():
        from app.services.setting_service import SettingService
        from app.utils.upload import resolve_image_url, resolve_fallback_url
        settings_dict = SettingService.get_all_settings()
        return {
            "app_settings": settings_dict,
            "org_name_en": settings_dict.get("org_name_en", "My Store"),
            "org_name_ur": settings_dict.get("org_name_ur", ""),
            "currency_symbol": settings_dict.get("currency_symbol", "Rs."),
            "format_money": format_money,
            "theme_primary": settings_dict.get("theme_color_primary", "#2563eb"),
            "theme_secondary": settings_dict.get("theme_color_secondary", "#0d9488"),
            "terminal_code": app.config.get("TERMINAL_CODE", ""),
            "counter_code": app.config.get("COUNTER_CODE", ""),
            "image_url": resolve_image_url,
            "image_fallback_url": resolve_fallback_url,
        }

    # Request Timing and Query Profiling (Performance measurement)
    @app.before_request
    def start_request_timer():
        g.request_start_time = time.perf_counter()
        g.sql_queries = []

    # Forced Password Change Interceptor (Must change password before accessing any other route)
    @app.before_request
    def enforce_must_change_password():
        if current_user.is_authenticated and getattr(current_user, "must_change_pwd", 0):
            allowed_endpoints = {"auth.change_password", "auth.logout", "static"}
            if request.endpoint and request.endpoint not in allowed_endpoints:
                return redirect(url_for("auth.change_password"))

    # Security Response Headers (Section 35 & 38.6)
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
        )
        return response

    # Serve Uploaded Assets from External Data Directory (C:\NoventraPOS\data\uploads)
    @app.route("/static/uploads/<path:filename>")
    def serve_external_uploads(filename):
        upload_base = app.config.get("UPLOAD_FOLDER", str(get_upload_dir()))
        return send_from_directory(upload_base, filename)

    @app.after_request
    def log_request_timing(response):
        if hasattr(g, "request_start_time"):
            total_ms = (time.perf_counter() - g.request_start_time) * 1000
            queries = getattr(g, "sql_queries", [])
            query_count = len(queries)
            total_sql_ms = sum(q["duration_ms"] for q in queries)
            log_msg = (
                f"[REQUEST TIMING] route={request.path} "
                f"total_ms={total_ms:.2f} query_count={query_count} total_sql_ms={total_sql_ms:.2f}"
            )
            app.logger.info(log_msg)
            print(log_msg, flush=True)
            response.headers["X-Total-Time-Ms"] = f"{total_ms:.2f}"
            response.headers["X-Query-Count"] = str(query_count)
            response.headers["X-Total-SQL-Ms"] = f"{total_sql_ms:.2f}"
            app.last_request_info = {
                "route": request.path,
                "total_ms": total_ms,
                "query_count": query_count,
                "total_sql_ms": total_sql_ms,
                "queries": list(queries),
            }
        return response

    # Error Handlers
    @app.errorhandler(403)
    def forbidden_error(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template("errors/404.html"), 404

    # Configure Persistent Rotating File Logging (C:\NoventraPOS\data\logs)
    try:
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "noventra_pos.log"
        file_handler = RotatingFileHandler(str(log_file), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s"))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
    except Exception:
        pass

    return app
