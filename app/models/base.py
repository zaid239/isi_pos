"""
Base Model Definitions and Universal Sync Mixin for Offline-First Architecture.
Enforces Oracle 19c constraints:
- Identity primary key (local only)
- ULID sync_uid (universal across local and central)
- Monotonic row_version for optimistic concurrency and sync conflict resolution
- UTC timestamps
- Soft deletion support (tombstone pattern)
"""
import json
from datetime import datetime, timezone
from sqlalchemy import event
from app.extensions import db
from app.utils.ulid import generate_ulid
from app.utils.timezone import utc_now

LOCAL_ONLY_TABLES = {
    "held_bills",
    "doc_counters",
    "print_jobs",
    "sync_outbox",
    "sync_batches",
}


def get_current_terminal_code():
    """
    Resolves the active terminal code from current_app.config with NO hardcoded default.
    Raises RuntimeError if misconfigured.
    """
    from flask import current_app, has_app_context
    if has_app_context():
        code = current_app.config.get("TERMINAL_CODE")
        if code:
            return code
    code = os.environ.get("TERMINAL_CODE")
    if code:
        return code
    raise RuntimeError(
        "CRITICAL ERROR: 'TERMINAL_CODE' is not configured. "
        "Every sync row and document counter must be explicitly bound to a configured terminal."
    )


def get_current_counter_code():
    """
    Resolves the active POS counter lane code from current_app.config with NO hardcoded default.
    Raises RuntimeError if misconfigured.
    """
    from flask import current_app, has_app_context
    if has_app_context():
        code = current_app.config.get("COUNTER_CODE")
        if code:
            return code
    code = os.environ.get("COUNTER_CODE")
    if code:
        return code
    raise RuntimeError(
        "CRITICAL ERROR: 'COUNTER_CODE' is not configured. "
        "Every POS counter transaction and document sequence must be explicitly bound to a configured counter."
    )


class SyncMixin:
    """
    Universal mixin applied to all 25 synced business tables.
    Guarantees every syncable record carries unique sync identity and version tracking.
    """
    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    sync_uid = db.Column(db.String(26), unique=True, nullable=False, default=generate_ulid, index=True)
    terminal_code = db.Column(db.String(10), nullable=False, default=get_current_terminal_code)
    row_version = db.Column(db.BigInteger, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)
    created_by = db.Column(db.BigInteger, nullable=True)
    updated_by = db.Column(db.BigInteger, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, user_id=None):
        """Marks record as deleted and increments row_version to replicate tombstone."""
        self.deleted_at = utc_now()
        self.updated_at = utc_now()
        self.updated_by = user_id
        self.row_version = (self.row_version or 1) + 1

    def to_sync_dict(self) -> dict:
        """Serializes model attributes to dictionary for JSON outbox payload."""
        data = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name)
            if isinstance(val, datetime):
                val = val.isoformat()
            elif hasattr(val, "__float__") and not isinstance(val, (int, bool)):
                val = str(val)
            data[col.name] = val
        return data


def register_outbox_listener(cls):
    """
    Registers ORM event listeners on a synced model to automatically insert
    entries into sync_outbox INSIDE the active database transaction
    and monotonically increment row_version on updates.
    """
    @event.listens_for(cls, "before_update")
    def receive_before_update(mapper, connection, target):
        target.row_version = (target.row_version or 0) + 1

    @event.listens_for(cls, "after_insert")
    def receive_after_insert(mapper, connection, target):
        _write_outbox(connection, target, "insert")

    @event.listens_for(cls, "after_update")
    def receive_after_update(mapper, connection, target):
        op = "delete" if getattr(target, "deleted_at", None) is not None else "update"
        _write_outbox(connection, target, op)

    return cls


def _write_outbox(connection, target, operation: str):
    """Executes atomic outbox row insertion using the active database connection."""
    from app.models.sync import SyncOutbox

    table_name = target.__tablename__
    if table_name in LOCAL_ONLY_TABLES:
        return

    payload_json = json.dumps(target.to_sync_dict())
    connection.execute(
        SyncOutbox.__table__.insert().values(
            table_name=table_name,
            row_uid=target.sync_uid,
            operation=operation,
            row_version=target.row_version,
            payload=payload_json,
            batch_id=None,
            status_code="pending",
            attempts=0,
            last_error=None,
            created_at=utc_now(),
            synced_at=None,
        )
    )
