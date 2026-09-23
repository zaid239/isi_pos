"""
Audit Trail Model: AuditLog.
Records all security, configuration, and transactional changes.
Complies with Oracle 19c reserved word rules:
- Table name audit_logs (avoids reserved word AUDIT)
- Column action_name (avoids reserved word ACTION)
- Columns old_val and new_val mapped to CLOB (instruction j)
- Carries row_version as required by instruction g
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener


@register_outbox_listener
class AuditLog(SyncMixin, db.Model):
    __tablename__ = "audit_logs"

    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True, index=True)
    action_name = db.Column(db.String(50), nullable=False, index=True)  # Avoid reserved ACTION
    module_name = db.Column(db.String(50), nullable=False, index=True)
    record_id = db.Column(db.String(50), nullable=True)

    old_val = db.Column(db.Text, nullable=True)  # CLOB in Oracle (instruction j)
    new_val = db.Column(db.Text, nullable=True)  # CLOB in Oracle (instruction j)

    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    user = db.relationship("User")

    def __repr__(self):
        return f"<AuditLog {self.module_name}.{self.action_name} user={self.user_id}>"
