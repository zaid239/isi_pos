# MASTER BUILD SPEC — POS, INVENTORY & REPORTING SYSTEM (ORACLE, OFFLINE-FIRST)

This is the complete and only specification. There are no other documents. Where anything here is ambiguous, ask — do not assume.

---

# PART A — PROJECT, RULES & FOUNDATION

## 1. ROLE & OBJECTIVE

You are a senior Python/Flask software architect and production engineer.

Build a production-ready **Point of Sale, Inventory, Purchasing, Customer/Supplier, Cash Management and Reporting system** for an institution in Pakistan.

This is not a demo or a prototype. It will handle real money, real stock and real tax records. It must be stable, secure, fast, professional, and operable by non-technical staff.

Target environment: desktop/laptop at the counter, barcode scanner, thermal receipt printer, tablet-compatible UI, English and Urdu content, **fully functional without internet**.

Do not produce a generic Bootstrap admin template. Build a polished business application.

---

## 2. NON-NEGOTIABLE RULES

**Data safety**

* Never drop the database, drop tables, truncate, or delete existing records automatically.
* Never run a destructive or schema-altering migration without showing me the migration file and the exact SQL first, and waiting for my approval.
* Never use `db.create_all()`. Alembic migrations only.
* Oracle DDL auto-commits and cannot be rolled back — every migration needs a tested `downgrade()`, and a backup must be taken immediately before any migration against a database containing data.

**Credentials**

* Never hardcode credentials, hostnames, organisation name, logo, tax rates, currency or printer settings anywhere in source code.
* Never print, log or commit real credentials. `.env` is local only; `.env.example` contains placeholders.

**Scope discipline**

* Before adding any dependency not listed in Section 4, explain why and wait for approval.
* Do not rewrite or restructure a module I have already confirmed without asking first.
* Where this spec is ambiguous about money, tax, or stock quantity — ask. Never guess.

**Code quality**

* Type hints, docstrings, clear names, modular architecture.
* Business logic lives in the service layer — not in templates, not in route functions.
* `decimal.Decimal` for all money and quantity. `float` is forbidden for financial values.

---

## 3. PRE-FLIGHT CHECKS — COMPLETE BEFORE WRITING ANY CODE

Ask me for these and wait for answers. Do not create a single table until items 1–8 are answered and items 9–11 are approved.

1. Oracle edition and version on the **central server** and on the **local on-site machine** (e.g. 19c EE, 21c XE, 23ai Free).
2. The **service name or SID** for each connection. Note: `isi` is a schema/user name, not a database name — Oracle additionally requires a service name such as `XEPDB1` or `ORCLPDB1`.
3. Listener port (default 1521) and confirmed reachability.
4. Database character set. Tell me to run:
   ```sql
   SELECT parameter, value FROM nls_database_parameters
   WHERE parameter IN ('NLS_CHARACTERSET','NLS_NCHAR_CHARACTERSET');
   ```
   It must be **`AL32UTF8`**. If it is not, stop and report it — Urdu text will be corrupted, and this cannot be fixed after data is entered.
5. If the local install is Oracle XE/Free, confirm the data cap (21c XE: 12 GB user data, 2 GB RAM, 2 threads) is acceptable, and confirm the institution's licensing covers installing Oracle on the on-site machine.
6. `TERMINAL_CODE` for this site (e.g. `T01`), and how many counters will run.
7. Thermal printer make/model and connection type (USB / network IP:9100 / serial).
8. Fiscal year start (default 1 July) and whether the institution falls under FBR Tier-1 POS integration rules.
9. Produce the **reserved-word collision report** (Section 7) covering every table and column name you intend to create.
10. Produce the revised `requirements.txt` with pinned versions for approval.
11. Produce the `config.py` connection code for approval.

---

## 4. TECHNOLOGY STACK

**Backend:** Python 3.11+, Flask (application factory + blueprints), SQLAlchemy 2.x, Flask-SQLAlchemy, Flask-Migrate/Alembic, Flask-Login, Flask-WTF, Flask-Limiter, Werkzeug password hashing.

**Database:** **Oracle Database**, driver **`oracledb`** (python-oracledb, thin mode — no Instant Client required), SQLAlchemy dialect `oracle+oracledb://`, port 1521, character set `AL32UTF8`.

**Frontend:** Jinja2, Bootstrap 5, vanilla JavaScript and/or Alpine.js, fetch/AJAX. **No React, Vue or any SPA framework.** The POS screen must operate without full page reloads.

**Reporting:** openpyxl (Excel), CSV, ReportLab or WeasyPrint (PDF), Chart.js.

**Printing:** python-escpos, browser print fallback, PDF.

**Scheduling:** APScheduler or OS-level scheduled tasks (cron / Windows Task Scheduler) for sync, backups and the print queue.

**Deployment:** Gunicorn, Nginx, systemd unit example, plus `flask run` for development. Provide `gunicorn.conf.py` and `nginx.sample.conf`.

### Connection configuration

Build the URI in `config.py` from individual values. Do not use `${VAR}` interpolation inside `.env`.

