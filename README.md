# Offline-First Institutional POS & Inventory Management System (Oracle Edition)

Production-ready, offline-first Point of Sale (POS), Inventory, Stock, Purchasing, Customer/Supplier, Reporting, and Cash Management web application built for institutional deployment in Pakistan.

---

## 1. System Architecture & Deployment Topology

### Offline-First Architecture
* The application server and its Oracle Database instance run **locally on-site** on the counter machine.
* Counter operations (scanning, cart manipulation, cashier checkout, thermal printing) are **100% offline** and never depend on the internet.
* At the end of the day or on-demand, local changes are pushed one-way to a **read-only central Oracle server** via an HMAC-signed HTTPS push endpoint (`POST /api/sync/push`).
* The local database is the single source of truth. The central server never writes to local counters.

### Operating System & WSGI Server Compatibility
* **Windows (Local Counter Host)**: Run using **`waitress`**:
  ```powershell
  waitress-serve --port=5000 wsgi:app
  ```
  *(Gunicorn is POSIX-only and cannot run on Windows).*
* **Linux (Central Server / Linux Counters)**: Run using **`gunicorn`**:
  ```bash
  gunicorn -c gunicorn.conf.py wsgi:app
  ```

---

## 2. Oracle Database Setup & Resource Guardrails

### Minimum Privileges Required (Section 35)
Connect to Oracle as a DBA user and grant only the necessary least-privileges to the application user:
```sql
-- Create application user
CREATE USER isi IDENTIFIED BY "YourSecurePasswordHere"
  DEFAULT TABLESPACE users
  TEMPORARY TABLESPACE temp
  QUOTA UNLIMITED ON users;

-- Grant minimal required permissions
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW,
      CREATE PROCEDURE, CREATE TRIGGER TO isi;

-- Grant Data Pump backup directory
CREATE OR REPLACE DIRECTORY POS_BACKUP_DIR AS 'C:\oradata\pos_backups';
GRANT READ, WRITE ON DIRECTORY POS_BACKUP_DIR TO isi;
```

### Critical Character Set Requirement (`AL32UTF8`)
The database character set **must be `AL32UTF8`** to prevent corruption of multi-byte Urdu text:
```sql
SELECT parameter, value FROM nls_database_parameters
WHERE parameter IN ('NLS_CHARACTERSET','NLS_NCHAR_CHARACTERSET');
```
*`flask db-check` will automatically verify this and halt execution if not met.*

### Oracle XE / Free Data Capacity & Projected Growth (DECISIONS.md)
* **Local Limit**: 12 GB user data, 2 GB RAM, 2 CPU threads.
* **Annual Growth Projection**:
  - 10,000 catalog items: ~15 MB.
  - 500 sales/day (2,000 lines/day) + ledger + outbox: ~2.5 MB/day.
  - Annual volume: ~900 MB to 1.1 GB.
  - A 12 GB Oracle XE instance provides **10 to 11 years** of on-site counter runway before requiring tablespace archiving.

---

## 3. Environment Configuration (`.env`)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Ensure `DB_SERVICE_NAME`, `DB_USER`, and `DB_PASSWORD` are configured:
```env
FLASK_ENV=development
SECRET_KEY=generate_a_random_32_byte_hex_key
DB_HOST=127.0.0.1
DB_PORT=1521
DB_SERVICE_NAME=XEPDB1
DB_USER=isi
DB_PASSWORD=YourPasswordHere
DB_SCHEMA=ISI
TERMINAL_CODE=T01
COUNTER_CODE=C01
```

---

## 4. CLI Tooling & Operational Commands

### 1. Database Pre-Flight Check
Validates connectivity, TNS listener reachability, and `AL32UTF8` character set:
```bash
flask db-check
```

### 2. Database Backup (`flask backup-db`)
Creates a timestamped, consistent database backup with companion uploads archive:
* **Host Mode (Enterprise)**: If Oracle Data Pump (`expdp`) is installed on the client machine's `PATH`, it initiates an `expdp` export job to the server host's configured Oracle `DIRECTORY` object (e.g. `POS_BACKUP_DIR`), readable only by the Oracle OS service user.
* **Client-Side Fallback Mode**: If `expdp` is not installed on the workstation client, the command executes a direct, self-contained logical export extracting all 31 tables and records with metadata into `backups/isi_pos_<timestamp>.dmp` (UTF-8 JSON format with ISO-8601 timestamps and decimal fidelity).
* **Uploads Directory Companion Archive**: Includes `--with-uploads` (enabled by default) which automatically packages `app/static/uploads/` (brand logos, favicons, and product photos) into `backups/isi_pos_<timestamp>_uploads.zip` alongside the database dump.
```bash
# Full backup with uploads included (default)
flask backup-db

# Database dump only (skip uploads folder)
flask backup-db --no-with-uploads
```

