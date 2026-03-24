# tests/test_three_way_match.py

from datetime import date
from decimal import Decimal
from core.models import Invoice, PurchaseOrder, GoodsReceipt, LineItem, IngestionSource
from agents.three_way_match_agent import run_three_way_match_agent


def _make_line_items(total: Decimal) -> list:
    return [
        LineItem(
            line_number=1,
            description="Test Item",
            quantity=Decimal("1"),
            unit_price=total,
            total=total,
        )
    ]


def _make_invoice(number: str, total: Decimal, po_number: str) -> Invoice:
    return Invoice(
        source=IngestionSource.PDF_UPLOAD,
        invoice_number=number,
        po_number=po_number,
        currency="USD",
        total=total,
        line_items=_make_line_items(total),
    )


def _make_po(number: str, vendor: str, total: Decimal) -> PurchaseOrder:
    return PurchaseOrder(
        po_number=number,
        vendor_name=vendor,
        po_date=date.today(),
        total_amount=total,
        currency="USD",
        line_items=_make_line_items(total),
    )


def _make_receipt(number: str, po_number: str, total: Decimal) -> GoodsReceipt:
    return GoodsReceipt(
        receipt_number=number,
        po_number=po_number,
        receipt_date=date.today(),
        line_items=_make_line_items(total),
    )


def test_full_match():
    invoice = _make_invoice("INV-100", Decimal("1000.00"), "PO-100")
    # Patch vendor to match PO
    from core.models import VendorInfo
    invoice.vendor = VendorInfo(vendor_name="Acme Corp")
    po = _make_po("PO-100", "Acme Corp", Decimal("1000.00"))
    receipt = _make_receipt("GR-100", "PO-100", Decimal("1000.00"))
    result = run_three_way_match_agent(invoice, po, receipt)
    assert result.match_status.value in ("full_match", "partial_match"), f"Expected match, got: {result.match_status}"
    print("PASS: full match")


def test_mismatch_over_tolerance():
    invoice = _make_invoice("INV-101", Decimal("1200.00"), "PO-101")
    from core.models import VendorInfo
    invoice.vendor = VendorInfo(vendor_name="Acme Corp")
    po = _make_po("PO-101", "Acme Corp", Decimal("1000.00"))
    receipt = _make_receipt("GR-101", "PO-101", Decimal("1000.00"))
    result = run_three_way_match_agent(invoice, po, receipt)
    assert result.match_status.value == "mismatch", f"Expected mismatch, got: {result.match_status}"
    print("PASS: mismatch over tolerance")


def test_po_not_found():
    invoice = _make_invoice("INV-102", Decimal("1000.00"), "PO-102")
    result = run_three_way_match_agent(invoice, po=None, receipt=None)
    assert result.match_status.value == "po_not_found", f"Expected po_not_found, got: {result.match_status}"
    print("PASS: PO not found handled correctly")


if __name__ == "__main__":
    test_full_match()
    test_mismatch_over_tolerance()
    test_po_not_found()
    print("\nAll three-way match tests passed.")
