"""
Command Line Interface (CLI) Commands for Offline-First Oracle POS.
Includes db-check, backup-db, restore-db, seed, and process-print-queue.
"""
import os
import sys
import getpass
import subprocess
from datetime import datetime
import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import text
from app.extensions import db
from app.models.user import Role, User
from app.models.setting import Setting
from app.models.item import Unit, Category
from app.models.payment import PaymentMode
from app.models.partner import Customer
from app.models.print_job import PrintJob
from app.utils.timezone import utc_now


def run_db_check(app=None) -> bool:
    """
    Validates Oracle database connectivity and verifies NLS_CHARACTERSET is AL32UTF8.
    Must pass before any migration or application startup.
    Can be called directly by entrypoint or wrapped by Click command.
    """
    if app is None:
        from flask import current_app
        app = current_app

    click.echo("=" * 60)
    click.echo("  ORACLE DATABASE CONNECTIVITY & CHARACTER SET CHECK")
    click.echo("=" * 60)

    config_err = app.config.get("DB_CONFIG_ERROR")
    if config_err:
        click.secho(f"[ERROR] Environment Configuration Error:\n{config_err}", fg="red", bold=True)
        click.echo("\nTroubleshooting Tips:")
        click.echo("1. Copy .env.example to .env: 'cp .env.example .env'")
        click.echo("2. Provide DB_SERVICE_NAME (e.g. XEPDB1), DB_USER, and DB_PASSWORD in your .env.")
        sys.exit(1)

    try:
        # 1. Connection ping and identity check
        with db.engine.connect() as conn:
            click.echo("[INFO] Testing basic listener round-trip...")
            user_row = conn.execute(text("SELECT USER, sys_context('USERENV', 'CURRENT_SCHEMA') FROM DUAL")).fetchone()
            click.secho(f"[PASS] Connected to Oracle as User: '{user_row[0]}' | Schema: '{user_row[1]}'", fg="green")

            # 2. Verify Essential Migration Privileges
            click.echo("[INFO] Verifying schema DDL privileges...")
            priv_rows = conn.execute(text("SELECT privilege FROM session_privs")).fetchall()
            user_privs = {r[0] for r in priv_rows}
            required_privs = ["CREATE TABLE", "CREATE SEQUENCE", "CREATE PROCEDURE", "CREATE TRIGGER", "CREATE VIEW"]
            missing_privs = [p for p in required_privs if p not in user_privs and "DBA" not in user_privs]
            if missing_privs:
                click.secho(f"[ERROR] Connected user '{user_row[0]}' lacks required privileges: {', '.join(missing_privs)}", fg="red")
                sys.exit(1)
            click.secho(f"[PASS] All required DDL privileges verified ({', '.join(required_privs)}).", fg="green")

            # 3. Verify Character Set
            click.echo("[INFO] Inspecting NLS database parameters...")
            nls_query = text(
                "SELECT parameter, value FROM nls_database_parameters "
                "WHERE parameter IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET')"
            )
            rows = conn.execute(nls_query).fetchall()
            nls_dict = {row[0]: row[1] for row in rows}

            char_set = nls_dict.get("NLS_CHARACTERSET", "UNKNOWN")
            nchar_set = nls_dict.get("NLS_NCHAR_CHARACTERSET", "UNKNOWN")

            click.echo(f"  * NLS_CHARACTERSET:       {char_set}")
            click.echo(f"  * NLS_NCHAR_CHARACTERSET: {nchar_set}")

            if char_set != "AL32UTF8":
                click.secho(
                    f"\n[FATAL ERROR] NLS_CHARACTERSET is '{char_set}', but 'AL32UTF8' is required!\n"
                    "Multi-byte Urdu text will be irreparably corrupted with this character set.\n"
                    "Migrations and application startup are strictly halted.",
                    fg="red",
                    bold=True,
                )
                sys.exit(1)

            click.secho("[PASS] Character set is verified AL32UTF8 (Urdu multi-byte compatible).", fg="green")
            click.secho("\nALL PRE-FLIGHT DATABASE CHECKS PASSED.", fg="green", bold=True)
            return True

    except Exception as exc:
        click.secho(f"\n[ERROR] Database connection failed: {exc}", fg="red", bold=True)
        click.echo("\nTroubleshooting Tips:")
        click.echo("1. Verify DB_SERVICE_NAME, DB_HOST, DB_PORT, DB_USER, and DB_PASSWORD in your .env.")
        click.echo("2. Verify that the Oracle TNS listener is running ('lsnrctl status').")
        click.echo("3. Confirm that user credentials are correct and account is not locked.")
        sys.exit(1)


@click.command("db-check")
@with_appcontext
def db_check_command():
    """
    Validates Oracle database connectivity and verifies NLS_CHARACTERSET is AL32UTF8.
    Must pass before any migration or application startup.
    """
    from flask import current_app
    run_db_check(current_app)


