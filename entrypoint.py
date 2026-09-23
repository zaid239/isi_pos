"""
Noventra POS - Desktop Entrypoint & Command Dispatcher.
Executes as a standalone Windows application powered by Waitress.
Enforces:
- Single running instance via Windows Named Mutex.
- External data directory (C:\\NoventraPOS\\data\\).
- Explicit manual CLI switches (--db-check, --migrate, --backup, --create-admin, --setup-shortcuts).
- Non-blocking daily backup execution.
- Automated browser launch on startup.
"""
import os
import sys
import time
import ctypes
import getpass
import shutil
import argparse
import subprocess
import threading
import webbrowser

from app.paths import ensure_data_directories, get_env_path, get_data_dir
from app import create_app
from app.extensions import db
from app.services.backup_manager import execute_backup, run_daily_backup_if_needed
from app.utils.shortcuts import setup_all_shortcuts


MUTEX_NAME = "NoventraPOS_SingleInstance_Mutex"
ERROR_ALREADY_EXISTS = 183


def open_pos_browser(app_url: str):
    """
    Opens the POS in Google Chrome with --app and --kiosk-printing flags.
    Falls back to the system default browser if Chrome is not installed.
    """
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LocalAppData", ""), "Google", "Chrome", "Application", "chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]

    chrome_bin = None
    for p in candidates:
        if p and os.path.isfile(p):
            chrome_bin = p
            break

    if chrome_bin:
        try:
            subprocess.Popen([
                chrome_bin,
                f"--app={app_url}",
                "--kiosk-printing",
            ])
            return
        except Exception:
            pass

    # Fallback to default browser
    webbrowser.open(app_url)


def acquire_single_instance_mutex():
    """
    Ensures only one instance of Noventra POS runs simultaneously on the machine.
    Returns (mutex_handle, is_primary_instance).
    """
    if os.name != "nt":
        return None, True

    try:
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_err = kernel32.GetLastError()
        if last_err == ERROR_ALREADY_EXISTS:
            return mutex, False
        return mutex, True
    except Exception:
        return None, True


def run_db_check(app):
    """Validates Oracle connectivity, character set AL32UTF8, and schema privileges."""
    from app.cli import run_db_check as execute_check
    with app.app_context():
        try:
            execute_check(app)
        except SystemExit as se:
            sys.exit(se.code)
        except Exception as err:
            print(f"[FATAL ERROR] db-check failed: {err}")
            sys.exit(1)


def run_migrate(app):
    """
    Takes a database backup first, then runs Alembic database migrations.
    Never runs migrations automatically or silently.
    """
    from flask_migrate import upgrade
    from app.paths import get_migrations_dir

    print("\n" + "=" * 60)
    print("  NOVENTRA POS: DATABASE MIGRATION PIPELINE")
    print("=" * 60)

    # 1. Take safety backup first
    print("\n[STEP 1/2] Taking safety backup before running migrations...")
    success, msg, backup_file = execute_backup(app, is_daily=False)
    if not success:
        print(f"[ERROR] Pre-migration safety backup failed:\n  {msg}")
        print("\nMigration aborted to prevent unrecoverable schema state.")
        sys.exit(1)
    print(f"[PASS] Pre-migration safety backup completed: {backup_file}")

    # 2. Execute Alembic Upgrade
    print("\n[STEP 2/2] Running Alembic database migrations (upgrade head)...")
    try:
        with app.app_context():
            migrations_dir = str(get_migrations_dir())
            upgrade(directory=migrations_dir)
        print("\n[SUCCESS] Database schema successfully upgraded to latest revision.")
    except Exception as err:
        print(f"\n[FATAL ERROR] Alembic migration failed: {err}")
        print(f"Restore point is available at: {backup_file}")
        sys.exit(1)


def run_manual_backup(app):
    """Executes an immediate manual database backup with USB replication."""
    print("\n" + "=" * 60)
    print("  NOVENTRA POS: MANUAL DATABASE BACKUP")
    print("=" * 60)
    success, msg, backup_file = execute_backup(app, is_daily=False)
    if success:
        print(f"\n[SUCCESS] Backup completed successfully!")
        print(f"File: {backup_file}")
        print(f"Details: {msg}")
    else:
        print(f"\n[ERROR] Backup failed: {msg}")
        sys.exit(1)


