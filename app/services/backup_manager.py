"""
Automated Daily Backup Manager for Noventra POS.
Features:
- Runs automatically on first launch of each day in a background thread.
- Dual-mode export: Oracle Data Pump (expdp) if available; otherwise comprehensive logical JSON/SQL dump.
- Rotates backups: strictly retains the 30 most recent backup files.
- USB replication: copies latest backup to configured USB drive letter if mounted.
- Status tracking: provides freshness indicator for dashboard ("Today", "X days ago", critical in red).
"""
import os
import shutil
import json
import subprocess
import threading
from datetime import datetime, date, timedelta
from typing import Tuple, Optional, Dict, Any
from sqlalchemy import text
from app.paths import get_backup_dir
from app.extensions import db


def rotate_backups(backup_dir: str, max_backups: int = 30):
    """
    Keeps only the most recent max_backups files in the backup directory.
    Prunes older backup dump and log files.
    """
    if not os.path.isdir(backup_dir):
        return

    # Find all backup files (exclude .last_backup_info.json)
    files = []
    for f in os.listdir(backup_dir):
        if f.startswith("."):
            continue
        fp = os.path.join(backup_dir, f)
        if os.path.isfile(fp):
            files.append((fp, os.path.getmtime(fp)))

    # Sort by modification time descending (newest first)
    files.sort(key=lambda x: x[1], reverse=True)

    # Separate dumpfiles and logfiles or total count
    if len(files) > max_backups:
        to_delete = files[max_backups:]
        for fp, _ in to_delete:
            try:
                os.remove(fp)
            except OSError:
                pass


def replicate_to_usb(source_file: str, usb_drive_letter: Optional[str]) -> Tuple[bool, str]:
    r"""
    If a USB drive letter is configured and mounted, copies the backup file to <DRIVE>:\NoventraPOS_Backups\.
    """
    if not usb_drive_letter or not str(usb_drive_letter).strip():
        return False, "No USB drive letter configured."

    drive = str(usb_drive_letter).strip().rstrip("\\/").upper()
    if not drive.endswith(":"):
        drive = f"{drive}:"

    drive_root = f"{drive}\\"
    if not os.path.exists(drive_root):
        return False, f"USB Drive '{drive}' is not connected."

    try:
        dest_folder = os.path.join(drive_root, "NoventraPOS_Backups")
        os.makedirs(dest_folder, exist_ok=True)
        dest_file = os.path.join(dest_folder, os.path.basename(source_file))
        shutil.copy2(source_file, dest_file)
        return True, f"Replicated to USB drive: {dest_file}"
    except Exception as exc:
        return False, f"Failed to copy to USB drive: {exc}"


