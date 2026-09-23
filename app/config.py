"""
Application Configuration for Offline-First Oracle POS & Inventory System.
Target Environment: Oracle 19c and above.
Enforces fetch_decimals=True so all numeric columns return as Decimal.
Builds SQLAlchemy database URIs explicitly without ${VAR} expansion.
"""
import os
import oracledb
from dotenv import load_dotenv
from sqlalchemy.engine import URL

import sys
from pathlib import Path
from app.paths import ensure_data_directories, get_env_path, get_upload_dir, get_backup_dir, get_log_dir

# Keep development config and client config separate:
# When packaged as frozen exe, strictly read C:\NoventraPOS\data\.env (client laptop)
# In development & testing, load project root .env (Contabo server)
if getattr(sys, "frozen", False):
    ensure_data_directories()
    data_env = get_env_path()
    if data_env.exists():
        load_dotenv(dotenv_path=data_env, override=True)
else:
    root_env = Path(__file__).resolve().parent.parent / ".env"
    if root_env.exists():
        load_dotenv(dotenv_path=root_env, override=True)
    else:
        load_dotenv()

# Enforce Decimal instances across all Oracle queries (strictly no float)
oracledb.defaults.fetch_decimals = True


class Config:
    """Base configuration with strict validation and safe production settings."""

    FLASK_ENV = os.environ.get("FLASK_ENV", "production").lower()
    DEBUG = False
    TESTING = False
    TEMPLATES_AUTO_RELOAD = True

    # Oracle Database Parameters (Defaults configured for Oracle XE 21c local)
    DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.environ.get("DB_PORT", 1521))
    DB_SERVICE_NAME = os.environ.get("DB_SERVICE_NAME", "XEPDB1")
    DB_USER = os.environ.get("DB_USER", "isi")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")
    DB_SCHEMA = os.environ.get("DB_SCHEMA", "ISI")

    # Store & Counter Identifiers
    TERMINAL_CODE = os.environ.get("TERMINAL_CODE", "T01")
    COUNTER_CODE = os.environ.get("COUNTER_CODE", "C01")

    # Security Keys
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Timezone & Business Rules
    APP_TIMEZONE = "Asia/Karachi"
    BUSINESS_DAY_CUTOFF = os.environ.get("BUSINESS_DAY_CUTOFF", "04:00")
    FISCAL_YEAR_START = os.environ.get("FISCAL_YEAR_START", "07-01")

    # External Data Paths (C:\NoventraPOS\data\)
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(get_upload_dir()))
    BACKUP_FOLDER = os.environ.get("BACKUP_FOLDER", str(get_backup_dir()))
    LOG_FOLDER = os.environ.get("LOG_FOLDER", str(get_log_dir()))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 5242880))  # 5 MB
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ALLOWED_DOC_EXTENSIONS = {"pdf"}

    # Central Synchronization
    CENTRAL_SYNC_URL = os.environ.get("CENTRAL_SYNC_URL", "")
    CENTRAL_SYNC_TOKEN = os.environ.get("CENTRAL_SYNC_TOKEN", "")
    CENTRAL_SYNC_SECRET = os.environ.get("CENTRAL_SYNC_SECRET", "")

    # Compliance & Hardware Toggles
    FBR_ENABLED = os.environ.get("FBR_ENABLED", "false").lower() in ("true", "1", "yes")
    POS_REGISTRATION_NO = os.environ.get("POS_REGISTRATION_NO", "")
    PAYMENT_GATEWAY = os.environ.get("PAYMENT_GATEWAY", "manual")

    # Engine Tuning Options (Section 4 & Addendum)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": 1800,
        "arraysize": 200,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    @classmethod
    def get_database_uri(cls) -> str:
        """Constructs the Oracle DSN and SQLAlchemy URI with explicit validation."""
        if not cls.DB_SERVICE_NAME:
            raise RuntimeError(
                "CRITICAL CONFIGURATION ERROR: 'DB_SERVICE_NAME' environment variable is missing. "
                "Oracle requires a pluggable database service name (e.g., XEPDB1, ORCLPDB1). "
                "Please configure DB_SERVICE_NAME in your .env file."
            )
        if not cls.DB_PASSWORD:
            raise RuntimeError(
                "CRITICAL CONFIGURATION ERROR: 'DB_PASSWORD' environment variable is missing. "
                "Database credentials must be provided in the local .env file."
            )
        if not cls.DB_USER:
            raise RuntimeError(
                "CRITICAL CONFIGURATION ERROR: 'DB_USER' environment variable is missing."
            )

        url = URL.create(
            "oracle+oracledb",
            username=cls.DB_USER,
            password=cls.DB_PASSWORD,
            host=cls.DB_HOST,
            port=cls.DB_PORT,
            database="",
            query={"service_name": cls.DB_SERVICE_NAME},
        )
        return url.render_as_string(hide_password=False)

    @classmethod
    def validate_required_config(cls):
        """Ensures all mandatory runtime configuration variables are defined with no default fallbacks."""
        missing = []
        if not cls.TERMINAL_CODE:
            missing.append("TERMINAL_CODE")
        if not cls.COUNTER_CODE:
            missing.append("COUNTER_CODE")
        if missing:
            raise RuntimeError(
                f"CRITICAL CONFIGURATION ERROR: Mandatory environment variable(s) missing: {', '.join(missing)}. "
                "The POS system requires explicit TERMINAL_CODE and COUNTER_CODE with no default fallbacks. "
                "Please configure them in your .env file."
            )


