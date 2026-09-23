"""
Setting Service.
Centralized, dynamic configuration and branding manager backed by the Oracle 'settings' table.
Exposes settings to Jinja context processor, templates, and backend logic with zero hardcoding.
Logs configuration modifications into the 'audit_logs' table.
"""
import json
from decimal import Decimal
from typing import Dict, Any, Optional
from app.extensions import db
from app.models.setting import Setting
from app.models.audit import AuditLog
from app.utils.timezone import utc_now

DEFAULT_SYSTEM_SETTINGS = {
    # Branding
    "org_name_en": ("My Store", "string", "branding", "Organization name in English"),
    "org_name_ur": ("ادارہ جاتی پوائنٹ آف سیل", "string", "branding", "Organization name in Urdu"),
    "brand_icon_black_url": ("", "string", "branding", "Black transparent icon URL"),
    "brand_icon_white_url": ("", "string", "branding", "White transparent icon URL"),
    "brand_wordmark_black_url": ("", "string", "branding", "Black transparent wordmark URL"),
    "brand_wordmark_white_url": ("", "string", "branding", "White transparent wordmark URL"),
    "brand_favicon_url": ("", "string", "branding", "Browser favicon URL generated from icon"),
    "org_logo_url": ("/static/images/default-logo.png", "string", "branding", "Organization brand logo path"),
    "org_favicon_url": ("/static/images/favicon.ico", "string", "branding", "Favicon icon path"),
    "theme_color_primary": ("#1e40af", "string", "branding", "Primary brand color (hex)"),
    "theme_color_secondary": ("#0d9488", "string", "branding", "Secondary accent color (hex)"),
    "org_address": ("Mall Road, Lahore, Pakistan", "string", "branding", "Physical store / institution address"),
    "org_phone": ("+92 42 111 000 000", "string", "branding", "Contact telephone number"),
    "org_email": ("pos@institution.edu.pk", "string", "branding", "Contact email address"),
    "org_ntn": ("1234567-8", "string", "branding", "National Tax Number (NTN)"),
    "org_strn": ("3277876543210", "string", "branding", "Sales Tax Registration Number (STRN)"),

    # Financial & Currency Defaults
    "currency_symbol": ("Rs.", "string", "financial", "Currency symbol displayed on bills and reports"),
    "decimal_places": (2, "int", "financial", "Number of decimal places for currency display"),
    "thousand_separator": (",", "string", "financial", "Thousand separator character"),
    "default_tax_percent": (Decimal("0.00"), "float", "financial", "Default sales tax percentage"),
    "tax_inclusive": (False, "bool", "financial", "Whether catalog prices include sales tax"),
    "cash_rounding_mode": ("off", "string", "financial", "Cash payment rounding mode: off, nearest_1, nearest_5"),

    # Invoicing & Counter Rules
    "invoice_prefix": ("INV", "string", "invoicing", "Invoice series prefix code"),
    "invoice_padding": (6, "int", "invoicing", "Zero padding for document sequence numbers"),
    "invoice_reset_rule": ("yearly", "string", "invoicing", "Sequence counter reset cadence: yearly or never"),
    "fiscal_year_start": ("07-01", "string", "invoicing", "Fiscal year commencement date (MM-DD)"),
    "business_day_cutoff": ("04:00", "string", "invoicing", "Daily cutoff time for sales attribution (HH:MM)"),
    "low_stock_threshold": (10, "int", "inventory", "Threshold below which low-stock alerts trigger"),
    "cashier_max_discount_percent": (Decimal("10.00"), "decimal", "pos", "Maximum discount percentage a cashier can give without manager approval"),

    # Receipt Layout & Compliance
    "receipt_header": ("Welcome to Institutional POS", "string", "receipt", "Header line printed on receipts"),
    "receipt_footer": ("Thank you for your visit!", "string", "receipt", "Footer line printed on receipts"),
    "return_policy": ("Exchange allowed within 7 days with original receipt.", "string", "receipt", "Return and exchange policy"),
    "receipt_paper_size": ("80mm", "string", "receipt", "Thermal printer paper width (80mm or 58mm)"),
    "print_logo_on_receipt": (False, "bool", "receipt", "Print organization logo on thermal receipts"),
    "pos_reg_no": ("POS-REG-001", "string", "compliance", "POS terminal registration number"),
    "fbr_enabled": (False, "bool", "compliance", "Enable live FBR Tier-1 fiscal invoice reporting"),
}