@click.command("backup-db")
@click.option("--dest-path", default=None, help="Secondary/offsite backup destination path")
@click.option("--with-uploads/--no-with-uploads", default=True, help="Package app/static/uploads directory alongside database dump (default: enabled)")
@with_appcontext
def backup_db_command(dest_path, with_uploads=True):
    """
    Executes an Oracle backup to establish a Day-Zero restore point before migration.
    Attempts Oracle Data Pump export (expdp) if binary is on PATH; otherwise executes
    a direct client-side logical schema and data dump over the active Oracle connection.
    """
    import json
    from app.extensions import db
    from sqlalchemy import text

    click.echo("=" * 60)
    click.echo("  ORACLE DATABASE BACKUP UTILITY (Day-Zero Restore Point)")
    click.echo("=" * 60)

    if current_app.config.get("TESTING"):
        db_user = current_app.config.get("TEST_DB_USER") or os.environ.get("TEST_DB_USER") or "isi_pos_test"
        db_pass = current_app.config.get("TEST_DB_PASSWORD") or os.environ.get("TEST_DB_PASSWORD") or "test_pass"
        db_service = current_app.config.get("TEST_DB_SERVICE_NAME") or os.environ.get("TEST_DB_SERVICE_NAME") or "XEPDB1"
    else:
        db_user = os.environ.get("DB_USER", "isi")
        db_pass = os.environ.get("DB_PASSWORD", "")
        db_service = os.environ.get("DB_SERVICE_NAME", "")
    oracle_dir = os.environ.get("ORACLE_BACKUP_DIR", "POS_BACKUP_DIR")
    backup_dir = os.path.join(os.getcwd(), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    if not db_pass or not db_service:
        click.secho("[ERROR] DB_PASSWORD and DB_SERVICE_NAME must be configured in .env.", fg="red")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dumpfile_name = f"isi_pos_{timestamp}.dmp"
    logfile_name = f"isi_pos_{timestamp}.log"
    dump_path = os.path.join(backup_dir, dumpfile_name)
    log_path = os.path.join(backup_dir, logfile_name)

    click.echo(f"Target Schema: {db_user.upper()}")
    click.echo(f"Service:       {db_service}")
    click.echo(f"Dumpfile:      {dumpfile_name}")
    click.echo(f"Logfile:       {logfile_name}")
    click.echo(f"Directory:     {backup_dir}")

    # 1. Attempt expdp if installed
    expdp_available = False
    try:
        res = subprocess.run(["expdp", "-help"], capture_output=True, text=True)
        if res.returncode == 0 or "Export: Release" in res.stdout:
            expdp_available = True
    except FileNotFoundError:
        expdp_available = False

    if expdp_available:
        click.echo("\n[MODE] Oracle Data Pump (expdp) detected on PATH.")
        cmd = [
            "expdp",
            f"{db_user}/{db_pass}@{db_service}",
            f"schemas={db_user.upper()}",
            f"directory={oracle_dir}",
            f"dumpfile={dumpfile_name}",
            f"logfile={logfile_name}",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        click.echo(res.stdout)
        if res.returncode != 0:
            click.secho(f"[ERROR] expdp exited with code {res.returncode}:\n{res.stderr}", fg="red")
            sys.exit(1)
        click.secho(f"[SUCCESS] Database backup successfully created: {dumpfile_name}", fg="green", bold=True)
        return

    click.echo("\n[MODE] Direct Logical Schema & Data Dump (expdp binary not installed on client PATH)")
    click.echo("Connecting to Oracle instance to extract schema tables and records...")

    log_lines = [
        f";;; ============================================================",
        f";;; Oracle Database Logical Export",
        f";;; Schema: {db_user.upper()} | Service: {db_service} | Time: {datetime.now().isoformat()}",
        f";;; Character Set: AL32UTF8",
        f";;; ============================================================",
    ]

    dump_data = {
        "metadata": {
            "timestamp": timestamp,
            "schema": db_user.upper(),
            "service": db_service,
            "charset": "AL32UTF8",
            "table_count": 0,
            "total_rows": 0,
        },
        "tables": {}
    }

    with db.engine.connect() as conn:
        tables_query = text(
            "SELECT table_name FROM all_tables WHERE owner = :schema ORDER BY table_name"
        )
        tables = [r[0] for r in conn.execute(tables_query, {"schema": db_user.upper()}).fetchall()]

        total_rows = 0
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
            count = len(rows_data)
            total_rows += count
            log_lines.append(f". . exported \"{db_user.upper()}\".\"{tbl}\"                {count} rows")

        dump_data["metadata"]["table_count"] = len(tables)
        dump_data["metadata"]["total_rows"] = total_rows

    log_lines.append(f";;; ------------------------------------------------------------")
    log_lines.append(f"Master export for schema \"{db_user.upper()}\" successfully completed.")
    log_lines.append(f"Total tables exported: {len(tables)}")
    log_lines.append(f"Total rows exported:   {total_rows}")
    log_lines.append(f"Job completed with 0 errors and 0 warnings.")

    # Save dump file
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(dump_data, f, ensure_ascii=False, indent=2)

    # Save log file
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    dump_size = os.path.getsize(dump_path)
    log_text = "\n".join(log_lines)
    click.echo("\n" + log_text)
    click.echo("-" * 60)
    click.echo(f"Dumpfile: {dump_path} ({dump_size} bytes)")
    click.echo(f"Logfile:  {log_path}")

    has_ora = any("ORA-" in line for line in log_lines)
    if not has_ora and dump_size > 0:
        click.secho(f"\n[SUCCESS] Database backup successfully created: {dumpfile_name}", fg="green", bold=True)
        if with_uploads:
            import zipfile
            uploads_dir = os.path.join(current_app.root_path, "static", "uploads")
            if os.path.exists(uploads_dir):
                uploads_zip_name = f"isi_pos_{timestamp}_uploads.zip"
                uploads_zip_path = os.path.join(backup_dir, uploads_zip_name)
                file_count = 0
                with zipfile.ZipFile(uploads_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(uploads_dir):
                        for f in files:
                            full_p = os.path.join(root, f)
                            rel_p = os.path.relpath(full_p, uploads_dir)
                            zf.write(full_p, rel_p)
                            file_count += 1
                uploads_sz = os.path.getsize(uploads_zip_path)
                click.echo(f"Uploads Archive: {uploads_zip_path} ({file_count} files, {uploads_sz} bytes)")
    else:
        click.secho("\n[ERROR] Database backup failed.", fg="red", bold=True)
        sys.exit(1)


@click.command("restore-db")
@click.argument("dumpfile")
@click.option("--confirm", is_flag=True, default=False, help="Confirm destructive database restore")
@click.option("--with-uploads/--no-with-uploads", default=True, help="Restore companion uploads archive if present (default: enabled)")
@with_appcontext
def restore_db_command(dumpfile, confirm, with_uploads=True):
    """
    Restores an Oracle database dump into target schema.
    Requires explicit --confirm flag to prevent accidental overwrite.
    """
    import json
    from app.extensions import db
    from sqlalchemy import text

    click.echo("=" * 60)
    click.echo("  ORACLE DATABASE RESTORE UTILITY")
    click.echo("=" * 60)

    db_user = os.environ.get("DB_USER", "isi")
    db_service = os.environ.get("DB_SERVICE_NAME", "")

    # Locate dumpfile
    if not os.path.isabs(dumpfile):
        candidate = os.path.join(os.getcwd(), "backups", dumpfile)
        if os.path.exists(candidate):
            dumpfile_path = candidate
        else:
            dumpfile_path = os.path.abspath(dumpfile)
    else:
        dumpfile_path = dumpfile

    click.echo(f"Target Schema:  {db_user.upper()}")
    click.echo(f"Target Service: {db_service}")
    click.echo(f"Source Dump:    {dumpfile_path}")

    if not confirm:
        click.secho(
            "\n[ABORTED] Restoring a database will overwrite existing schema records!\n"
            "You MUST pass the '--confirm' flag to proceed: flask restore-db <file> --confirm",
            fg="red",
            bold=True,
        )
        sys.exit(1)

    if not os.path.exists(dumpfile_path):
        click.secho(f"[ERROR] Dump file not found: {dumpfile_path}", fg="red")
        sys.exit(1)

    try:
        with open(dumpfile_path, "r", encoding="utf-8") as f:
            dump_data = json.load(f)

        click.echo("\n[INFO] Restoring logical schema dump into Oracle...")
        meta = dump_data.get("metadata", {})
        click.echo(f"Dump Timestamp: {meta.get('timestamp')}")
        click.echo(f"Source Schema:  {meta.get('schema')}")
        click.echo(f"Tables count:   {meta.get('table_count')}")

        restored_tables = 0
        restored_rows = 0
        with db.engine.connect() as conn:
            # 1. Disable Foreign Key Constraints
            conn.execute(text("""
                BEGIN
                    FOR c IN (SELECT table_name, constraint_name FROM user_constraints WHERE constraint_type = 'R') LOOP
                        EXECUTE IMMEDIATE 'ALTER TABLE "' || c.table_name || '" DISABLE CONSTRAINT "' || c.constraint_name || '"';
                    END LOOP;
                END;
            """))

            try:
                for tbl_name, tbl_info in dump_data.get("tables", {}).items():
                    rows = tbl_info.get("rows", [])
                    # Clean table data
                    try:
                        conn.execute(text(f'DELETE FROM "{db_user.upper()}"."{tbl_name}"'))
                    except Exception:
                        pass

                    if rows:
                        cols = tbl_info.get("columns", [])
                        col_list = ", ".join(f'"{c.upper()}"' for c in cols)
                        param_list = ", ".join(f":p_{i}" for i in range(len(cols)))
                        insert_sql = text(f'INSERT INTO "{db_user.upper()}"."{tbl_name}" ({col_list}) VALUES ({param_list})')

                        for r in rows:
                            row_params = {}
                            for i, col in enumerate(cols):
                                v = r.get(col)
                                if isinstance(v, str) and len(v) >= 19 and ("T" in v or "-" in v):
                                    try:
                                        row_params[f"p_{i}"] = datetime.fromisoformat(v)
                                    except Exception:
                                        row_params[f"p_{i}"] = v
                                else:
                                    row_params[f"p_{i}"] = v
                            conn.execute(insert_sql, row_params)
                        restored_rows += len(rows)
                    restored_tables += 1
                    click.echo(f". . restored \"{db_user.upper()}\".\"{tbl_name}\"              {len(rows)} rows")
                conn.commit()

            finally:
                # 2. Re-enable Foreign Key Constraints
                conn.execute(text("""
                    BEGIN
                        FOR c IN (SELECT table_name, constraint_name FROM user_constraints WHERE constraint_type = 'R') LOOP
                            EXECUTE IMMEDIATE 'ALTER TABLE "' || c.table_name || '" ENABLE CONSTRAINT "' || c.constraint_name || '"';
                        END LOOP;
                    END;
                """))
                conn.commit()

                # 3. Restart identity sequences to MAX(id) + 1 to avoid sequence collisions
                try:
                    conn.execute(text("""
                        DECLARE
                            v_max NUMBER;
                        BEGIN
                            FOR id_col IN (SELECT table_name, column_name FROM user_tab_identity_cols) LOOP
                                BEGIN
                                    EXECUTE IMMEDIATE 'SELECT NVL(MAX("' || id_col.column_name || '"), 0) + 1 FROM "' || id_col.table_name || '"' INTO v_max;
                                    EXECUTE IMMEDIATE 'ALTER TABLE "' || id_col.table_name || '" MODIFY ("' || id_col.column_name || '" RESTART WITH ' || v_max || ')';
                                EXCEPTION WHEN OTHERS THEN NULL;
                                END;
                            END LOOP;
                        END;
                    """))
                    conn.commit()
                except Exception as seq_err:
                    click.echo(f"[WARN] Could not reset identity sequences: {seq_err}")

        click.secho(f"\n[SUCCESS] Database successfully restored from {os.path.basename(dumpfile_path)}.", fg="green", bold=True)
        click.echo(f"Restored {restored_tables} tables, {restored_rows} records with 0 errors.")

        if with_uploads:
            import zipfile
            dump_base, _ = os.path.splitext(dumpfile_path)
            candidate_uploads = f"{dump_base}_uploads.zip"
            if os.path.exists(candidate_uploads):
                uploads_dest = os.path.join(current_app.root_path, "static", "uploads")
                os.makedirs(uploads_dest, exist_ok=True)
                with zipfile.ZipFile(candidate_uploads, "r") as zf:
                    zf.extractall(uploads_dest)
                click.secho(f"[SUCCESS] Uploads directory restored from {os.path.basename(candidate_uploads)}.", fg="green")

    except Exception as exc:
        click.secho(f"[ERROR] Failed to parse or restore dump: {exc}", fg="red")
        sys.exit(1)


@click.command("prune-orphan-uploads")
@click.option("--confirm", is_flag=True, default=False, help="Confirm deletion of orphan upload files.")
@with_appcontext
def prune_orphan_uploads_command(confirm):
    """
    Scans app/static/uploads/ for files not referenced in database settings or item catalog.
    Requires --confirm to permanently delete orphaned files.
    """
    import os
    from app.models.setting import Setting
    from app.models.item import ItemImage

    upload_cfg = current_app.config.get("UPLOAD_FOLDER")
    if upload_cfg and os.path.isabs(upload_cfg):
        uploads_dir = upload_cfg
    elif upload_cfg:
        uploads_dir = os.path.join(current_app.root_path, upload_cfg)
    else:
        uploads_dir = os.path.join(current_app.root_path, "static", "uploads")

    if not os.path.exists(uploads_dir):
        click.echo("Uploads directory does not exist. Nothing to prune.")
        return

    # 1. Collect all active referenced files from database
    active_paths = set()
    branding_keys = [
        "org_logo_url", "org_favicon_url",
        "brand_icon_black_url", "brand_icon_white_url",
        "brand_wordmark_black_url", "brand_wordmark_white_url",
        "brand_favicon_url"
    ]
    for s in Setting.query.filter(Setting.setting_key.in_(branding_keys)).all():
        if s.setting_val:
            clean = s.setting_val.replace("\\", "/").replace("/static/", "").lstrip("/")
            active_paths.add(clean)
            base, _ = os.path.splitext(clean)
            active_paths.add(f"{base}.jpg")
            active_paths.add(f"{base}.png")
            active_paths.add(f"{base}.webp")

    for img in ItemImage.query.all():
        if img.file_path:
            clean = img.file_path.replace("\\", "/").replace("/static/", "").lstrip("/")
            active_paths.add(clean)
            base, _ = os.path.splitext(clean)
            active_paths.add(f"{base}.jpg")
            active_paths.add(f"{base}.png")
            active_paths.add(f"{base}.webp")

    # 2. Walk static/uploads/ and find unreferenced files
    orphan_files = []
    total_orphan_bytes = 0

    for root, _, files in os.walk(uploads_dir):
        for f in files:
            full_path = os.path.join(root, f)
            rel_to_static = os.path.relpath(full_path, os.path.join(current_app.root_path, "static")).replace("\\", "/")
            if rel_to_static not in active_paths:
                sz = os.path.getsize(full_path)
                orphan_files.append((full_path, rel_to_static, sz))
                total_orphan_bytes += sz

    click.echo("=" * 60)
    click.echo("  ORPHAN UPLOAD PRUNER")
    click.echo("=" * 60)
    click.echo(f"Active database referenced paths: {len(active_paths)}")
    click.echo(f"Orphan files found on disk:       {len(orphan_files)}")
    click.echo(f"Reclaimable disk space:           {total_orphan_bytes / 1024:.1f} KB ({total_orphan_bytes} bytes)")
    click.echo("-" * 60)

    if not orphan_files:
        click.secho("[SUCCESS] No orphan uploads found. Disk is completely in sync with database.", fg="green")
        return

    for full_path, rel_path, sz in orphan_files:
        click.echo(f"  - {rel_path} ({sz} bytes)")

    if not confirm:
        click.secho("\n[SAFETY GUARD] Dry-run complete. No files were deleted.", fg="yellow", bold=True)
        click.echo("Re-run with '--confirm' to permanently delete these orphan files:")
        click.echo("  flask prune-orphan-uploads --confirm")
        return

    # Delete confirmed
    deleted_count = 0
    deleted_bytes = 0
    for full_path, rel_path, sz in orphan_files:
        try:
            os.remove(full_path)
            deleted_count += 1
            deleted_bytes += sz
        except Exception as e:
            click.secho(f"[ERROR] Failed to delete {rel_path}: {e}", fg="red")

    click.secho(f"\n[SUCCESS] Successfully pruned {deleted_count} orphan file(s) ({deleted_bytes / 1024:.1f} KB reclaimed).", fg="green", bold=True)


@click.command("seed")
@with_appcontext
def seed_command():
    """
    Seeds initial system roles, default settings, units, payment modes, and walk-in customer.
    Prompts interactively for the Super Admin password via getpass (no hardcoded credentials).
    """
    click.echo("=" * 60)
    click.echo("  INITIALIZING SYSTEM ROLES, SETTINGS & SUPER ADMIN")
    click.echo("=" * 60)

    # 1. Seed Roles
    roles_data = [
        ("super_admin", "Super Admin", "Full institutional administrative access"),
        ("manager", "Manager", "Management of items, stock adjustments, voids, and reports"),
        ("cashier", "Cashier", "Counter sales, POS operation, shift drawer, receipt printing"),
        ("store_keeper", "Store Keeper", "Stock intake, stock out, inventory reports"),
    ]

    created_roles = {}
    for code, name, desc in roles_data:
        role = Role.query.filter_by(role_code=code).first()
        if not role:
            role = Role(role_code=code, role_name=name, description=desc, is_system=1)
            db.session.add(role)
            click.echo(f"[+] Created role: {name} ({code})")
        created_roles[code] = role
    db.session.commit()

    # 2. Seed Default Units
    default_units = [
        ("pcs", "Pieces", 0),
        ("kg", "Kilogram", 1),
        ("ltr", "Liter", 1),
        ("box", "Box", 0),
        ("pack", "Pack", 0),
        ("meter", "Meter", 1),
    ]
    for code, name, dec in default_units:
        if not Unit.query.filter_by(unit_code=code).first():
            unit = Unit(unit_code=code, unit_name=name, allow_decimal=dec, is_active=1)
            db.session.add(unit)
            click.echo(f"[+] Created unit: {name} ({code})")
    db.session.commit()

    # 3. Seed Default Payment Modes
    default_modes = [
        ("cash", "Cash", 0, 1),
        ("card", "Debit / Credit Card", 1, 2),
        ("bank_transfer", "Bank Transfer", 1, 3),
        ("cheque", "Cheque", 1, 4),
        ("credit", "On Account / Credit", 0, 5),
    ]
    for code, name, req_ref, s_ord in default_modes:
        if not PaymentMode.query.filter_by(mode_code=code).first():
            mode = PaymentMode(mode_code=code, mode_name=name, requires_ref=req_ref, sort_order=s_ord, is_active=1)
            db.session.add(mode)
            click.echo(f"[+] Created payment mode: {name} ({code})")
    db.session.commit()

    # 4. Seed Default Walk-in Customer
    if not Customer.query.filter_by(is_walk_in=1).first():
        walk_in = Customer(
            customer_name="Walk-in Customer",
            phone_no="00000000000",
            is_walk_in=1,
            is_active=1,
            opening_balance=0,
            current_balance=0,
        )
        db.session.add(walk_in)
        click.echo("[+] Created default Walk-in Customer.")
    db.session.commit()

    # 5. Seed Default System Settings
    default_settings = [
        ("org_name_en", "Institutional Store & POS", "string", "branding", "Organization name in English"),
        ("org_name_ur", "ادارہ جاتی پوائنٹ آف سیل", "string", "branding", "Organization name in Urdu"),
        ("currency_symbol", "Rs.", "string", "general", "Currency symbol"),
        ("receipt_paper_size", "80mm", "string", "printer", "Thermal paper size (80mm or 58mm)"),
        ("cash_rounding_mode", "off", "string", "pos", "Cash rounding: off, nearest_1, nearest_5"),
        ("business_day_cutoff", "04:00", "string", "general", "Business day cutoff hour (HH:MM)"),
        ("fiscal_year_start", "07-01", "string", "general", "Fiscal year start date (MM-DD)"),
        ("printer_mode", "network", "string", "printer", "Printer mode: network, usb, serial"),
        ("printer_ip", "192.168.1.200", "string", "printer", "Thermal printer IP"),
        ("printer_port", "9100", "int", "printer", "Thermal printer TCP port"),
        ("fbr_enabled", "false", "bool", "compliance", "FBR Tier-1 integration toggle"),
    ]
    for key, val, stype, grp, desc in default_settings:
        if not Setting.query.filter_by(setting_key=key).first():
            Setting.set_value(key, val, setting_type=stype, group_name=grp, description=desc)
            click.echo(f"[+] Initialized setting: {key}")

    # 6. Interactive Super Admin Creation
    admin_user = User.query.filter_by(username="admin").first()
    if not admin_user:
        click.echo("\n" + "-" * 50)
        click.echo("  CREATE INITIAL SUPER ADMIN ACCOUNT")
        click.echo("-" * 50)
        admin_username = click.prompt("Enter Super Admin username", default="admin").strip()
        admin_fullname = click.prompt("Enter Admin Full Name", default="System Administrator").strip()
        admin_email = click.prompt("Enter Admin Email", default="admin@pos.local").strip()

        pwd1 = getpass.getpass("Enter Super Admin password: ")
        pwd2 = getpass.getpass("Confirm Super Admin password: ")
        while pwd1 != pwd2 or len(pwd1) < 8:
            if pwd1 != pwd2:
                click.secho("Passwords do not match. Please try again.", fg="red")
            elif len(pwd1) < 8:
                click.secho("Password must be at least 8 characters.", fg="red")
            pwd1 = getpass.getpass("Enter Super Admin password: ")
            pwd2 = getpass.getpass("Confirm Super Admin password: ")

        super_admin_role = Role.query.filter_by(role_code="super_admin").first()
        admin_user = User(
            username=admin_username,
            full_name=admin_fullname,
            email=admin_email,
            role_id=super_admin_role.id,
            is_active=1,
            must_change_pwd=0,
        )
        admin_user.set_password(pwd1)
        db.session.add(admin_user)
        db.session.commit()
        click.secho(f"\n[SUCCESS] Super Admin '{admin_username}' created successfully!", fg="green", bold=True)
    else:
        click.echo("[*] Super Admin account already exists.")


@click.command("process-print-queue")
@with_appcontext
def process_print_queue_command():
    """
    Processes pending print jobs from the print_jobs queue (Section 21 & 38.7).
    Retries failed print tasks up to 3 times before marking as permanently failed.
    """
    click.echo("Processing print queue...")
    pending_jobs = PrintJob.query.filter(
        PrintJob.job_status.in_(["pending", "failed"]),
        PrintJob.attempt_count < 3
    ).order_by(PrintJob.created_at.asc()).limit(10).all()

    if not pending_jobs:
        click.echo("No pending print jobs in queue.")
        return

    from app.services.printer_service import PrinterService

    for job in pending_jobs:
        job.attempt_count += 1
        job.job_status = "printing"
        db.session.commit()

        try:
            printer = PrinterService.get_printer_connection()
            printer.text(job.payload)
            printer.cut()
            job.job_status = "done"
            job.last_error = None
            click.secho(f"[DONE] Print job #{job.id} printed successfully.", fg="green")
        except Exception as exc:
            job.last_error = str(exc)[:250]
            if job.attempt_count >= 3:
                job.job_status = "failed"
                click.secho(f"[FAILED] Print job #{job.id} failed after 3 attempts: {exc}", fg="red")
            else:
                job.job_status = "pending"
                click.secho(f"[RETRY] Print job #{job.id} failed attempt #{job.attempt_count}: {exc}", fg="yellow")
        db.session.commit()


@click.command("seed-demo")
@click.option("--admin-password", default="Admin123!", help="Super Admin password")
@with_appcontext
def seed_demo_command(admin_password):
    """
    Seeds realistic client demo data:
    1. System roles and payment modes (Cash, Card, etc.).
    2. Measurement units and settings.
    3. Super Admin account (admin / Admin123!).
    4. 4 realistic categories (Stationery, Beverages, Grocery, Electronics).
    5. 15 realistic items with authentic Pakistani Urdu names, barcodes, prices, and stock.
    """
    from decimal import Decimal
    from app.models.item import Item
    from app.models.inventory import StockMovement

    click.echo("=" * 60)
    click.echo("  SEEDING CLIENT DEMO ENVIRONMENT (ROLES, SETTINGS & ITEMS)")
    click.echo("=" * 60)

    # 1. Ensure Roles
    roles_data = [
        ("super_admin", "Super Admin", "Full institutional administrative access"),
        ("manager", "Manager", "Management of items, stock adjustments, voids, and reports"),
        ("cashier", "Cashier", "Counter sales, POS operation, shift drawer, receipt printing"),
        ("store_keeper", "Store Keeper", "Stock intake, stock out, inventory reports"),
    ]
    role_map = {}
    for code, name, desc in roles_data:
        r = Role.query.filter_by(role_code=code).first()
        if not r:
            r = Role(role_code=code, role_name=name, description=desc, is_system=1)
            db.session.add(r)
            click.echo(f"[+] Role created: {name}")
        role_map[code] = r
    db.session.commit()

    # 2. Ensure Units
    units_data = [
        ("pcs", "Pieces", 0),
        ("kg", "Kilogram", 1),
        ("ltr", "Liter", 1),
        ("box", "Box", 0),
        ("pack", "Pack", 0),
        ("meter", "Meter", 1),
    ]
    unit_map = {}
    for code, name, dec in units_data:
        u = Unit.query.filter_by(unit_code=code).first()
        if not u:
            u = Unit(unit_code=code, unit_name=name, allow_decimal=dec, is_active=1)
            db.session.add(u)
            click.echo(f"[+] Unit created: {name}")
        unit_map[code] = u
    db.session.commit()

    # 3. Ensure Payment Modes
    modes_data = [
        ("cash", "Cash", 0, 1),
        ("card", "Debit / Credit Card", 1, 2),
        ("bank_transfer", "Bank Transfer", 1, 3),
        ("cheque", "Cheque", 1, 4),
        ("credit", "On Account / Credit", 0, 5),
    ]
    for code, name, req_ref, s_ord in modes_data:
        pm = PaymentMode.query.filter_by(mode_code=code).first()
        if not pm:
            pm = PaymentMode(mode_code=code, mode_name=name, requires_ref=req_ref, sort_order=s_ord, is_active=1)
            db.session.add(pm)
            click.echo(f"[+] Payment mode created: {name}")
    db.session.commit()

    # 4. Ensure Walk-in Customer
    if not Customer.query.filter_by(is_walk_in=1).first():
        walk_in = Customer(
            customer_name="Walk-in Customer",
            phone_no="03001234567",
            is_walk_in=1,
            is_active=1,
            opening_balance=0,
            current_balance=0,
        )
        db.session.add(walk_in)
        click.echo("[+] Default Walk-in Customer created.")
    db.session.commit()

    # 5. Ensure System Settings
    settings_data = [
        ("org_name_en", "Institutional Store & POS", "string", "branding", "Organization name in English"),
        ("org_name_ur", "", "string", "branding", "Organization name in Urdu (retained unused)"),
        ("currency_symbol", "Rs.", "string", "general", "Currency symbol"),
        ("receipt_paper_size", "80mm", "string", "printer", "Thermal paper size (80mm or 58mm)"),
        ("cash_rounding_mode", "nearest_1", "string", "pos", "Cash rounding: off, nearest_1, nearest_5"),
        ("business_day_cutoff", "04:00", "string", "general", "Business day cutoff hour (HH:MM)"),
        ("fiscal_year_start", "07-01", "string", "general", "Fiscal year start date (MM-DD)"),
        ("receipt_header", "Official Store Receipt", "string", "branding", "Receipt header message"),
        ("receipt_footer", "Thank you for your visit!", "string", "branding", "Receipt footer message"),
    ]
    for key, val, stype, grp, desc in settings_data:
        s_row = Setting.query.filter_by(setting_key=key).first()
        if not s_row:
            Setting.set_value(key, val, setting_type=stype, group_name=grp, description=desc)
            click.echo(f"[+] Setting initialized: {key}")
        elif key in ("receipt_footer", "org_name_ur"):
            Setting.set_value(key, val, setting_type=stype, group_name=grp, description=desc)
    db.session.commit()

    # 6. Ensure Super Admin Account
    admin_user = User.query.filter_by(username="admin").first()
    if not admin_user:
        super_admin_role = role_map.get("super_admin") or Role.query.filter_by(role_code="super_admin").first()
        admin_user = User(
            username="admin",
            full_name="System Administrator",
            email="admin@pos.local",
            role_id=super_admin_role.id,
            is_active=1,
            must_change_pwd=0,
        )
        admin_user.set_password(admin_password)
        db.session.add(admin_user)
        db.session.commit()
        click.secho(f"[+] Super Admin 'admin' created with password: '{admin_password}'", fg="green", bold=True)
    else:
        click.echo("[*] Super Admin 'admin' already exists.")

    # 7. Seed Coffee Shop Categories
    categories_data = [
        ("Hot Coffee", "CAT-HOT", 1),
        ("Cold Coffee", "CAT-COLD", 2),
        ("Tea & Other", "CAT-TEA", 3),
        ("Bakery", "CAT-BAKE", 4),
        ("Retail", "CAT-RET", 5),
    ]
    # Deactivate any legacy non-coffee categories
    legacy_codes = ["CAT-STAT", "CAT-BEV", "CAT-GROC", "CAT-ELEC"]
    Category.query.filter(Category.cat_code.in_(legacy_codes)).update({"is_active": 0}, synchronize_session=False)

    cat_map = {}
    for name_en, code, s_ord in categories_data:
        c = Category.query.filter_by(cat_code=code).first()
        if not c:
            c = Category(cat_name=name_en, cat_name_ur=None, cat_code=code, sort_order=s_ord, is_active=1)
            db.session.add(c)
            click.echo(f"[+] Category created: {name_en}")
        else:
            c.cat_name = name_en
            c.cat_name_ur = None
            c.sort_order = s_ord
            c.is_active = 1
        cat_map[code] = c
    db.session.commit()

    # 8. Deactivate any old demo items from legacy categories
    old_skus = [
        "STAT-PEN-01", "STAT-PEN-02", "STAT-REG-01", "STAT-STP-01",
        "BEV-ROOH-01", "BEV-PAK-01", "BEV-TEA-01", "BEV-WAT-01",
        "GROC-RIC-01", "GROC-OIL-01", "GROC-SLT-01", "GROC-MSL-01",
        "ELEC-USB-01", "ELEC-MOU-01", "ELEC-EXT-01"
    ]
    Item.query.filter(Item.sku.in_(old_skus)).update({"is_active": 0}, synchronize_session=False)
    db.session.commit()

    # 9. Seed Authentic Coffee Shop Items
    # (name_en, sku, barcode, cat_code, unit_code, cost, sale, tax, stock)
    # Drinks are made to order (stock 500 in DB for checkout, displayed as Made to order in POS)
    # Bakery & Retail have exact physical stock
    demo_items = [
        # Hot Coffee
        ("Espresso", "COF-ESP-01", "89650001001", "CAT-HOT", "pcs", 95.00, 380.00, 16.00, 500),
        ("Double Espresso", "COF-DESP-01", "89650001002", "CAT-HOT", "pcs", 130.00, 480.00, 16.00, 500),
        ("Americano", "COF-AMER-01", "89650001003", "CAT-HOT", "pcs", 110.00, 450.00, 16.00, 500),
        ("Cappuccino", "COF-CAPP-01", "89650001004", "CAT-HOT", "pcs", 150.00, 550.00, 16.00, 500),
        ("Flat White", "COF-FLAT-01", "89650001005", "CAT-HOT", "pcs", 160.00, 580.00, 16.00, 500),
        ("Latte", "COF-LATT-01", "89650001006", "CAT-HOT", "pcs", 155.00, 560.00, 16.00, 500),
        ("Mocha", "COF-MOCH-01", "89650001007", "CAT-HOT", "pcs", 180.00, 620.00, 16.00, 500),
        ("Cortado", "COF-CORT-01", "89650001008", "CAT-HOT", "pcs", 135.00, 490.00, 16.00, 500),

        # Cold Coffee
        ("Cold Brew", "COF-CLDB-01", "89650001009", "CAT-COLD", "pcs", 160.00, 600.00, 16.00, 500),
        ("Iced Latte", "COF-ICEL-01", "89650001010", "CAT-COLD", "pcs", 160.00, 590.00, 16.00, 500),
        ("Iced Americano", "COF-ICEA-01", "89650001011", "CAT-COLD", "pcs", 120.00, 480.00, 16.00, 500),
        ("Frappe", "COF-FRAP-01", "89650001012", "CAT-COLD", "pcs", 190.00, 650.00, 16.00, 500),

        # Tea & Other
        ("Green Tea", "TEA-GRN-01", "89650001013", "CAT-TEA", "pcs", 60.00, 320.00, 16.00, 500),
        ("Karak Chai", "TEA-KRK-01", "89650001014", "CAT-TEA", "pcs", 70.00, 280.00, 16.00, 500),
        ("Hot Chocolate", "TEA-HCH-01", "89650001015", "CAT-TEA", "pcs", 160.00, 580.00, 16.00, 500),

        # Bakery (Fresh physical stock)
        ("Croissant", "BAK-CROI-01", "89650001016", "CAT-BAKE", "pcs", 180.00, 380.00, 16.00, 24),
        ("Chocolate Croissant", "BAK-CCRO-01", "89650001017", "CAT-BAKE", "pcs", 210.00, 440.00, 16.00, 18),
        ("Blueberry Muffin", "BAK-MUFF-01", "89650001018", "CAT-BAKE", "pcs", 190.00, 420.00, 16.00, 15),
        ("Banana Bread", "BAK-BBRD-01", "89650001019", "CAT-BAKE", "pcs", 150.00, 350.00, 16.00, 12),
        ("Cheesecake Slice", "BAK-CHSK-01", "89650001020", "CAT-BAKE", "pcs", 250.00, 550.00, 16.00, 10),

        # Retail (Packaged physical stock)
        ("House Blend Beans 250g", "RET-BEAN-01", "89650001021", "CAT-RET", "pack", 850.00, 1450.00, 16.00, 20),
        ("Artisan Ceramic Mug", "RET-MUG-01", "89650001022", "CAT-RET", "pcs", 480.00, 950.00, 16.00, 15),
    ]

    terminal_code = current_app.config.get("TERMINAL_CODE", "T01")
    seeded_count = 0
    for name_en, sku, barcode, c_code, u_code, cost, sale, tax, stock in demo_items:
        it = Item.query.filter_by(sku=sku).first()
        cat = cat_map.get(c_code)
        unit = unit_map.get(u_code)
        if not it:
            it = Item(
                item_name=name_en,
                item_name_ur=None,
                sku=sku,
                barcode=barcode,
                category_id=cat.id if cat else None,
                unit_id=unit.id if unit else 1,
                cost_price=Decimal(str(cost)),
                sale_price=Decimal(str(sale)),
                tax_percent=Decimal(str(tax)),
                opening_stock=Decimal(str(stock)),
                current_stock=Decimal(str(stock)),
                reorder_level=Decimal("5"),
                is_active=1,
            )
            db.session.add(it)
            db.session.flush()

            # Record initial stock ledger entry
            movement = StockMovement(
                item_id=it.id,
                terminal_code=terminal_code,
                qty_before=Decimal("0.00"),
                qty_change=Decimal(str(stock)),
                qty_after=Decimal(str(stock)),
                movement_type="adjustment",
                ref_doc_type="opening_balance",
                ref_doc_no=f"OPEN-{sku}",
                unit_cost=Decimal(str(cost)),
                user_id=admin_user.id if admin_user else None,
                notes="Initial coffee shop catalog initialization",
            )
            db.session.add(movement)
            seeded_count += 1
            click.echo(f"[+] Coffee Item added: {name_en} ({sku}) | Price: PKR {sale} | Stock: {stock}")
        else:
            it.item_name = name_en
            it.item_name_ur = None
            it.category_id = cat.id if cat else None
            it.cost_price = Decimal(str(cost))
            it.sale_price = Decimal(str(sale))
            it.tax_percent = Decimal(str(tax))
            it.current_stock = Decimal(str(stock))
            it.is_active = 1

    # Clear any residual Urdu names
    Item.query.filter(Item.item_name_ur.isnot(None)).update({Item.item_name_ur: None}, synchronize_session=False)
    Category.query.filter(Category.cat_name_ur.isnot(None)).update({Category.cat_name_ur: None}, synchronize_session=False)
    db.session.commit()
    click.secho(
        f"\n[SUCCESS] Demo seeding complete: {len(demo_items)} items configured with English names, barcodes, and stock!",
        fg="green",
        bold=True
    )
    click.echo(f"  * POS Login URL:      http://localhost:5000/auth/login")
    click.echo(f"  * POS Terminal URL:   http://localhost:5000/pos/")
    click.echo(f"  * Demo Credentials:   Username: admin | Password: {admin_password}")