def execute_backup(app, is_daily: bool = False) -> Tuple[bool, str, Optional[str]]:
    r"""
    Executes a full database backup into C:\NoventraPOS\data\backups\.
    Rotates to retain last 30 files, and replicates to USB if configured.
    """
    with app.app_context():
        backup_dir = str(get_backup_dir())
        os.makedirs(backup_dir, exist_ok=True)

        db_user = app.config.get("DB_USER", "isi")
        db_pass = app.config.get("DB_PASSWORD", "")
        db_service = app.config.get("DB_SERVICE_NAME", "XEPDB1")
        oracle_dir = os.environ.get("ORACLE_BACKUP_DIR", "POS_BACKUP_DIR")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "noventra_daily" if is_daily else "noventra_manual"
        dumpfile_name = f"{prefix}_{timestamp}.dmp"
        logfile_name = f"{prefix}_{timestamp}.log"
        dump_path = os.path.join(backup_dir, dumpfile_name)
        log_path = os.path.join(backup_dir, logfile_name)

        # 1. Try expdp if available on PATH
        expdp_available = False
        try:
            res = subprocess.run(["expdp", "-help"], capture_output=True, text=True)
            if res.returncode == 0 or "Export: Release" in res.stdout:
                expdp_available = True
        except FileNotFoundError:
            expdp_available = False

        success = False
        message = ""
        created_file = None

        if expdp_available and db_pass:
            try:
                cmd = [
                    "expdp",
                    f"{db_user}/{db_pass}@{db_service}",
                    f"schemas={db_user.upper()}",
                    f"directory={oracle_dir}",
                    f"dumpfile={dumpfile_name}",
                    f"logfile={logfile_name}",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    success = True
                    created_file = dump_path
                    message = f"Oracle Data Pump backup completed: {dumpfile_name}"
                else:
                    message = f"expdp failed: {res.stderr or res.stdout}"
            except Exception as e:
                message = f"expdp error: {e}"

        # 2. Fallback to logical JSON dump over SQLAlchemy connection
        if not success:
            try:
                dump_data = {
                    "metadata": {
                        "timestamp": timestamp,
                        "schema": db_user.upper(),
                        "service": db_service,
                        "created_at": datetime.now().isoformat(),
                    },
                    "tables": {}
                }
                with db.engine.connect() as conn:
                    tables_query = text(
                        "SELECT table_name FROM all_tables WHERE owner = :schema ORDER BY table_name"
                    )
                    tables = [r[0] for r in conn.execute(tables_query, {"schema": db_user.upper()}).fetchall()]

                    for tbl in tables:
                        rows_query = text(f'SELECT * FROM "{db_user.upper()}"."{tbl}"')
                        result = conn.execute(rows_query)
                        cols = list(result.keys())
                        rows_data = []
                        for row in result.fetchall():
                            row_dict = {}
                            for col, val in zip(cols, row):
                                if isinstance(val, datetime):
                                    row_dict[col] = val.isoformat()
                                elif hasattr(val, "__float__") and not isinstance(val, (int, bool)):
                                    row_dict[col] = str(val)
                                elif isinstance(val, bytes):
                                    row_dict[col] = val.hex()
                                else:
                                    row_dict[col] = val
                            rows_data.append(row_dict)

                        dump_data["tables"][tbl] = {
                            "columns": cols,
                            "rows": rows_data,
                        }

                json_dump_path = os.path.join(backup_dir, f"{prefix}_{timestamp}.json")
                with open(json_dump_path, "w", encoding="utf-8") as jf:
                    json.dump(dump_data, jf, indent=2)

                success = True
                created_file = json_dump_path
                message = f"Logical database backup completed: {os.path.basename(json_dump_path)}"
            except Exception as exc:
                success = False
                message = f"Database backup failed: {exc}"
                created_file = None

        # Replicate to USB drive if configured and backup succeeded
        usb_result_msg = ""
        if success and created_file:
            from app.services.setting_service import SettingService
            usb_drive = SettingService.get_value("backup_usb_drive", "")
            if usb_drive:
                usb_ok, usb_msg = replicate_to_usb(created_file, usb_drive)
                usb_result_msg = f" | USB: {usb_msg}"

        # Rotate backups to keep only last 30 files
        rotate_backups(backup_dir, max_backups=30)

        # Update metadata file for instant dashboard status lookup
        info_file = os.path.join(backup_dir, ".last_backup_info.json")
        try:
            info = {
                "last_backup_date": date.today().isoformat(),
                "last_backup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "success" if success else "failed",
                "message": message + usb_result_msg,
                "file": created_file,
            }
            with open(info_file, "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)
        except Exception:
            pass

        return success, message + usb_result_msg, created_file


def run_daily_backup_if_needed(app):
    """
    Checks if a backup has already been taken today.
    If not, launches execute_backup in a background daemon thread so it never blocks Waitress startup or browser launch.
    """
    import time

    def _worker():
        # Non-blocking: allow Waitress to start and browser to open first
        time.sleep(3.0)
        try:
            backup_dir = str(get_backup_dir())
            info_file = os.path.join(backup_dir, ".last_backup_info.json")
            today_str = date.today().isoformat()

            if os.path.isfile(info_file):
                try:
                    with open(info_file, "r", encoding="utf-8") as f:
                        info = json.load(f)
                    if info.get("last_backup_date") == today_str and info.get("status") == "success":
                        # Already backed up today
                        return
                except Exception:
                    pass

            # Execute backup in background
            success, msg, backup_file = execute_backup(app, is_daily=True)
            if not success:
                app.logger.error(f"Daily background backup failed: {msg}")
        except Exception as err:
            app.logger.error(f"Daily backup background thread error: {err}")
            try:
                backup_dir = str(get_backup_dir())
                info_file = os.path.join(backup_dir, ".last_backup_info.json")
                info = {
                    "last_backup_date": date.today().isoformat(),
                    "last_backup_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "failed",
                    "message": f"Daily backup error: {err}",
                    "file": None,
                }
                with open(info_file, "w", encoding="utf-8") as f:
                    json.dump(info, f, indent=2)
            except Exception:
                pass

    thread = threading.Thread(target=_worker, name="NoventraDailyBackupWorker", daemon=True)
    thread.start()


def get_backup_status() -> Dict[str, Any]:
    """
    Calculates backup status for display on the Dashboard.
    Returns:
    - label: 'Today', '1 day ago', 'X days ago', 'Failed', or 'Never'
    - days_ago: int
    - is_critical: bool (True if days_ago >= 2, failed, or never backed up, to display in red)
    - last_backup_time: formatted string
    - message: status details
    """
    backup_dir = str(get_backup_dir())
    info_file = os.path.join(backup_dir, ".last_backup_info.json")

    last_dt = None
    msg = ""
    status = "unknown"

    if os.path.isfile(info_file):
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            time_str = info.get("last_backup_time")
            msg = info.get("message", "")
            status = info.get("status", "unknown")
            if time_str:
                last_dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    # If status explicitly failed, display Failed on dashboard in red
    if status == "failed":
        return {
            "label": f"Failed ({last_dt.strftime('%I:%M %p') if last_dt else 'Today'})",
            "days_ago": 0,
            "is_critical": True,
            "last_backup_time": last_dt.strftime("%Y-%m-%d %I:%M %p") if last_dt else "Today",
            "message": f"Backup Failed: {msg}" if msg else "Daily backup execution failed."
        }

    # If info file missing or unreadable, check newest file in backup_dir
    if not last_dt and os.path.isdir(backup_dir):
        files = [
            os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
            if not f.startswith(".") and os.path.isfile(os.path.join(backup_dir, f))
        ]
        if files:
            newest = max(files, key=os.path.getmtime)
            last_dt = datetime.fromtimestamp(os.path.getmtime(newest))

    if not last_dt:
        return {
            "label": "Never",
            "days_ago": 999,
            "is_critical": True,
            "last_backup_time": "No backup found",
            "message": "No backups taken yet."
        }

    now = datetime.now()
    days_ago = (now.date() - last_dt.date()).days

    if days_ago == 0:
        label = f"Today ({last_dt.strftime('%I:%M %p')})"
    elif days_ago == 1:
        label = "1 day ago"
    else:
        label = f"{days_ago} days ago"

    is_critical = (days_ago >= 2)

    return {
        "label": label,
        "days_ago": days_ago,
        "is_critical": is_critical,
        "last_backup_time": last_dt.strftime("%Y-%m-%d %I:%M %p"),
        "message": msg
    }
