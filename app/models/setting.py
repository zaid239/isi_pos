"""
System and Organization Settings Model.
Provides dynamic, non-hardcoded configuration for branding, printer, FBR, and tax rules.
Complies with Oracle 19c rules (setting_val mapped to CLOB, avoid reserved keyword 'GROUP').
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener


@register_outbox_listener
class Setting(SyncMixin, db.Model):
    __tablename__ = "settings"

    setting_key = db.Column(db.String(50), unique=True, nullable=False, index=True)
    setting_val = db.Column(db.Text, nullable=True)  # CLOB in Oracle for arbitrary length content
    setting_type = db.Column(db.String(20), nullable=False, default="string")  # string, int, float, bool, json
    group_name = db.Column(db.String(50), nullable=False, default="general", index=True)  # Avoid reserved word GROUP
    description = db.Column(db.String(255), nullable=True)
    is_public = db.Column(db.SmallInteger, nullable=False, default=0)

    __table_args__ = (
        db.CheckConstraint("is_public IN (0, 1)", name="ck_settings_is_public"),
    )

    @classmethod
    def get_value(cls, key: str, default=None):
        record = cls.query.filter_by(setting_key=key, deleted_at=None).first()
        if not record or record.setting_val is None:
            return default

        val = record.setting_val
        t = record.setting_type
        if t == "bool":
            return val.lower() in ("true", "1", "yes")
        elif t == "int":
            try:
                return int(val)
            except ValueError:
                return default
        elif t in ("float", "decimal", "numeric"):
            try:
                from decimal import Decimal
                return Decimal(val)
            except Exception:
                return default
        return val

    @classmethod
    def set_value(cls, key: str, value, setting_type: str = "string", group_name: str = "general", description: str = None, user_id=None):
        record = cls.query.filter_by(setting_key=key).first()
        str_val = str(value) if value is not None else None
        if record:
            record.setting_val = str_val
            record.setting_type = setting_type
            record.group_name = group_name
            if description:
                record.description = description
            record.updated_by = user_id
            record.row_version = (record.row_version or 1) + 1
        else:
            record = cls(
                setting_key=key,
                setting_val=str_val,
                setting_type=setting_type,
                group_name=group_name,
                description=description,
                created_by=user_id,
            )
            db.session.add(record)
        db.session.commit()
        try:
            from app.services.setting_service import SettingService
            SettingService.invalidate_cache()
        except Exception:
            pass
        return record

    def __repr__(self):
        return f"<Setting {self.setting_key}={self.setting_val}>"
