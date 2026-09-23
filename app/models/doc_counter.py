"""
Document Sequence Counter Model: DocCounter.
Local-Only table (never synced to central, instruction i).
Ensures gapless, audit-compliant document number generation without Oracle sequence caching gaps.
"""
from sqlalchemy import UniqueConstraint
from app.extensions import db
from app.models.base import get_current_terminal_code, get_current_counter_code
from app.utils.timezone import utc_now


class DocCounter(db.Model):
    __tablename__ = "doc_counters"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    terminal_code = db.Column(db.String(10), nullable=False, default=get_current_terminal_code)
    counter_code = db.Column(db.String(10), nullable=False, default=get_current_counter_code)
    doc_type = db.Column(db.String(30), nullable=False)  # invoice, purchase, return, adjustment, expense
    fiscal_year = db.Column(db.String(10), nullable=False)  # e.g., '2526'
    next_no = db.Column(db.BigInteger, nullable=False, default=1)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("terminal_code", "counter_code", "doc_type", "fiscal_year", name="uq_doc_counter_comb"),
    )

    @classmethod
    def allocate_next_number(
        cls, terminal_code: str = None, doc_type: str = "invoice", fiscal_year: str = None, counter_code: str = None, padding: int = 6
    ) -> str:
        if not terminal_code:
            terminal_code = get_current_terminal_code()
        if not counter_code:
            counter_code = get_current_counter_code()
        """
        Executes a row-locked atomic increment inside the active sale/document transaction.
        SELECT ... FOR UPDATE prevents sequence gaps upon server restarts.
        Keyed per counter_code so concurrent registers never contend on the same row.
        Format: ISI-{terminal_code}-{counter_code}-{fiscal_year}-{number:06d} (e.g. ISI-T01-C01-2526-000001)
        """
        counter = cls.query.filter_by(
            terminal_code=terminal_code,
            counter_code=counter_code,
            doc_type=doc_type,
            fiscal_year=fiscal_year
        ).with_for_update().one_or_none()


        if not counter:
            counter = cls(
                terminal_code=terminal_code,
                counter_code=counter_code,
                doc_type=doc_type,
                fiscal_year=fiscal_year,
                next_no=1,
            )
            db.session.add(counter)
            current_num = 1
            counter.next_no = 2
        else:
            current_num = counter.next_no
            counter.next_no += 1

        counter.updated_at = utc_now()
        from app.services.setting_service import SettingService
        prefix = SettingService.get_value("invoice_prefix", "INV") or "INV"
        return f"{prefix}-{current_num:0{padding}d}"

    def __repr__(self):
        return f"<DocCounter {self.terminal_code}:{self.counter_code}:{self.doc_type}:{self.fiscal_year} next={self.next_no}>"