def run_create_admin(app, username=None, password=None):
    """Interactively or programmatically creates or updates the Super Admin user account."""
    from app.models.user import Role, User

    print("\n" + "=" * 60)
    print("  NOVENTRA POS: CREATE OR UPDATE SUPER ADMIN ACCOUNT")
    print("=" * 60)

    with app.app_context():
        # Ensure super_admin role exists
        admin_role = Role.query.filter_by(role_code="super_admin").first()
        if not admin_role:
            admin_role = Role(
                role_code="super_admin",
                role_name="Super Admin",
                description="Full administrative access",
                is_system=1,
            )
            db.session.add(admin_role)
            db.session.commit()

        if not username:
            if sys.stdin and sys.stdin.isatty():
                username = input("Enter admin username [default: admin]: ").strip() or "admin"
            else:
                username = "admin"

        if not password:
            if sys.stdin and sys.stdin.isatty():
                while True:
                    password = getpass.getpass("Enter password: ").strip()
                    if len(password) < 6:
                        print("Password must be at least 6 characters.")
                        continue
                    confirm = getpass.getpass("Confirm password: ").strip()
                    if password != confirm:
                        print("Passwords do not match. Please try again.")
                        continue
                    break
            else:
                password = "Admin123!"

        user = User.query.filter_by(username=username).first()
        if user:
            user.set_password(password)
            user.role_id = admin_role.id
            user.is_active = 1
            user.locked_until = None
            user.failed_attempts = 0
            user.must_change_pwd = 0
            print(f"\n[*] Existing user '{username}' password successfully updated.")
        else:
            user = User(
                username=username,
                full_name="System Administrator",
                email=f"{username}@pos.local",
                role_id=admin_role.id,
                is_active=1,
                must_change_pwd=0,
            )
            user.set_password(password)
            db.session.add(user)
            print(f"\n[+] Super Admin '{username}' successfully created.")

        db.session.commit()
        print("[SUCCESS] Super Admin account is active and ready for login.")


def run_app():
    """Launches the POS application on Waitress with single-instance mutex and background tasks."""
    port = int(os.environ.get("PORT", 5000))
    app_url = f"http://127.0.0.1:{port}/"

    mutex, is_primary = acquire_single_instance_mutex()
    if not is_primary:
        print("[INFO] Noventra POS is already running. Opening existing instance in browser...")
        open_pos_browser(app_url)
        sys.exit(0)

    # 1. Ensure C:\NoventraPOS\data\ directories exist
    ensure_data_directories()

    # 2. Setup shortcuts silently on first launch
    try:
        setup_all_shortcuts()
    except Exception:
        pass

    # 3. Create Flask Application
    try:
        app = create_app()
    except RuntimeError as err:
        if "DB_PASSWORD" in str(err):
            print("\n" + "=" * 65)
            print("  [SETUP REQUIRED] Database password is not configured.")
            print(f"  Please open: {get_env_path()}")
            print("  and fill in 'DB_PASSWORD=<your_password>' to continue.")
            print("=" * 65 + "\n")
            sys.exit(1)
        raise

    # 4. Schedule automatic browser launch after Waitress begins listening
    def _open_browser():
        time.sleep(1.2)
        open_pos_browser(app_url)

    threading.Thread(target=_open_browser, name="NoventraBrowserLauncher", daemon=True).start()

    # 5. Non-blocking daily backup in background thread
    run_daily_backup_if_needed(app)

    # 6. Banner
    print("\n" + "=" * 65)
    print("  NOVENTRA POS - DESKTOP SYSTEM READY")
    print(f"  URL:      {app_url}")
    print(f"  Data Dir: {get_data_dir()}")
    print("=" * 65)
    print("Server running on Waitress. Close this window to shut down the POS.\n")

    # 7. Start Waitress Server (blocking main thread)
    from waitress import serve
    try:
        serve(app, host="127.0.0.1", port=port, threads=6)
    except (KeyboardInterrupt, SystemExit):
        print("\nNoventra POS shut down cleanly.")


def main():
    parser = argparse.ArgumentParser(
        description="Noventra POS - Standalone Desktop System",
        add_help=True
    )
    parser.add_argument("--db-check", action="store_true", help="Validate Oracle database connectivity and character set")
    parser.add_argument("--migrate", action="store_true", help="Take safety backup and run Alembic database migrations")
    parser.add_argument("--backup", action="store_true", help="Take immediate database backup with USB replication")
    parser.add_argument("--create-admin", action="store_true", help="Interactively create or update Super Admin user")
    parser.add_argument("--setup-shortcuts", action="store_true", help="Create Desktop and Startup folder shortcuts")
    parser.add_argument("--username", default=None, help="Super Admin username (optional)")
    parser.add_argument("--password", default=None, help="Super Admin password (optional)")

    args = parser.parse_args()

    # Initialize data directories before any operation
    ensure_data_directories()

    if args.setup_shortcuts:
        ok, msg = setup_all_shortcuts()
        print(msg)
        sys.exit(0 if ok else 1)

    if args.db_check:
        app = create_app()
        run_db_check(app)
        sys.exit(0)

    if args.migrate:
        app = create_app()
        run_migrate(app)
        sys.exit(0)

    if args.backup:
        app = create_app()
        run_manual_backup(app)
        sys.exit(0)

    if args.create_admin:
        app = create_app()
        run_create_admin(app, username=args.username, password=args.password)
        sys.exit(0)

    # Default action: run the application
    run_app()


if __name__ == "__main__":
    main()
