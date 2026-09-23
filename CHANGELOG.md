# Changelog

All notable changes to the Offline-First Oracle POS & Inventory Management System will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to Semantic Versioning.

---

## [Unreleased] - Stage 1 (Foundation & Database)

### Added
- **Master Project Layout**: Structured Flask Application Factory directory hierarchy (`app/`, `blueprints/`, `models/`, `services/`, `payments/`, `compliance/`, `utils/`, `templates/`, `static/`, `scripts/`, `tests/`).
- **Dependency Configuration**: Pinned `requirements.txt` with verified PyPI packages (`Flask 3.1.3`, `SQLAlchemy 2.0.40`, `oracledb 3.4.2`, `waitress 3.0.2`, `python-ulid 3.0.0`, `qrcode 8.2`, `python-barcode 0.16.1`, `Pillow 11.3.0`, `Flask-Limiter 3.10.1`, etc.).
- **Security & Config**:
  - `config.py` with strict environment variable validation, no insecure fallbacks in production, and zero `${VAR}` interpolation.
  - `oracledb.defaults.fetch_decimals = True` configuration.
  - Safe `.env.example` placeholder template.
- **Oracle 19c Compatible Schema Models**:
  - Full declarative definitions for all 30 database tables adhering to the $\le 30$-character unquoted lowercase naming convention.
  - Universal sync mixin (`sync_uid` [ULID], `terminal_code`, `row_version`, `created_at`, `updated_at`, `created_by`, `updated_by`, `deleted_at`).
  - Automatic `sync_outbox` transactional queue generation via SQLAlchemy ORM event listeners.
  - Explicit `CLOB` mapping for long text fields (`items.description`, `settings.setting_val`, `audit_logs.old_val`, `audit_logs.new_val`, `sync_outbox.payload`).
  - `NUMBER(12, 2)` for currency and `NUMBER(12, 3)` for stock quantities.
  - Atomic sequence tracking table `doc_counters` with row-level locking.
- **Financial Calculation Engine (`app/services/calculation_service.py`)**:
  - Deterministic implementation of the 6 mandatory financial rules using `decimal.Decimal` and `ROUND_HALF_UP`.
- **Authentication & RBAC**:
  - Server-side role decorators (`@login_required`, `@role_required`, `@permission_required`).
  - Password hashing with Werkzeug scrypt/pbkdf2.
  - Login rate limiting and account lockout after 5 failed attempts (15-minute lock).
  - Force password change flag (`must_change_pwd`).
- **CLI Commands**:
  - `flask db-check`: validates Oracle listener reachability and verifies `NLS_CHARACTERSET == 'AL32UTF8'`.
  - `flask backup-db`: Oracle Data Pump (`expdp`) command with timestamping and verification, available prior to first migration.
  - `flask restore-db`: Data Pump restore command requiring explicit `--confirm`.
  - `flask seed`: Seeds roles, settings, units, payment modes, prompting interactively for Super Admin password via `getpass`.
  - `flask process-print-queue`: Print queue retry worker.
- **Hardware Validation**:
  - `scripts/printer_smoke_test.py`: Standalone CLI testing USB, Network TCP (Port 9100), and Serial thermal printer connections.
- **Safety Testing Suite**:
  - `tests/conftest.py` with strict database isolation preventing execution against production DSNs.
  - Automated unit tests for calculation rules, authentication, and role authorization.

---

## [Unreleased] - Stage 2 (Branding, Settings & Dashboard Shell) & Stage 3 (Items & Catalog)

### Added
- **Dynamic Configuration & Branding Engine**:
  - `SettingService`: Centralized management of typed settings backed by Oracle `settings` table with CLOB storage.
  - Zero hardcoding: Injected all organization identity, theme colors, and defaults into Jinja via global context processor.
  - Settings administration UI (`/settings/`) restricted to Super Admin.
  - Safe image upload utility (`app/utils/upload.py`) with strict extension whitelist, magic byte header inspection, UUID naming, and strict SVG rejection.
  - Color pickers dynamically updating CSS custom properties `--brand-primary` and `--brand-secondary`.
- **Operational Dashboard Shell**:
  - Real-time catalog metrics: total items, low stock alerts, active categories, and pending sync outbox count.
  - Quick action buttons for POS, new item creation, and system configuration.
  - Hardware, terminal, and database health status badges.
- **Category Hierarchy & Measurement Units**:
  - Category management (`/items/categories`) supporting parent/sub-category tree, Urdu names (`cat_name_ur`), and sort ordering.
  - Measurement unit management (`/items/units`) with decimal fractional capability flags (`allow_decimal`).
- **Catalog Items & Inventory Management**:
  - Full Item CRUD (`/items/`, `/items/create`, `/items/<id>/edit`, `/items/<id>/delete`).
  - Auto-generated SKU recommendation engine (`ItemService.generate_next_sku()`) with full manual override.
  - Bilingual Urdu labeling (`item_name_ur`) verified with `VARCHAR2(250 CHAR)` semantics.
  - Financial precision: `cost_price`, `sale_price`, `wholesale_price` stored as `NUMBER(12, 2)` and manipulated strictly via `decimal.Decimal`.
  - Reorder level alerts (`reorder_level` stored as `NUMBER(12, 3)`).
  - Multi-criteria filtering: search across English, Urdu, SKU, barcode; filter by category, status, or low-stock.
  - Field-level audit trail: every edit records detailed before/after JSON diffs to the `audit_logs` table.
  - Soft deletion: sets `deleted_at` tombstone and leverages function-based partial unique indexes to immediately release SKU and barcode for reuse on active items.
- **Automated Oracle Integration Test Suite**:
  - 11 new database-backed integration tests in `tests/test_stage2_stage3_oracle.py`, expanding the test suite to 37 passing tests against live Oracle.