class SettingService:
    _cache: Optional[Dict[str, Any]] = None

    @classmethod
    def invalidate_cache(cls):
        """Invalidates the in-memory cache so subsequent reads reload from database."""
        cls._cache = None

    @classmethod
    def get_all_settings(cls) -> Dict[str, Any]:
        """
        Returns all system settings as a unified typed dictionary for the context processor.
        Cached in-memory: loaded once per process and invalidated on save.
        """
        if cls._cache is not None:
            return dict(cls._cache)

        settings_dict = {}
        # Start with baseline defaults
        for k, v in DEFAULT_SYSTEM_SETTINGS.items():
            settings_dict[k] = v[0]

        try:
            records = Setting.query.filter_by(deleted_at=None).all()
            for r in records:
                if r.setting_val is not None:
                    t = r.setting_type
                    val = r.setting_val
                    if t == "bool":
                        settings_dict[r.setting_key] = val.lower() in ("true", "1", "yes")
                    elif t == "int":
                        try:
                            settings_dict[r.setting_key] = int(val)
                        except ValueError:
                            pass
                    elif t in ("float", "decimal", "numeric"):
                        try:
                            settings_dict[r.setting_key] = Decimal(val)
                        except Exception:
                            pass
                    else:
                        settings_dict[r.setting_key] = val
        except Exception:
            pass

        cls._cache = settings_dict
        return dict(cls._cache)

    @classmethod
    def get_value(cls, key: str, default=None):
        """
        Retrieves a typed setting value from the in-memory cache, falling back to default schema config.
        """
        all_settings = cls.get_all_settings()
        if key in all_settings:
            return all_settings[key]
        if key in DEFAULT_SYSTEM_SETTINGS:
            return DEFAULT_SYSTEM_SETTINGS[key][0]
        return default

    @classmethod
    def update_settings(
        cls,
        new_values: Dict[str, Any],
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates multiple settings in a single transaction.
        Writes change audit record into 'audit_logs' if any setting value changed.
        """
        changes_old = {}
        changes_new = {}

        for key, val in new_values.items():
            str_val = str(val) if val is not None else ""
            record = Setting.query.filter_by(setting_key=key).first()

            if record:
                old_str = record.setting_val or ""
                if old_str != str_val:
                    changes_old[key] = old_str
                    changes_new[key] = str_val
                    record.setting_val = str_val
                    record.updated_by = user_id
                    record.updated_at = utc_now()
                    record.row_version = (record.row_version or 1) + 1
            else:
                # Discover meta from defaults
                meta = DEFAULT_SYSTEM_SETTINGS.get(key, (str_val, "string", "general", key))
                new_record = Setting(
                    setting_key=key,
                    setting_val=str_val,
                    setting_type=meta[1],
                    group_name=meta[2],
                    description=meta[3],
                    created_by=user_id,
                    updated_by=user_id,
                )
                db.session.add(new_record)
                changes_old[key] = None
                changes_new[key] = str_val

        # Create Audit Log if changes occurred
        if changes_new:
            audit = AuditLog(
                user_id=user_id,
                action_name="update_settings",
                module_name="settings",
                record_id="system_settings",
                old_val=json.dumps(changes_old, ensure_ascii=False),
                new_val=json.dumps(changes_new, ensure_ascii=False),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.add(audit)

        db.session.commit()
        cls.invalidate_cache()
        return cls.get_all_settings()
