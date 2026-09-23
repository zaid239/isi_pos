"""
Financial Calculation Service.
Implements the 6 non-negotiable arithmetic rules strictly using decimal.Decimal and ROUND_HALF_UP.
No floating-point operations are ever permitted for financial or quantity calculations.
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

# Precision constants
TWO_PLACES = Decimal("0.01")
THREE_PLACES = Decimal("0.001")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100.00")
ONE = Decimal("1.00")


def to_decimal(val: Any) -> Decimal:
    """Safely converts input to Decimal."""
    if isinstance(val, Decimal):
        return val
    if val is None or val == "":
        return ZERO
    return Decimal(str(val))


def quantize_money(val: Decimal) -> Decimal:
    """Rounds currency amounts to 2 decimal places using ROUND_HALF_UP."""
    return val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def quantize_qty(val: Decimal) -> Decimal:
    """Rounds quantity amounts to 3 decimal places using ROUND_HALF_UP."""
    return val.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class CalculatedLine:
    item_id: int
    quantity: Decimal
    unit_price: Decimal
    gross_amount: Decimal
    line_discount: Decimal
    bill_discount_allocated: Decimal
    taxable_value: Decimal
    tax_percent: Decimal
    tax_amount: Decimal
    line_total: Decimal


@dataclass
class CalculationResult:
    lines: List[CalculatedLine]
    gross_total: Decimal
    line_discount_total: Decimal
    bill_discount_total: Decimal
    taxable_total: Decimal
    tax_total: Decimal
    subtotal_before_round: Decimal
    round_off: Decimal
    payable_total: Decimal


class CalculationService:
    """
    Centralized financial arithmetic engine.
    Never duplicate this logic in routes, templates, or frontend code.
    """

    @staticmethod
    def calculate_line(
        quantity: Any,
        unit_price: Any,
        line_discount: Any = 0,
        tax_percent: Any = 0,
        is_tax_inclusive: bool = False,
        bill_discount_alloc: Any = 0,
    ) -> CalculatedLine:
        """
        Rule 1: gross = qty * rate -> minus line discount -> taxable value -> tax = taxable * tax% -> line total = taxable + tax.
        Rule 5: If tax-inclusive, taxable value = price / (1 + tax%).
        """
        qty = quantize_qty(to_decimal(quantity))
        price = to_decimal(unit_price)
        line_disc = to_decimal(line_discount)
        bill_disc = to_decimal(bill_discount_alloc)
        tax_pct = to_decimal(tax_percent)

        gross = qty * price

        if is_tax_inclusive:
            # Rule 5: price includes tax; taxable value = gross_after_discounts / (1 + tax%)
            # gross_net_of_disc = (qty * price) - line_disc - bill_disc
            gross_after_disc = gross - line_disc - bill_disc
            if gross_after_disc < ZERO:
                gross_after_disc = ZERO

            tax_divisor = ONE + (tax_pct / HUNDRED)
            taxable_val = gross_after_disc / tax_divisor if tax_divisor > 0 else gross_after_disc
            tax_amt = gross_after_disc - taxable_val
            line_tot = gross_after_disc
        else:
            # Tax-exclusive calculation
            gross_after_disc = gross - line_disc - bill_disc
            if gross_after_disc < ZERO:
                gross_after_disc = ZERO

            taxable_val = gross_after_disc
            tax_amt = taxable_val * (tax_pct / HUNDRED)
            line_tot = taxable_val + tax_amt

        # Rule 3: Round at line level to 2 decimals using ROUND_HALF_UP
        q_gross = quantize_money(gross)
        q_line_disc = quantize_money(line_disc)
        q_bill_disc = quantize_money(bill_disc)
        q_taxable = quantize_money(taxable_val)
        q_tax = quantize_money(tax_amt)
        q_total = quantize_money(line_tot)

        # Re-ensure total equals taxable + tax after quantization
        if not is_tax_inclusive:
            q_total = quantize_money(q_taxable + q_tax)

        return CalculatedLine(
            item_id=0,
            quantity=qty,
            unit_price=price,
            gross_amount=q_gross,
            line_discount=q_line_disc,
            bill_discount_allocated=q_bill_disc,
            taxable_value=q_taxable,
            tax_percent=tax_pct,
            tax_amount=q_tax,
            line_total=q_total,
        )

    @classmethod
    def calculate_bill(
        cls,
        raw_lines: List[Dict[str, Any]],
        bill_discount: Any = 0,
        is_tax_inclusive: bool = False,
        cash_rounding_mode: str = "off",  # 'off', 'nearest_1', 'nearest_5'
    ) -> CalculationResult:
        """
        Calculates complete bill according to all 6 rules:
        - Rule 2: Bill-level discount distributed proportionally across lines BEFORE tax.
        - Rule 3: Line rounding, and bill total is the exact sum of rounded lines.
        - Rule 4: Cash rounding (off, nearest 1, nearest 5) with round_off column tracking.
        """
        total_bill_disc = to_decimal(bill_discount)

        # First pass: calculate gross minus line discounts to establish weights for bill discount
        line_bases: List[Decimal] = []
        for line in raw_lines:
            q = quantize_qty(to_decimal(line.get("quantity", 0)))
            p = to_decimal(line.get("unit_price", 0))
            ld = to_decimal(line.get("line_discount", 0))
            base = (q * p) - ld
            line_bases.append(base if base > ZERO else ZERO)

        sum_bases = sum(line_bases)

        # Rule 2: Distribute bill discount proportionally before tax
        bill_disc_allocated: List[Decimal] = []
        if sum_bases > ZERO and total_bill_disc > ZERO:
            allocated_so_far = ZERO
            for i, base in enumerate(line_bases):
                if i == len(line_bases) - 1:
                    # Last line takes remainder to avoid rounding drift
                    share = total_bill_disc - allocated_so_far
                else:
                    share = quantize_money(total_bill_disc * (base / sum_bases))
                    allocated_so_far += share
                bill_disc_allocated.append(share)
        else:
            bill_disc_allocated = [ZERO] * len(raw_lines)

        # Second pass: Compute each line with its allocated bill discount
        calculated_lines: List[CalculatedLine] = []
        for i, line in enumerate(raw_lines):
            calc_line = cls.calculate_line(
                quantity=line.get("quantity", 0),
                unit_price=line.get("unit_price", 0),
                line_discount=line.get("line_discount", 0),
                tax_percent=line.get("tax_percent", 0),
                is_tax_inclusive=is_tax_inclusive,
                bill_discount_alloc=bill_disc_allocated[i],
            )
            calc_line.item_id = line.get("item_id", 0)
            calculated_lines.append(calc_line)

        # Rule 3: Bill total is the sum of rounded lines
        gross_tot = sum(l.gross_amount for l in calculated_lines)
        line_disc_tot = sum(l.line_discount for l in calculated_lines)
        bill_disc_tot = sum(l.bill_discount_allocated for l in calculated_lines)
        taxable_tot = sum(l.taxable_value for l in calculated_lines)
        tax_tot = sum(l.tax_amount for l in calculated_lines)
        subtotal_before_round = sum(l.line_total for l in calculated_lines)

        # Rule 4: Cash rounding
        payable, round_diff = cls.apply_cash_rounding(subtotal_before_round, cash_rounding_mode)

        return CalculationResult(
            lines=calculated_lines,
            gross_total=gross_tot,
            line_discount_total=line_disc_tot,
            bill_discount_total=bill_disc_tot,
            taxable_total=taxable_tot,
            tax_total=tax_tot,
            subtotal_before_round=subtotal_before_round,
            round_off=round_diff,
            payable_total=payable,
        )

    @staticmethod
    def apply_cash_rounding(amount: Decimal, mode: str = "off") -> tuple[Decimal, Decimal]:
        """
        Rule 4: Rounds payable amount based on institutional settings.
        Modes:
        - 'off': no rounding
        - 'nearest_1': rounds to nearest integer rupee (0.50 -> 1.00)
        - 'nearest_5': rounds to nearest 5 rupees
        Returns: (payable_total, round_off_difference)
        """
        amount = quantize_money(amount)
        if mode == "nearest_1":
            rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            round_off = quantize_money(rounded - amount)
            return rounded, round_off
        elif mode == "nearest_5":
            # Round to nearest 5: (amount / 5).quantize(1) * 5
            divided = (amount / Decimal("5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            rounded = quantize_money(divided * Decimal("5"))
            round_off = quantize_money(rounded - amount)
            return rounded, round_off
        else:
            return amount, ZERO

    @staticmethod
    def validate_payment_total(payable: Any, payments: List[Any]) -> bool:
        """
        Rule 6: Server-side validation asserting sum(payments.amount) == invoice.payable.
        Rejects transaction if sums do not match exactly.
        """
        payable_dec = quantize_money(to_decimal(payable))
        payments_sum = sum(quantize_money(to_decimal(p)) for p in payments)
        return payable_dec == payments_sum
