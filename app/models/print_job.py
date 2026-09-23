"""
Hardware Print Job Queue Model: PrintJob.
Local-Only table (never synced to central server, instruction i).
Spools receipt and report print tasks with automatic retry handling (Section 21 & 38.7).
"""
from app.extensions import db
from app.utils.timezone import utc_now


class PrintJob(db.Model):
    __tablename__ = "print_jobs"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=True, index=True)
    printer_name = db.Column(db.String(100), nullable=False, default="thermal_default")
    job_type = db.Column(db.String(30), nullable=False, default="receipt")  # receipt, report, test_slip

    job_status = db.Column(db.String(20), nullable=False, default="pending", index=True)  # pending, printing, done, failed
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.String(255), nullable=True)

    payload = db.Column(db.Text, nullable=False)  # Formatted receipt text or ESC/POS binary data (CLOB)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    invoice = db.relationship("Invoice")

    def __repr__(self):
        return f"<PrintJob id={self.id} type={self.job_type} status={self.job_status} attempts={self.attempt_count}>"
