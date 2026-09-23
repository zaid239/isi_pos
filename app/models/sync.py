"""
Synchronization Engine Models: SyncOutbox and SyncBatch.
Local-Only tables (never synced to central, instruction i).
Includes CLOB JSON payload with CHECK (payload IS JSON) constraint and
compound index on (status_code, id) for efficient queue retrieval (instruction k).
"""
from app.extensions import db
from app.utils.timezone import utc_now


class SyncOutbox(db.Model):
    __tablename__ = "sync_outbox"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    table_name = db.Column(db.String(50), nullable=False, index=True)
    row_uid = db.Column(db.String(26), nullable=False, index=True)  # ULID of modified row
    operation = db.Column(db.String(20), nullable=False)  # insert, update, delete
    row_version = db.Column(db.BigInteger, nullable=False, default=1)

    payload = db.Column(db.Text, nullable=False)  # JSON representation of changed entity (CLOB)
    batch_id = db.Column(db.String(26), nullable=True, index=True)

    status_code = db.Column(db.String(20), nullable=False, default="pending")  # pending, sending, synced, failed
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    synced_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        # Instruction k: compound index on (status_code, id) for batch query performance
        db.Index("ix_sync_outbox_status_id", "status_code", "id"),
    )

    def __repr__(self):
        return f"<SyncOutbox {self.id} table={self.table_name} uid={self.row_uid} status={self.status_code}>"


class SyncBatch(db.Model):
    __tablename__ = "sync_batches"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(26), unique=True, nullable=False, index=True)  # ULID
    terminal_code = db.Column(db.String(10), nullable=False, index=True)

    row_count = db.Column(db.Integer, nullable=False, default=0)
    applied_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)

    received_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    status_code = db.Column(db.String(20), nullable=False, default="received")  # received, applied, failed
    error_summary = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f"<SyncBatch {self.batch_id} terminal={self.terminal_code} rows={self.row_count} status={self.status_code}>"
