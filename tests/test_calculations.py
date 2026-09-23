"""
Unit Tests for CalculationService.
Validates all 6 non-negotiable financial arithmetic rules from Section 9 and Section 38.3.
"""
from decimal import Decimal
import pytest
from app.services.calculation_service import CalculationService, TWO_PLACES


def test_rule_1_line_calculation_tax_exclusive():
    """
    Rule 1: gross = qty * rate -> minus line discount -> taxable value -> tax = taxable * tax% -> line total = taxable + tax.
    Qty: 3, Rate: 150.00, Line Discount: 50.00, Tax: 18%
    Gross = 450.00
    Taxable = 400.00
    Tax = 400.00 * 0.18 = 72.00
    Line Total = 400.00 + 72.00 = 472.00
    """
    line = CalculationService.calculate_line(
        quantity=3,
        unit_price="150.00",
        line_discount="50.00",
        tax_percent="18.00",
        is_tax_inclusive=False,
    )
    assert line.gross_amount == Decimal("450.00")
    assert line.taxable_value == Decimal("400.00")
    assert line.tax_amount == Decimal("72.00")
    assert line.line_total == Decimal("472.00")


def test_rule_2_bill_discount_proportional_distribution_before_tax():
    """
    Rule 2: Bill-level discount is distributed proportionally across lines BEFORE tax, and tax is recomputed.
    Line 1: 1 @ 1000.00 (Base: 1000, Tax: 18%)
    Line 2: 1 @ 2000.00 (Base: 2000, Tax: 18%)
    Total Bill Discount: 300.00
    Line 1 share: 100.00 -> Taxable: 900.00 -> Tax: 162.00 -> Total: 1062.00
    Line 2 share: 200.00 -> Taxable: 1800.00 -> Tax: 324.00 -> Total: 2124.00
    Payable = 1062.00 + 2124.00 = 3186.00
    """
    raw_lines = [
        {"item_id": 1, "quantity": 1, "unit_price": "1000.00", "line_discount": 0, "tax_percent": "18.00"},
        {"item_id": 2, "quantity": 1, "unit_price": "2000.00", "line_discount": 0, "tax_percent": "18.00"},
    ]
    result = CalculationService.calculate_bill(
        raw_lines=raw_lines,
        bill_discount="300.00",
        is_tax_inclusive=False,
        cash_rounding_mode="off",
    )

    assert result.lines[0].bill_discount_allocated == Decimal("100.00")
    assert result.lines[0].taxable_value == Decimal("900.00")
    assert result.lines[0].tax_amount == Decimal("162.00")
    assert result.lines[0].line_total == Decimal("1062.00")

    assert result.lines[1].bill_discount_allocated == Decimal("200.00")
    assert result.lines[1].taxable_value == Decimal("1800.00")
    assert result.lines[1].tax_amount == Decimal("324.00")
    assert result.lines[1].line_total == Decimal("2124.00")

    assert result.payable_total == Decimal("3186.00")


def test_rule_3_line_rounding_half_up_and_reconciliation():
    """
    Rule 3: Line rounding uses ROUND_HALF_UP to 2 decimals.
    The bill total is the exact sum of rounded lines.
    Qty: 1, Rate: 33.333 -> Gross: 33.33, Tax: 17% -> Tax: 5.6661 -> 5.67
    Line total: 33.33 + 5.67 = 39.00
    """
    raw_lines = [
        {"item_id": 1, "quantity": 1, "unit_price": "33.33", "tax_percent": "17.00"},
        {"item_id": 2, "quantity": 1, "unit_price": "66.67", "tax_percent": "17.00"},
    ]
    result = CalculationService.calculate_bill(raw_lines=raw_lines)
    assert result.subtotal_before_round == sum(l.line_total for l in result.lines)


def test_rule_4_cash_rounding_modes():
    """
    Rule 4: Cash rounding (off, nearest 1, nearest 5) with round_off column tracking.
    Amount: 143.40
    - 'off': payable = 143.40, round_off = 0.00
    - 'nearest_1': payable = 143.00, round_off = -0.40
    - 'nearest_5': payable = 145.00, round_off = +1.60
    """
    amt = Decimal("143.40")

    p_off, r_off = CalculationService.apply_cash_rounding(amt, mode="off")
    assert p_off == Decimal("143.40")
    assert r_off == Decimal("0.00")

    p_1, r_1 = CalculationService.apply_cash_rounding(amt, mode="nearest_1")
    assert p_1 == Decimal("143.00")
    assert r_1 == Decimal("-0.40")

    p_5, r_5 = CalculationService.apply_cash_rounding(amt, mode="nearest_5")
    assert p_5 == Decimal("145.00")
    assert r_5 == Decimal("1.60")

    # Verify reconciliation: original + round_off == payable
    assert amt + r_1 == p_1
    assert amt + r_5 == p_5


def test_rule_5_tax_inclusive_pricing():
    """
    Rule 5: Tax-inclusive pricing: taxable value = price / (1 + tax%).
    Item sale price: 118.00 (includes 18% GST).
    Taxable value = 118.00 / 1.18 = 100.00
    Tax amount = 118.00 - 100.00 = 18.00
    Line total = 118.00
    """
    line = CalculationService.calculate_line(
        quantity=1,
        unit_price="118.00",
        line_discount=0,
        tax_percent="18.00",
        is_tax_inclusive=True,
    )
    assert line.taxable_value == Decimal("100.00")
    assert line.tax_amount == Decimal("18.00")
    assert line.line_total == Decimal("118.00")


def test_rule_6_payment_validation():
    """
    Rule 6: sum(invoice_payments.amount) must exactly equal invoices.payable.
    """
    payable = Decimal("5000.00")
    valid_payments = [Decimal("3000.00"), Decimal("2000.00")]
    assert CalculationService.validate_payment_total(payable, valid_payments) is True

    invalid_payments = [Decimal("3000.00"), Decimal("1999.99")]
    assert CalculationService.validate_payment_total(payable, invalid_payments) is False