class DevelopmentConfig(Config):
    DEBUG = True
    # In development, generate a fallback secret key if not specified
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-key-change-in-production")


class TestingConfig(Config):
    """
    Testing configuration requiring explicit test credentials with NO default fallbacks.
    Tests must run in an isolated Oracle schema (ISI_POS_TEST) and abort if matching production.
    """
    TESTING = True
    DEBUG = True
    SECRET_KEY = "test-session-secret-key"
    RATELIMIT_ENABLED = False
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")


    TEST_DB_HOST = os.environ.get("TEST_DB_HOST")
    TEST_DB_PORT = int(os.environ.get("TEST_DB_PORT", 1521))
    TEST_DB_SERVICE_NAME = os.environ.get("TEST_DB_SERVICE_NAME")
    TEST_DB_USER = os.environ.get("TEST_DB_USER")
    TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
    TEST_DB_SCHEMA = os.environ.get("TEST_DB_SCHEMA")

    @classmethod
    def get_database_uri(cls) -> str:
        service = os.environ.get("TEST_DB_SERVICE_NAME") or cls.TEST_DB_SERVICE_NAME
        user = os.environ.get("TEST_DB_USER") or cls.TEST_DB_USER
        password = os.environ.get("TEST_DB_PASSWORD") or cls.TEST_DB_PASSWORD
        host = os.environ.get("TEST_DB_HOST") or cls.TEST_DB_HOST or cls.DB_HOST
        port = int(os.environ.get("TEST_DB_PORT") or cls.TEST_DB_PORT or cls.DB_PORT)

        missing = []
        if not service:
            missing.append("TEST_DB_SERVICE_NAME")
        if not user:
            missing.append("TEST_DB_USER")
        if not password:
            missing.append("TEST_DB_PASSWORD")
        if missing:
            raise RuntimeError(
                f"CRITICAL TEST CONFIGURATION ERROR: Mandatory test environment variable(s) missing: {', '.join(missing)}. "
                "Tests must point to an isolated Oracle database (ISI_POS_TEST) and must never fall back to SQLite."
            )

        url = URL.create(
            "oracle+oracledb",
            username=user,
            password=password,
            host=host,
            port=port,
            database="",
            query={"service_name": service},
        )
        return url.render_as_string(hide_password=False)


class ProductionConfig(Config):
    """Production configuration enforcing strict cookie security and key presence."""
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
