"""
Centralized Path Resolution for Noventra POS.
Ensures all writable state (.env, uploads, logs, backups) is kept strictly in C:\\NoventraPOS\\data\\.
"""
import os
import secrets
from pathlib import Path

# Base data directory (defaults to C:\NoventraPOS\data, overridable by env for tests)
DEFAULT_DATA_DIR = os.environ.get("NOVENTRA_DATA_DIR", r"C:\NoventraPOS\data")


def get_data_dir() -> Path:
    """Returns the root data directory path."""
    return Path(os.environ.get("NOVENTRA_DATA_DIR", DEFAULT_DATA_DIR)).resolve()


def get_env_path() -> Path:
    """Returns the full path to data\\.env."""
    return get_data_dir() / ".env"


def get_upload_dir(subfolder: str = "") -> Path:
    """Returns the upload directory or a subfolder within it."""
    base = get_data_dir() / "uploads"
    if subfolder:
        return base / subfolder
    return base


def get_backup_dir() -> Path:
    """Returns the backups directory."""
    return get_data_dir() / "backups"


def get_log_dir() -> Path:
    """Returns the logs directory."""
    return get_data_dir() / "logs"


def ensure_data_directories() -> Path:
    """
    Creates C:\\NoventraPOS\\data\\ and all required subdirectories:
    - uploads/items, uploads/logos, uploads/temp
    - logs
    - backups
    If data\\.env is missing, initializes it with a freshly generated SECRET_KEY
    and Oracle XE 21c default configuration (leaving DB_PASSWORD blank).
    """
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    for sub in ["items", "logos", "temp"]:
        (data_dir / "uploads" / sub).mkdir(parents=True, exist_ok=True)

    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "backups").mkdir(parents=True, exist_ok=True)

    env_file = get_env_path()
    if not env_file.exists():
        fresh_secret_key = secrets.token_hex(32)
        env_content = f"""# ==============================================================================
# Noventra POS Configuration (Oracle XE 21c)
# Data Directory: {data_dir}
# ==============================================================================
FLASK_ENV=production
SECRET_KEY={fresh_secret_key}

# Oracle Database Configuration (Oracle XE 21c local)
DB_HOST=127.0.0.1
DB_PORT=1521
DB_SERVICE_NAME=XEPDB1
DB_USER=isi
DB_PASSWORD=
DB_SCHEMA=ISI

# Store & Counter Identifiers
TERMINAL_CODE=T01
COUNTER_CODE=C01

# Business & Hardware Defaults
BUSINESS_DAY_CUTOFF=04:00
FISCAL_YEAR_START=07-01
FBR_ENABLED=false
PAYMENT_GATEWAY=manual
"""
        env_file.write_text(env_content, encoding="utf-8")

    return data_dir


def get_bundle_dir() -> Path:
    """Returns the root directory where application bundle assets reside."""
    import sys
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS).resolve()
        internal_p = Path(sys.executable).parent / "_internal"
        if internal_p.exists():
            return internal_p.resolve()
        return Path(sys.executable).parent.resolve()
    return Path(__file__).resolve().parent.parent


def get_migrations_dir() -> Path:
    """Returns the absolute path to the migrations directory, resolving from sys._MEIPASS when frozen."""
    import sys
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            meipass_p = Path(sys._MEIPASS) / "migrations"
            if meipass_p.exists():
                return meipass_p.resolve()
        # Fallback to _internal subfolder adjacent to the executable
        internal_p = Path(sys.executable).parent / "_internal" / "migrations"
        if internal_p.exists():
            return internal_p.resolve()
        # Fallback to next to executable
        exe_p = Path(sys.executable).parent / "migrations"
        if exe_p.exists():
            return exe_p.resolve()
        if hasattr(sys, "_MEIPASS"):
            return (Path(sys._MEIPASS) / "migrations").resolve()
        return internal_p.resolve()
    return (Path(__file__).resolve().parent.parent / "migrations").resolve()