```python
import oracledb
from sqlalchemy.engine import URL

oracledb.defaults.fetch_decimals = True   # numbers return as Decimal, not float

dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE_NAME)
SQLALCHEMY_DATABASE_URI = URL.create(
    "oracle+oracledb", username=DB_USER, password=DB_PASSWORD, query={"dsn": dsn}
)
```

`.env.example`:

```env
FLASK_ENV=development
SECRET_KEY=CHANGE_ME
DB_HOST=YOUR_DB_HOST
DB_PORT=1521
DB_SERVICE_NAME=XEPDB1
DB_USER=isi
DB_PASSWORD=CHANGE_ME
DB_SCHEMA=ISI
TERMINAL_CODE=T01
UPLOAD_FOLDER=app/static/uploads
MAX_CONTENT_LENGTH=5242880
PAYMENT_GATEWAY=manual
FBR_ENABLED=false
CENTRAL_SYNC_URL=
CENTRAL_SYNC_TOKEN=
CENTRAL_SYNC_SECRET=
```

Engine options: `pool_pre_ping=True`, `pool_size=5`, `max_overflow=10`, `pool_recycle=1800`, `arraysize=200`.

---

## 5. DEPLOYMENT TOPOLOGY — OFFLINE-FIRST

* The application and its Oracle database run **locally on the on-site machine**. All daily operation is offline and never depends on the internet.
* At end of day, and on demand, the local system pushes that day's changes to a **central Oracle server** over the internet.
* The central server is a **read-only mirror** for backup, consolidation and management reporting. Nobody edits data there. Data flows one way only: **local → central**.
* The local database is the single source of truth. If local and central disagree, local wins.
* Local and central must run the same Oracle major version and the same schema.

The on-site machine is a single point of failure. See Section 33 — hourly local backups and a UPS are mandatory, and the sync is **not** a substitute for a backup.

---

# PART B — DATA FOUNDATION

## 6. ORACLE BEHAVIOUR YOU MUST HANDLE EXPLICITLY