### 3. Database Restore Drill (`flask restore-db`)
Restores schema records from an exported dump (requires explicit `--confirm` flag to prevent accidental overwrite):
* In client-side mode, it disables foreign key constraints, replaces records in a single database transaction, re-enables constraints, dynamically restarts all identity sequences (`user_tab_identity_cols`) to `NVL(MAX(id), 0) + 1` to guarantee zero sequence collisions on subsequent inserts, and automatically extracts and restores companion `_uploads.zip` archives into `app/static/uploads/`.
```bash
flask restore-db isi_pos_20260919_162420.dmp --confirm
```

### 4. Orphan Uploads Pruning (`flask prune-orphan-uploads`)
Audits the physical `app/static/uploads/` tree against active database records in `settings` (`org_logo_url`, `org_favicon_url`) and `item_images` (`file_path`):
```bash
# Dry-run audit (reports orphan files and reclaimable space without deleting)
flask prune-orphan-uploads

# Permanent deletion (requires explicit --confirm flag)
flask prune-orphan-uploads --confirm
```

### 4. Database Seeding & Interactive Super Admin
Initializes roles, default settings, units, payment modes, and prompts interactively via `getpass` for the Super Admin password:
```bash
flask seed
```

### 5. Hardware Printer Smoke Test CLI
Validates thermal printer drivers and interface connectivity before POS launch:
```bash
# Network printer (Raw TCP Port 9100)
python scripts/printer_smoke_test.py --mode network --ip 192.168.1.200 --port 9100 --paper-size 80mm

# USB printer (provide Vendor ID and Product ID)
python scripts/printer_smoke_test.py --mode usb --vendor-id 0x04b8 --product-id 0x0202

# Serial printer
python scripts/printer_smoke_test.py --mode serial --serial-port COM1 --baudrate 9600
```

### 6. Background Print Queue Worker
Processes pending print jobs with up to 3 automatic retries:
```bash
flask process-print-queue
```

---

## 5. Automated Test Suite (26 Passing Tests)

### Safety Isolation
Tests strictly require an isolated test schema (`ISI_POS_TEST`) and will **abort immediately** with a fatal error if pointed at production credentials:
```sql
CREATE USER isi_pos_test IDENTIFIED BY "TestPassword123"
  DEFAULT TABLESPACE users
  QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO isi_pos_test;
```
Configure test parameters in your environment:
```env
TEST_DB_SERVICE_NAME=orclpdb1
TEST_DB_USER=isi_pos_test
TEST_DB_PASSWORD=TestPassword123
```
Run pytest:
```bash
python -m pytest tests/ -v
```
**Test Coverage Summary (26 Tests Passed)**:
* `tests/test_oracle_backend.py` (15 tests against live Oracle): Safety guard aborts, role enforcement, forced password redirects, CSRF enforcement, account lockout, sync_uid uniqueness, monotonic row_version increments, transactional outbox safety, soft delete timestamp retention, SKU reuse via function-based unique indexes, CHAR semantics with multi-byte Urdu strings, Decimal exact round-trip, empty/whitespace string rejection, outbox positive committed records, and non-soft-deletable entity `NotImplementedError` validation.
* `tests/test_auth.py`, `tests/test_calculations.py`, `tests/test_roles.py` (11 unit tests): Password hashing, session security, financial tax/rounding math (`ROUND_HALF_UP`), and role hierarchy.

---

## 6. Financial Arithmetic & Compliance Notes

* **Money Calculations**: Implemented centrally in `CalculationService` using `decimal.Decimal` and `ROUND_HALF_UP` (strictly no floats).
* **Idempotency**: POS clients supply a unique `idempotency_key` (UUID) with each transaction. Repeated submissions return the existing invoice without duplicate stock deductions.
* **PCI-DSS Compliance**: No cardholder Primary Account Number (PAN), Personal Identification Number (PIN), or CVV security code is ever stored anywhere in the database. In `invoice_payments`, only masked `card_last4`, card type, and bank reference are stored. In `payment_transactions`, raw gateway request and response payloads store only transaction IDs, tokens, and authorization statuses.
* **Natural Key Policy**: Master/lookup entities with unique codes (`User.username`, `Category.cat_code`, `Unit.unit_code`, `PaymentMode.mode_code`, `ExpenseHead.head_code`) use status deactivation (`is_active = 0`) instead of soft deletion to prevent permanently blocking unique natural keys. Calling `.soft_delete()` raises `NotImplementedError`.
* **FBR Tier-1 Readiness**: Columns (`fbr_invoice_no`, `fbr_status`, `fbr_response`, `pos_reg_no`) and `FbrClient` stub exist in `app/compliance/fbr.py`.
* **Online Payment Gateways**: Abstraction in `app/payments/` with `ManualGateway` active and stubs for `HBLPayGateway`, `EasypaisaGateway`, and `JazzCashGateway`.
