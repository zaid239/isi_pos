# Architectural Decisions Log (DECISIONS.md)

This log records every architectural decision, technical rationale, trade-offs, and design constraint for the Offline-First Oracle POS & Inventory Management System.

---

## ADR 001: Offline-First Deployment Topology & Central Synchronization
* **Date**: 2026-09-19
* **Context**: Counter sales must never depend on the internet. Institutional retail environments in Pakistan experience WAN/ISP drops and power disruptions.
* **Decision**: 
  1. The application and its Oracle database run locally on-site at each store/counter.
  2. The local database is the single source of truth.
  3. At end of day and on-demand, local changes replicate one-way (local $\to$ central) to a read-only central Oracle server over HTTPS using an authenticated, signed REST push endpoint (`POST /api/sync/push`).
  4. The central server never writes to local counters.
* **Consequences**: Outages on the WAN do not interrupt cashier checkouts, scanning, or printing.

---

## ADR 002: Oracle 19c Compatibility Baseline
* **Date**: 2026-09-19
* **Context**: The target Oracle environment spans versions from 19c Enterprise Edition down to 21c XE and 23ai Free.
* **Decision**:
  1. Restrict all schema and SQL to features available in **Oracle 19c**.
  2. No native `BOOLEAN` columns (use `NUMBER(1)` with a `CHECK (col IN (0, 1))` constraint).
  3. No native 23ai `JSON` data type (use `CLOB` with `CHECK (col IS JSON)`).
  4. Strict unquoted lowercase naming for all tables, columns, indexes, and constraints.
  5. Maximum identifier length constrained to $\le 30$ characters.
  6. Explicit constraint naming (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`).
* **Consequences**: Maximum portability across all Oracle 19c, 21c, and 23ai distributions.

---

## ADR 003: Local Storage Caps & Projected Data Growth
* **Date**: 2026-09-19
* **Context**: Local on-site instances may run Oracle XE or 23ai Free, which enforce a 12 GB user data ceiling, 2 GB SGA/PGA RAM, and 2 CPU threads.
* **Decision**:
  1. Record the 12 GB limit as a permanent operational boundary for local installations.
  2. Estimated data growth analysis:
     - 10,000 active catalog items: $\approx 15 \text{ MB}$.
     - 500 sales per day $\times$ 4 lines average = 2,000 invoice line rows per day.
     - 2,000 invoice lines + invoices + stock movements + outbox entries: $\approx 2.5 \text{ MB}$ raw per day.
     - Annual data growth: $\approx 900 \text{ MB}$ to $1.1 \text{ GB}$ per operational year.
     - A 12 GB local database provides approximately **10 to 11 years** of local runway before tablespace exhaustion.
  3. Implement routine pruning commands (`flask prune-outbox --older-than 90d --confirm`) to purge applied sync payloads.

---

## ADR 004: Universal Identity, ULIDs, and the Outbox Pattern
* **Date**: 2026-09-19
* **Context**: Central consolidation requires collision-free primary keys across multiple branch sites without exposing internal sequential autoincrements.
* **Decision**:
  1. `sync_uid`: Every syncable table carries a 26-character `VARCHAR2(26 CHAR)` ULID generated in Python at insert time.
  2. Local `id` (`NUMBER GENERATED AS IDENTITY`) is strictly local and never synced.
  3. All foreign keys in sync payloads reference the parent's `sync_uid`.
  4. Central server writes using `MERGE INTO ... ON (target.sync_uid = incoming.sync_uid)` guarded by `incoming.row_version > target.row_version`.
  5. The `sync_outbox` table is populated via SQLAlchemy ORM event listeners (`after_insert`, `after_update`, `after_delete`) inside the **exact same transaction** as the business change. Rollbacks roll back outbox entries cleanly.
* **Consequences**: Zero orphan sync records, zero duplicate sales, and zero central collision risk.

---

## ADR 005: Distinction Between `terminal_code` and `counter_code`
* **Date**: 2026-09-19
* **Context**: Clarification on store identification vs physical counter registers.
* **Decision**:
  1. `terminal_code`: Represents the **branch/site identifier** (e.g. `T01` for Headquarters, `T02` for Branch 2). It is embedded in document numbers (`ISI-T01-2526-000123`) and attached to every synced record.
  2. `counter_code`: Represents the **physical checkout counter lane / POS workstation** within that site (e.g. `C01`, `C02`).
* **Consequences**: Enables multi-counter drawer reconciliations and counter-specific shift reporting without colliding document series.

---

## ADR 006: Local-Only vs. Central-Synced Tables
* **Date**: 2026-09-19
* **Context**: Outbox synchronization must only transmit durable business and audit records, not transient counter state.
* **Decision**:
  - **Local-Only Tables (NEVER synced)**:
    1. `held_bills` (temporary parked carts during a cashier shift)
    2. `doc_counters` (local atomic counters for gapless document numbers)
    3. `print_jobs` (local hardware spooler and retry queue)
    4. `sync_outbox` (local queue awaiting push)
    5. `sync_batches` (local and central batch transport tracking)
  - **Synced Tables (All carry standard sync columns and outbox listeners)**:
    `settings`, `roles`, `users`, `login_history`, `categories`, `units`, `items`, `item_images`, `suppliers`, `customers`, `purchases`, `purchase_items`, `stock_movements`, `stock_adjustments`, `shifts`, `invoices`, `invoice_items`, `payment_modes`, `invoice_payments`, `payment_transactions`, `sales_returns`, `sales_return_items`, `expense_heads`, `expenses`, `audit_logs`.

---

## ADR 007: WSGI Server Architecture (Windows vs. Linux)
* **Date**: 2026-09-19
* **Context**: Gunicorn requires POSIX system calls (`fork()`, signals) and cannot run on Windows counter machines.
* **Decision**:
  1. Local on-site servers running Windows will use **`waitress`** (`waitress-serve --port=5000 wsgi:app`).
  2. Central servers or counter machines running Linux will use **`gunicorn`** configured via `gunicorn.conf.py`.
  3. Production documentation and service configurations are supplied for both operating systems.

---

## ADR 008: Money, Decimals and Rounding (CalculationService)
* **Date**: 2026-09-19
* **Context**: Financial integrity requires absolute determinism and zero floating-point drift.
* **Decision**:
  1. `python-oracledb` default configured with `oracledb.defaults.fetch_decimals = True`.
  2. All money represented as `NUMBER(12, 2)` $\to$ `decimal.Decimal`.
  3. All quantities represented as `NUMBER(12, 3)` $\to$ `decimal.Decimal`.
  4. Rounding method: `ROUND_HALF_UP` on line totals.
  5. Bill-level discounts distributed proportionally across lines *before* tax, recomputing line tax.
  6. Final payable cash rounding stored in dedicated `round_off` column on `invoices`.
  7. Server-side payment validation: $\sum(payments) == payable$.

---

## ADR 009: Test Isolation and Safety Guards
* **Date**: 2026-09-19
* **Context**: Automated tests must run against real Oracle schemas, but must never risk truncating production tables.
* **Decision**:
  1. `TestingConfig` strictly requires `TEST_DB_HOST`, `TEST_DB_PORT`, `TEST_DB_SERVICE_NAME`, `TEST_DB_USER`, and `TEST_DB_PASSWORD` with no default fallbacks.
  2. `tests/conftest.py` executes an assertion guard comparing test connection parameters to production parameters; if identical, execution aborts immediately.

---

## ADR 010: Terminal Token Row Ownership & Immutability in Sync Engine (Stage 10)
* **Date**: 2026-09-19
* **Context**: When syncing offline nodes to central server, data sovereignty and tamper-proofing must prevent cross-site corruption.
* **Decision**:
  1. In Stage 10 Sync Engine, central sync endpoints authenticate incoming payloads via mutual bearer token strictly bound to an authorized `terminal_code`.
  2. The server-side synchronization receiver verifies that every row payload's `terminal_code` matches the authenticating terminal's identity.
  3. A terminal is strictly prohibited from inserting, updating, or tombstoning any `sync_uid` owned by another terminal. Cross-terminal updates are rejected with `403 Forbidden` and audited.

---

## ADR 011: Document Sequence Isolation by Counter Lane (doc_counters)
* **Date**: 2026-09-19
* **Context**: Multiple counter lanes concurrently generating invoices at high volume will contend on row locks if document numbering is serialized per site alone.
* **Decision**:
  1. `doc_counters` primary key composite constraint binds: `(terminal_code, counter_code, doc_type, fiscal_year)`.
  2. Document numbers follow the format `ISI-{terminal_code}-{counter_code}-{fiscal_year}-{number:06d}` (e.g. `ISI-T01-C01-2526-000001`).
  3. Because counters `C01` and `C02` update separate rows, lane lock contention is mathematically eliminated. A slow or offline counter never impacts transaction throughput on neighboring counters.

---

## ADR 012: Master & Lookup Natural Key Status Deactivation Policy
* **Date**: 2026-09-19
* **Context**: Master/lookup entities (`users.username`, `categories.cat_code`, `units.unit_code`, `payment_modes.mode_code`, `expense_heads.head_code`) carry plain `UNIQUE` constraints. If soft deletion were permitted on them, soft-deleted rows would remain in the table and permanently block future reuse of that natural key.
* **Decision**:
  1. Master/lookup models use status deactivation (`is_active = 0`) rather than soft deletion.
  2. Calling `.soft_delete()` on `User`, `Category`, `Unit`, `PaymentMode`, or `ExpenseHead` raises `NotImplementedError`.
  3. Only transactional records and items (which use function-based unique indexes filtering `deleted_at IS NULL`) support soft deletion.

---

## ADR 013: Dual-Mode Backup & Restore Strategy with Identity Sequence Resets
* **Date**: 2026-09-19
* **Context**: On remote development stations and thin-client counter machines, the Oracle Data Pump binary (`expdp` / `impdp`) may not be present on the local client PATH.
* **Decision**:
  1. `flask backup-db` and `flask restore-db` implement dual-mode execution:
     - **Host Data Pump**: If `expdp` is available on the client PATH, it delegates to Oracle Data Pump targeting the server host's configured Oracle `DIRECTORY`.
     - **Client Logical Fallback**: If `expdp` is not found, the CLI performs a direct transactional schema extraction and restore via SQLAlchemy and `python-oracledb`.
  2. The restore utility temporarily disables all foreign keys, replaces records in a single database transaction, re-enables foreign keys, and dynamically advances all identity sequences (`user_tab_identity_cols`) to `NVL(MAX(id), 0) + 1` to prevent sequence key collisions on subsequent inserts.

---

## ADR 014: POS Sale Screen & Atomic Checkout Transaction Architecture
* **Date**: 2026-09-19
* **Context**: Client demo requires a high-performance counter sale screen handling barcode scanning, live calculation, cash/card payment, stock deduction, and receipt printing without race conditions or financial discrepancies.
* **Decision**:
  1. **Financial Arithmetic Isolation**: All subtotals, tax rates, cash rounding, and grand totals are calculated authoritatively through `CalculationService.calculate_bill`. No arithmetic is duplicated in frontend JavaScript or SQL triggers.
  2. **Deadlock Elimination**: Items in checkout are ordered by `id` ascending and locked using `SELECT ... FOR UPDATE` before stock verification and deductions. Concurrent cashier lanes purchasing overlapping items will never deadlock Oracle.
  3. **Atomic Multi-Entity Transaction**: `invoices`, `invoice_items`, `invoice_payments`, `items.current_stock` decrements, and `stock_movements` rows are committed in a single atomic database transaction. If any validation fails (e.g. insufficient stock), zero records are created and stock is untouched.
  4. **Idempotency Guard**: Every checkout payload includes a client-generated UUID `idempotency_key`. If a cashier double-clicks or a network retry occurs, the existing completed invoice receipt is returned immediately without duplicate stock deductions or billing.
  5. **Direct Browser Printability**: Receipts are styled for thermal (80mm/58mm) and standard printing using CSS `@media print` rules and triggered via `window.print()`, eliminating external driver dependencies for the demo.

---

## ADR 015: Retention of Unused `_ur` (Urdu) Database Columns
* **Date**: 2026-09-19
* **Context**: The institution operates strictly and exclusively in English. An initial requirement for bilingual English/Urdu cataloging and settings (`item_name_ur`, `cat_name_ur`, `org_name_ur`) was mistakenly requested and subsequently rescinded. The underlying Oracle database tables (`items`, `categories`, `settings`) had already been migrated with these nullable columns using `AL32UTF8` character semantics.
* **Decision**:
  1. **Zero Schema Alterations / No Destructive Migrations**: Do not execute `ALTER TABLE DROP COLUMN` or generate any migration for `item_name_ur`, `cat_name_ur`, or other `_ur` columns. Altering columns or dropping them on a live, shared enterprise Oracle database carries operational risks with zero functional benefit.
  2. **Strict UI & Search Elimination**:
     - All user-facing interfaces (item create/edit forms, item lists, category administration, POS product grid, live search, cart view, on-screen and printable receipts, and branding settings) operate in 100% English.
     - Search queries (`POSService.search_items`, `ItemService.get_filtered_items`) query English name, SKU, and barcode only. Urdu column expressions are completely removed from search queries.
     - External font requests (e.g. `Noto Nastaliq Urdu`) are eliminated from base and receipt templates.
     - Catalog seeders (`flask seed-demo`) seed items with English names and set `_ur` fields to `NULL`.
  3. **Column Status**: All `_ur` database columns are retained in the physical Oracle schema as dormant, nullable, unused fields.
* **Consequences**: Zero risk of table locks, DDL disruption, or migration rollbacks on production Oracle instances; completely English-only UI and operational workflows.