1. **Empty string is NULL.** `''` and `NULL` are identical in Oracle. Every required text field needs `NOT NULL` plus application validation rejecting whitespace-only input. Never compare `!= ''`.
2. **Identifiers fold to uppercase** unless quoted. Define all names lowercase in models and stay consistent. Never mix quoted and unquoted styles.
3. **Keep every identifier under 30 characters** — tables, columns, indexes, constraints. Name constraints explicitly (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`); do not let Alembic auto-generate long names.
4. **`NULL` sorts last** in `ORDER BY ASC`. Use `NULLS FIRST`/`NULLS LAST` explicitly in reports.
5. **No `LIMIT`.** Use `OFFSET n ROWS FETCH NEXT m ROWS ONLY`. Verify the generated SQL.
6. **No `ON DUPLICATE KEY UPDATE`.** Use `MERGE INTO ... USING dual ON (...) WHEN MATCHED ... WHEN NOT MATCHED ...`.
7. **Comparisons are case-sensitive.** For item search, build a function-based index on `UPPER(name)` and search with `UPPER()`. Apply one approach consistently and record it in `DECISIONS.md`.
8. **Sequences produce gaps** after restart. Do not use them for invoice numbers (Section 19).
9. `DUAL` is required for scalar selects.

### Type mapping

| Purpose | Oracle type |
|---|---|
| Money | `NUMBER(12,2)` |
| Quantity | `NUMBER(12,3)` |
| Percentage | `NUMBER(5,2)` |
| Short text | `VARCHAR2(n CHAR)` — **always CHAR semantics**, never bytes (Urdu is multi-byte) |
| Long text | `CLOB` |
| Boolean | `NUMBER(1)` + `CHECK (col IN (0,1))` |
| Timestamp | `TIMESTAMP(6)` storing **UTC** — never `DATE` |
| Primary key | `NUMBER GENERATED BY DEFAULT AS IDENTITY` (`sqlalchemy.Identity()`) |
| Sync UID | `VARCHAR2(26 CHAR)` |
| JSON payload | `CLOB` + `CHECK (payload IS JSON)` |

---

## 7. NAMING & RESERVED WORDS

These are reserved or unsafe in Oracle and must not be used as-is:

| Do not use | Use instead |
|---|---|
| `uid` | `sync_uid` |
| `returns` / `return_items` (tables) | `sales_returns` / `sales_return_items` |
| `date` | `invoice_date`, `entry_date` |
| `number` | `invoice_no`, `doc_no` |
| `mode` | `mode_name`, `payment_mode_code` |
| `comment` | `notes` |
| `level` | `reorder_level` |
| `size` | `paper_size` |
| `order` / `group` | `sort_order` / `group_name` |
| `session`, `user`, `access`, `audit`, `resource`, `share`, `raw`, `public`, `row`, `rows`, `start`, `option`, `file`, `index`, `lock`, `long`, `view`, `check`, `default`, `desc`, `minus`, `validate` | rename or prefix |

Before creating the schema, produce the full collision report required by Section 3 item 9. **Never solve a collision by quoting the identifier.**

---

## 8. SCHEMA — TABLES & UNIVERSAL COLUMNS

Minimum tables:

```
users, roles, login_history, settings, categories, units, items, item_images,
suppliers, customers, purchases, purchase_items, stock_movements,
stock_adjustments, invoices, invoice_items, held_bills, payment_modes,
invoice_payments, payment_transactions, sales_returns, sales_return_items,
expenses, expense_heads, shifts, doc_counters, audit_logs, print_jobs,
sync_outbox, sync_batches
```

**Every syncable business table must carry:**

```
id             NUMBER GENERATED BY DEFAULT AS IDENTITY   -- LOCAL ONLY, never synced
sync_uid       VARCHAR2(26 CHAR) NOT NULL UNIQUE         -- ULID, generated in Python at insert
terminal_code  VARCHAR2(10 CHAR) NOT NULL
row_version    NUMBER NOT NULL DEFAULT 1                 -- +1 on every update, via event listener
created_at     TIMESTAMP(6) NOT NULL                     -- UTC
updated_at     TIMESTAMP(6) NOT NULL                     -- UTC
created_by     NUMBER
updated_by     NUMBER
deleted_at     TIMESTAMP(6) NULL                         -- soft delete / tombstone
```

**Hard deletes are forbidden on syncable tables.** A deletion is a tombstone (`deleted_at` set, `row_version` incremented) so it can replicate.

Required indexes: `invoices.invoice_no` UNIQUE, `invoices.invoice_date`, `invoices.idempotency_key` UNIQUE, `items.barcode` UNIQUE, `items.sku` UNIQUE, function-based index on `UPPER(items.name)`, `stock_movements(item_id, created_at)`, all foreign keys, `sync_outbox(status, id)`.

---

## 9. MONEY & CALCULATION RULES

Implement once in a central `CalculationService`. Never duplicate this arithmetic anywhere else.

1. **Per line:** gross = qty × rate → minus line discount → = taxable value → tax = taxable × tax% → line total = taxable + tax.
2. **Bill-level discount** is distributed proportionally across lines **before** tax, and tax is recomputed. Never apply bill discount after tax.
3. **Rounding** at line level to 2 decimals using `ROUND_HALF_UP` with `decimal.Decimal`. The bill total is the sum of the rounded lines, so the printed total always reconciles.
4. **Cash rounding** of the final payable to the nearest whole rupee (Settings: off / nearest 1 / nearest 5). The difference is stored in its own `round_off` column on the invoice.
5. **Tax-inclusive pricing:** if Settings says prices include tax, taxable value = `price ÷ (1 + tax%)`. Store taxable value and tax separately on the line.
6. **Payment validation:** `sum(invoice_payments.amount)` must exactly equal `invoices.payable`. Reject the sale server-side otherwise.

Unit tests are required for all six rules, including a tax-inclusive case and a bill-discount case.

---

## 10. TIME & BUSINESS DAY

* Store all timestamps in UTC. Display and report in `Asia/Karachi`. Do the conversion in the application layer, never via NLS session settings, so behaviour never depends on client configuration.
* Settings holds a **business day cutoff** (default 04:00). A sale at 01:30 belongs to the previous business day in every daily report and in the Z-report.
* Shift reports and Z-reports use the business day, not the calendar day.
* Invoice number yearly reset follows the configured fiscal year start (default 1 July).

---

## 11. IDENTITY, IDEMPOTENCY & THE OUTBOX

### Identity rules (this is what makes duplicates impossible)

1. `sync_uid` is a **ULID generated in Python at insert time** — never by the database, never by the central server.
2. The integer `id` is **local-only**: never sent to the central server, never used as a reference in any sync payload.
3. All foreign keys inside sync payloads are expressed as the parent's `sync_uid`.
4. On the central server, `sync_uid` is the unique key for every table, and all writes are **`MERGE` upserts on `sync_uid`**. Re-sending a row updates it; it can never duplicate it.
5. `terminal_code` is embedded in every document number: `ISI-T01-2526-000123`. Two sites can never collide.

### Idempotent submission (mandatory)

A double-click, a double barcode Enter, or a network retry must never create two invoices or deduct stock twice.

* The POS client generates a UUID `idempotency_key` when a bill starts and sends it with the sale request.
* It is stored in a unique-indexed column on `invoices`.
* A repeat request with an existing key returns the already-created invoice instead of creating a new one.
* The save button is disabled on submit and re-enabled only on a final response.
* The same applies to stock in, stock adjustment, purchase entry, return, and customer payment receipt.

### The outbox

`sync_outbox`: `id`, `table_name`, `row_uid`, `operation` (insert/update/delete), `row_version`, `payload` (CLOB IS JSON), `batch_id`, `status` (pending/sending/synced/failed), `attempts`, `last_error`, `created_at`, `synced_at`.

* A SQLAlchemy `after_insert` / `after_update` event writes the outbox entry **inside the same transaction** as the business change. If a sale rolls back, its outbox entries roll back too. Never write the outbox after commit.
* Sync reads only from the outbox. Never scan business tables by `updated_at` — clock changes and long transactions silently miss rows.
* If a row changes several times before sync, collapse to the latest `row_version` for that `row_uid`.
* Outbox rows are marked `synced`, never deleted by the sync job. Provide `flask prune-outbox --older-than 90d --confirm`.

---

# PART C — APPLICATION MODULES

## 12. AUTHENTICATION & ROLES

**Roles:**

| Role | Access |
|---|---|
| Super Admin | Everything: users, roles, settings, printer, payments, audit log, backups, sync |
| Manager | Items, stock, purchases, sales, returns, voids, adjustments, all reports, higher discount limit |
| Cashier | POS, sales, own shift, receipt printing, limited discount, returns per permission |
| Store Keeper | Stock in/out/adjustment, purchases, suppliers, inventory reports. No sales, no financial reports |

Enforce **server-side** with `@login_required`, `@role_required(*roles)`, `@permission_required(...)`, **and** hide unauthorised navigation in the UI.

Implement: login, logout, password change, **force password change on first login**, session timeout, optional remember-me, login attempt rate limiting, account lockout (default 5 attempts / 15 minutes, configurable), login and activity history with IP and user agent.

Seed the first Super Admin via `flask seed`, prompting interactively for the password with `getpass`. No hardcoded production passwords ever.

---

## 13. BRANDING & SETTINGS

Everything below is admin-configurable and stored in a `settings` table. Nothing branding-related may be hardcoded in templates. Expose to Jinja via a context processor.

Organisation name (English + Urdu), logo, favicon, primary and secondary theme colour, address, phone, email, NTN, STRN, receipt header, receipt footer, return policy, currency symbol (default `Rs.`), decimal places, thousand separator, default tax %, tax inclusive/exclusive, invoice prefix, invoice number padding, invoice reset rule, fiscal year start, business day cutoff, receipt paper size, cash rounding mode, low-stock threshold, terminal code, POS registration number, FBR toggle, printer configuration, cash drawer toggle, sync schedule and endpoint.

Uploaded branding must appear on: login page, sidebar, dashboard, POS, receipts, PDF invoices, reports and error pages.

---

## 14. ITEMS

Fields: auto-generated SKU with manual override, barcode, name (English), name (Urdu), category, sub-category, unit, description, image, cost price, sale price, wholesale price, tax %, discount limit %, opening stock, current stock, reorder level, expiry date, supplier, active flag.

Image handling: upload, preview, replace, delete, extension whitelist, MIME validation, max size, UUID filename.

Listing: search by name/SKU/barcode, category filter, status filter, low-stock filter, pagination, sorting.

Every edit writes an audit record with user, timestamp, field, old value, new value.

**Never hard-delete** an item that appears in historical invoices — soft delete only.

Barcode label printing (multiple labels per sheet).

---

## 15. BULK IMPORT / EXPORT

Import from Excel and CSV with a downloadable template. Report total rows, successful rows, failed rows, and the reason per failed row, with a downloadable error report. Export items to Excel and CSV.

---

## 16. INVENTORY

**Stock In:** supplier, purchase invoice no, date, items, quantity, cost, freight, other costs, notes. Updates stock and applies **weighted-average** costing.

**Stock Out:** reasons — institutional issue, damage, wastage, internal use, other.

**Stock Adjustment:** reason, quantity, user, timestamp, **Manager approval required**.

**Stock Ledger:** every movement records item, before quantity, change, after quantity, movement type, reference document, user, timestamp. Current stock must always be reconcilable from the ledger.

**Negative stock is not allowed** by default. Make it a configurable admin setting only if requested later.

---

## 17. POS SCREEN

The most important screen. Designed for cashiers.

**Left:** search box, barcode input, category tiles, product grid with images.
**Right:** cart, quantity controls, price, discount, remove, subtotal, discount, tax, grand total.

Requirements: barcode scanner support with auto-focused input and Enter-to-add; search by barcode, SKU or name; category filtering; keyboard-friendly; touch-friendly; no unnecessary page reloads.

Shortcuts (with an on-screen help overlay): `F1` search, `F2` payment, `F3` hold bill, `F4` new bill, `F5` customer, `F6` discount, `Esc` close modal, `Delete` remove line.

**Cart:** increase/decrease/manual quantity, remove line, line discount, bill discount, tax, rounding, customer selection, notes. Selling more than available stock is blocked with a clear message.

**Hold/Park bills:** name a held bill, start another, list held bills, resume, delete with permission. Multiple held bills supported.

---

## 18. PAYMENTS

Payment modes: **Cash, Card, Bank Transfer, Cheque, Credit/On Account** — admin can add or disable modes.

* **Cash:** tendered amount, change calculated.
* **Card:** card type, last 4 digits, bank, POS terminal approval/reference number. **Never store full card number, CVV or PIN.** No online API call — the physical terminal does the transaction.
* **Credit:** adds to customer outstanding; a separate screen receives payments against outstanding.
* **Split payment:** one invoice can carry multiple payment rows; total must equal payable.

### Future gateway architecture (build now, leave unused)

`app/payments/` with an abstract base:

```python
class PaymentGateway(ABC):
    @abstractmethod
    def authorize(self, amount, currency, metadata): ...
    @abstractmethod
    def capture(self, transaction_id): ...
    @abstractmethod
    def refund(self, transaction_id, amount): ...
    @abstractmethod
    def status(self, transaction_id): ...
```

Implement `ManualGateway` (active default). Provide `HBLPayGateway`, `EasypaisaGateway`, `JazzCashGateway` as stubs raising `NotImplementedError`. Add a gateway registry/factory selected by `PAYMENT_GATEWAY=manual`. Create `payment_transactions` (gateway, gateway_txn_id, status, raw_request, raw_response, created_at, updated_at) so no migration is needed later. Scaffold an unregistered webhook route `/payments/webhook/<gateway>`. The README must state exactly which files change when a real gateway is added.

---

## 19. INVOICE CREATION

On sale completion, in this order, **inside one transaction**:

1. Check the `idempotency_key` — if it exists, return the existing invoice and stop.
2. Validate cart and stock.
3. Lock item rows: `SELECT ... FOR UPDATE NOWAIT`, always in **ascending id order** to prevent deadlocks. On `ORA-00054`, return a clean "please retry" message, never a 500.
4. Calculate totals via `CalculationService`.
5. Allocate the invoice number (see below).
6. Create invoice, invoice lines (snapshotting item name, SKU, price, tax, cost, discount), payments, stock movements; update stock.
7. Write outbox entries.
8. Commit.
9. **Then** generate the receipt and enqueue the print job — never inside the transaction.
10. Clear the POS.

Any failure rolls back the entire sale.

Later item edits must never change a historical invoice.

### Invoice numbering — do not use an Oracle sequence

Sequences cache and leave gaps after a restart; a gapped invoice series is a problem in a tax audit. Use a locked counter row:

```sql
SELECT next_no FROM doc_counters
 WHERE doc_type = :t AND terminal_code = :tc AND fiscal_year = :fy
 FOR UPDATE;
```

Increment and update within the same sale transaction. Combined with the idempotency key, a retried request reuses its already-issued number.

---

## 20. RETURNS & VOIDS

**Returns:** search invoice, select lines, partial or full return, quantity, reason, restock, payment adjustment, credit note, printed return receipt. Creates stock movements. The original invoice is never modified destructively.

**Voids:** Manager/Super Admin only. Requires reason, confirmation and an audit record. Invoice records are never physically deleted.

---

## 21. PRINTING

Three modes, selectable in Settings:

1. **ESC/POS** via python-escpos — USB (vendor/product id), network (IP + port 9100), or serial (port + baud).
2. **Browser print** — 58 mm / 80 mm receipt CSS with `@media print`.
3. **PDF** — A4 and thermal-size.

All application code calls `printer.print_receipt(invoice)` through a `PrinterService` abstraction — never a printer library directly.

Settings: printer mode, USB vendor id, USB product id, network IP, network port, serial port, baud rate, receipt size, cash drawer enabled.

**Test Print** button in Settings, printing a sample receipt with a clear success/error result.

**Print queue:** `print_jobs` needs a real worker — status (pending/printing/done/failed), attempt count, last error, background retry via `flask process-print-queue` (scheduled). Maximum 3 retries, then marked failed and surfaced in the UI. If the printer is offline the sale still completes; a **Reprint** button is available on any past invoice.

Receipt content: logo (rasterised for ESC/POS), organisation name, address, phone, NTN, invoice number, date/time, cashier, customer, item lines (name, qty, rate, amount), subtotal, discount, tax, round-off, grand total, payment modes, tendered, change, footer, optional QR/barcode, and a reserved FBR area (Section 25).

Cash drawer pulse after a cash sale, toggleable.

**Deliver `scripts/printer_smoke_test.py` at the end of Stage 1** — a standalone CLI that connects to the configured printer and prints a diagnostic slip. Printer driver problems must surface before the POS is built, not in Stage 6.

---

## 22. CUSTOMERS, SUPPLIERS, PURCHASES, EXPENSES

**Customers:** name, phone, email, address, opening balance, current balance, notes. Ledger of sales, returns, payments, outstanding, balance history. A walk-in customer exists by default.

**Suppliers:** name, phone, email, address, opening balance, current payable, notes. Ledger of purchases, payments, returns, balance.

**Purchases:** supplier, purchase invoice, date, items, quantity, cost, freight, other charges, tax, total. Automatically creates stock movements.

**Expenses:** manageable expense heads; date, head, amount, payment mode, description, attachment. Reflected in cash and financial reports.

---

## 23. SHIFTS & CASH DRAWER

**Open shift:** opening cash.
**During shift:** cash sales, cash returns, cash expenses, cash received, cash added/removed if permitted.
**Close shift:** counted cash → expected cash, counted cash, short/over. Printable shift-closing report.

A closed shift cannot be modified without Manager permission. Shift boundaries follow the business day cutoff.

---

## 24. DASHBOARD

Cards: today's sales, cash sales, card sales, invoice count, average bill, low-stock items, total stock value, outstanding receivables, today's expenses, sync status.

Charts (Chart.js): last 30 days sales, top 10 items, category-wise sales, payment mode distribution.

The dashboard respects user permissions.

---

## 25. FBR / TAX COMPLIANCE READINESS

Do **not** integrate any FBR API now. But do:

* Add to `invoices`: `fbr_invoice_no`, `fbr_status` (not_applicable/pending/sent/failed), `fbr_response`, `fbr_synced_at`, `pos_registration_no`, `terminal_code`.
* Add to Settings: POS registration number, FBR integration toggle (default off), FBR QR toggle.
* Reserve an area on the receipt template for the FBR QR code and invoice number, rendered only when enabled.
* Create `app/compliance/fbr.py` with a stubbed `FbrClient` raising `NotImplementedError`, following the payment gateway pattern.
* Document in the README what must change to enable it.

---

## 26. REPORTS

Every report supports: date range, relevant filters, search, on-screen table with a totals row, and export to **Excel, CSV, PDF and Print**. PDF and print carry the uploaded logo and organisation header. Daily reports use the business day (Section 10).

**Sales:** 1 Daily Sales Summary / Z-Report · 2 Sales Register · 3 Item-wise Sales · 4 Category-wise Sales · 5 Top Selling Items · 6 Slow Moving Items · 7 Sales by Payment Mode · 8 Sales by Cashier · 9 Hourly Sales · 10 Sales Returns · 11 Void Invoices · 12 Discount Report

**Inventory:** 13 Current Stock · 14 Inventory Valuation (at cost and at sale price) · 15 Low Stock · 16 Out of Stock · 17 Stock Ledger · 18 Stock Adjustments · 19 Wastage · 20 Expiry / Near Expiry

**Purchase & Finance:** 21 Purchase Register · 22 Supplier-wise Purchases · 23 Profit & Margin · 24 GST / Sales Tax · 25 Customer Outstanding · 26 Receivables Ageing (0-30/31-60/61-90/90+) · 27 Supplier Payables · 28 Expense Report · 29 Cash Drawer / Shift Report

**System:** 30 Audit Log · 31 User Activity · 32 Login History · 33 Sync History

---

## 27. AUDIT LOG

Track: login, logout, failed login, create, update, delete, stock adjustment, sale, return, void, discount above limit, settings change, user change, permission change, sync run.

Store: user, action, module, record id, old value, new value, IP address, timestamp.

Audit records are not editable from the UI.

---

# PART D — CENTRAL SYNC

## 28. TRANSPORT

Do **not** connect the local application directly to the remote Oracle listener over the internet.

* Endpoint on the central server: `POST /api/sync/push`
* Auth: per-terminal API token header **plus** an HMAC-SHA256 signature of the body using a shared secret. Both in `.env`, never in code.
* Body:

```json
{
  "batch_id": "01J...ULID",
  "terminal_code": "T01",
  "generated_at": "2026-09-19T19:00:00Z",
  "rows": [
    {"table": "items", "uid": "01J...", "op": "update", "row_version": 4, "data": {}}
  ]
}
```

* Also `GET /api/sync/status?terminal_code=T01` returning the last received batch and row counts.

## 29. SERVER-SIDE APPLY RULES

1. If `batch_id` already exists in `sync_batches` with status `applied`, **return the previous result and apply nothing.** A retried batch is harmless.
2. Apply the whole batch in **one transaction** — all rows land or none.
3. Per row, `MERGE` on `sync_uid` with a version guard:
   * no existing row → insert
   * exists and `incoming.row_version > existing.row_version` → update
   * `incoming.row_version <= existing.row_version` → **skip and log** (stale-replay guard)
4. Record the batch in `sync_batches` (batch_id, terminal_code, row count, applied, skipped, received_at, status) before responding.
5. Respond with per-row results.

Local outbox rows are marked `synced` **only** after a 2xx response naming those rows. Never optimistically.

## 30. ORDERING, BATCHING & RESILIENCE

Parents first, fixed order:

```
settings → roles → users → units → categories → suppliers → customers
→ items → purchases → purchase_items → shifts → invoices → invoice_items
→ payment_modes → invoice_payments → sales_returns → sales_return_items
→ stock_movements → expense_heads → expenses → audit_logs
```

A child whose parent `sync_uid` is missing on the server is rejected with `missing_parent` — never inserted as an orphan — and retried in the next batch.

* Batch size configurable, default 500 rows.
* On failure: exponential backoff, max 5 attempts per batch, then mark `failed` with the error and surface in the UI. Rows are never lost.
* Nightly scheduled run at a configurable time via `flask sync-push`, plus a manual **Sync Now** button.
* Safe to run while the POS is in use; a lock prevents two concurrent runs.
* Must survive days offline — a backlog of 10,000+ rows syncs correctly in ordered batches.

## 31. SYNC UI (Manager / Super Admin)

Last successful sync time and batch id · pending rows by table · failed rows with table, uid, error and a **Retry** button · sync history log (batch id, rows sent/applied/skipped, duration, result) · central server reachability check · a persistent warning badge in the top bar when the last successful sync is older than a configurable threshold (default 48 hours).

## 32. IMMUTABILITY PREFERENCE

To keep conflicts near zero: invoices, invoice lines, payments, returns and stock movements are **append-only**. Mistakes are corrected with a return or void, never by editing the original row. Editable entities (items, customers, suppliers, settings) sync by `row_version`. Never allow an edit that changes a historical invoice total after it has been synced.

---

# PART E — QUALITY, SECURITY & DELIVERY

## 33. BACKUP (ORACLE DATA PUMP — `mysqldump` DOES NOT EXIST)

* Create an Oracle `DIRECTORY` object for the backup folder, granted to the app schema. Document the exact SQL in the README.
* `flask backup-db` runs:
  ```
  expdp isi/****@service schemas=ISI directory=POS_BACKUP_DIR \
        dumpfile=isi_pos_<timestamp>.dmp logfile=isi_pos_<timestamp>.log
  ```
* `flask restore-db <file>` uses `impdp`, **requires `--confirm`**, and prints the target schema and service name before running.
* Verify each dump is non-empty and its log contains no `ORA-` errors; fail loudly otherwise.
* **Hourly** scheduled backup to a second physical location (external drive or separate disk), plus the nightly sync. Retention: last 24 hourly, last 14 daily.
* Note that `expdp` runs **server-side** and writes to a directory on the database host, not the client — account for this in the path design and document it.
* README must contain a **tested** restore drill and a disaster recovery procedure covering both restoring from local backup and rebuilding local from the central server.
* README must state that the on-site machine requires a UPS and periodic disk health checks. **The sync is not a backup.**

---

## 34. UI / UX

Must look like professional commercial institutional software — not a Bootstrap demo, not a student project, not a bare CRUD panel.

Fixed collapsible sidebar with grouped icon menu · top bar with logo, current user, shift status, sync status, date/time, logout · breadcrumbs · cards · tables with filters, search and pagination · modals · toast notifications (never `alert()`) · loading and skeleton states · empty states · confirmation dialogs before destructive actions · inline field-level validation errors.

Consistent design system: one configurable primary colour from Settings, 8px spacing scale, subtle shadows, deliberate visual hierarchy. Light and dark mode. Custom 403/404/500 pages with the logo.

Typography: include **Noto Nastaliq Urdu** so Urdu item names render correctly. Optional Urdu UI via Flask-Babel with RTL support.

Responsive — the POS must work on a tablet. `Rs.` formatting with Pakistani thousand separators throughout.

---

## 35. SECURITY

Password hashing · CSRF on every form · secure sessions · session timeout · login rate limiting · account lockout · RBAC · SQLAlchemy ORM only, no unsafe raw SQL · audit logging · secure error handling. Production never exposes tracebacks.

**Uploads:** allow PNG, JPG, JPEG, WEBP (and PDF for attachments). **SVG is not allowed** anywhere — it can embed scripts and creates a stored-XSS vector. Validate extension and MIME, enforce max size, generate UUID filenames, never trust the uploaded filename, never allow executables. Serve uploads through a route setting `X-Content-Type-Options: nosniff`; include the Nginx rule preventing execution from the upload directory.

Set a Content-Security-Policy; cookies `HttpOnly`, `Secure`, `SameSite=Lax`; HTTPS-only in production. Log failed logins with IP; never log passwords, card references or payment request bodies.

**Database privileges** — minimum necessary:

```sql
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW,
      CREATE PROCEDURE, CREATE TRIGGER TO isi;
ALTER USER isi QUOTA UNLIMITED ON <tablespace>;
```

No `DBA`, no `ANY` privileges, no `GRANT` option. Propose a separate runtime user with DML-only rights and ask before implementing.

---

## 36. PERFORMANCE TARGETS (ACCEPTANCE CRITERIA)

With 10,000 items and 100,000 invoice lines seeded:

* POS item search under 300 ms
* Adding a scanned item to the cart under 200 ms
* Sale completion (commit + receipt generation) under 1.5 s
* Any one-month report under 3 s

Provide `flask seed-demo --items 10000 --invoices 5000` to generate this load. Report measured numbers at the end of Stage 5 and Stage 9. If a target is missed, fix it before proceeding — do not defer.

---

## 37. TESTING

**Tests must run against a real Oracle schema — SQLite is not acceptable.** Empty-string-is-NULL, identifier folding, `MERGE`, `NUMBER` precision and locking all differ; SQLite-passing tests would give false confidence.

* Dedicated `ISI_POS_TEST` schema. `conftest.py` must **abort loudly** if the test service name or schema matches production.
* Each test rolls back its transaction or truncates its own tables. Production data is never touched.
* Provide the exact `CREATE USER` / `GRANT` script for the test schema in the README.

Required coverage: login · lockout · forced password change · CSRF · role permissions for all four roles · item create/edit · stock in/out/adjustment · stock deduction on sale · insufficient stock rejection · all six calculation rules · split payment validation · invoice number uniqueness and gaplessness · **transaction rollback when any part of a sale fails** · duplicate submission creates exactly one invoice and one stock movement set · return and restocking · void · customer outstanding · shift closing · audit logs.

Sync tests: same batch twice yields identical server state and zero duplicates · interrupted batch retried applies exactly once · stale batch (lower `row_version`) skipped and logged · missing-parent row rejected then succeeds next batch · ten days of offline data syncs completely in order · two terminal codes never collide · a rolled-back sale leaves no outbox entries · a soft delete propagates as a tombstone.

---

## 38. CLI COMMANDS

```
flask db-check                # connectivity test with clear error reporting
flask seed                    # roles, settings, units, payment modes, sample categories; prompts for admin password
flask seed-demo --items N --invoices N
flask backup-db               # Data Pump export
flask restore-db <file> --confirm
flask process-print-queue
flask sync-push
flask prune-outbox --older-than 90d --confirm
flask purge-demo-data --confirm
```

---

## 39. PROJECT STRUCTURE

```
isi_pos/
├── app/
│   ├── __init__.py            # create_app factory
│   ├── config.py              # Development / Production / Testing
│   ├── extensions.py          # db, migrate, login_manager, csrf, limiter
│   ├── models/                # user, setting, item, partner, inventory, purchase,
│   │                          # invoice, payment, sales_return, expense, shift,
│   │                          # audit, print_job, sync
│   ├── blueprints/
│   │   ├── auth/ dashboard/ items/ stock/ pos/ purchases/ customers/
│   │   ├── suppliers/ expenses/ reports/ settings/ users/ sync/
│   ├── services/
│   │   ├── calculation_service.py
│   │   ├── invoice_service.py
│   │   ├── stock_service.py
│   │   ├── printer_service.py
│   │   ├── sync_service.py
│   │   ├── audit_service.py
│   │   └── report_service.py
│   ├── payments/              # base.py, gateways.py, registry.py
│   ├── compliance/            # fbr.py (stub)
│   ├── utils/                 # decorators, security, timezone, ulid, formatters
│   ├── templates/             # base.html, auth/, errors/, ...
│   └── static/                # css/, js/, uploads/{logo,items}
├── scripts/printer_smoke_test.py
├── tests/                     # conftest.py + test modules
├── migrations/
├── .env.example
├── requirements.txt
├── wsgi.py
├── gunicorn.conf.py
├── nginx.sample.conf
├── CHANGELOG.md
├── DECISIONS.md
└── README.md
```

---

## 40. BUILD STAGES

| Stage | Contents |
|---|---|
| 1 | Pre-flight checks · structure · config · Oracle connection · full schema incl. `sync_uid`, `terminal_code`, `row_version`, `deleted_at`, `sync_outbox` · auth · RBAC · `CalculationService` · CLI commands · printer smoke test · CHANGELOG/DECISIONS |
| 2 | Branding/settings · dashboard shell |
| 3 | Categories · units · items · uploads · import/export · barcode labels |
| 4 | Inventory · stock ledger · adjustments |
| 5 | POS · invoices · payments · split payment · idempotency |
| 6 | Thermal printing · browser print · PDF · print queue worker |
| 7 | Returns · voids · held bills · shifts · cash drawer |
| 8 | Customers · suppliers · purchases · expenses |
| 9 | All reports · exports |
| 10 | Sync engine · API · scheduling · sync UI · audit · backups · security hardening · tests · deployment |

The identity columns, ULID generation, event listeners and outbox writes must all exist from **Stage 1**. Retrofitting identity columns onto live data later is dangerous and must be avoided.

### After every stage

1. State exactly what was built and list files changed.
2. List the acceptance criteria for that stage and mark each met / not met.
3. Give the commands to run it.
4. Give manual testing steps.
5. Run the automated tests and report **actual output**, not a claim.
6. List every assumption made where the spec was ambiguous.
7. Update `CHANGELOG.md` and `DECISIONS.md`.
8. **STOP and wait for my confirmation before the next stage.**

Once I confirm a stage, do not refactor it later without asking.

---

## 41. GO-LIVE PACKAGE

* `flask purge-demo-data --confirm` removing all seeded sample data so the system goes live clean.
* Go-live checklist: change all default passwords · verify DB privileges · confirm character set and timezone · set real branding · enter opening stock · set opening cash · test print · verify sync to central · take a backup.
* **User manual in Urdu and English** with screenshots for cashiers: make a sale, hold a bill, take a return, close a shift, reprint a receipt.
* One-page troubleshooting sheet: printer not printing · database not connecting · sync failing · barcode scanner not reading · stock mismatch.

---

## 42. FINAL DELIVERABLES

Complete runnable codebase · Alembic migrations · seed commands · full test suite · `.env.example` · pinned `requirements.txt` · README · `gunicorn.conf.py` · `nginx.sample.conf` · systemd unit example · printer setup instructions · Oracle setup, privileges and character set instructions · backup and tested restore procedure · sync setup and scheduling instructions · payment gateway integration instructions · FBR integration instructions · role documentation · Urdu/English user manual · `CHANGELOG.md` · `DECISIONS.md`.

---

**Begin with Section 3 (pre-flight checks). Do not create any table until those are answered and approved. Then build Stage 1 only, and stop.**