# tests/test_extraction.py

from decimal import Decimal
from core.models import Invoice, LineItem, IngestionSource


def test_invoice_model_creation():
    invoice = Invoice(
        source=IngestionSource.PDF_UPLOAD,
        invoice_number="INV-001",
        currency="USD",
        total=Decimal("1500.00"),
        line_items=[
            LineItem(
                line_number=1,
                description="Consulting Services",
                quantity=Decimal("10"),
                unit_price=Decimal("150.00"),
                total=Decimal("1500.00"),
            )
        ],
    )
    assert invoice.invoice_number == "INV-001"
    assert invoice.total == Decimal("1500.00")
    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].description == "Consulting Services"
    print("PASS: invoice model creation")


def test_invoice_model_defaults():
    invoice = Invoice(
        source=IngestionSource.PDF_UPLOAD,
    )
    assert invoice.currency == "USD"
    assert invoice.line_items == []
    assert invoice.invoice_number is None
    print("PASS: invoice model defaults")


def test_line_item_total():
    item = LineItem(
        line_number=1,
        description="Office Supplies",
        quantity=Decimal("5"),
        unit_price=Decimal("20.00"),
        total=Decimal("100.00"),
    )
    assert item.total == Decimal("100.00")
    print("PASS: line item total")


if __name__ == "__main__":
    test_invoice_model_creation()
    test_invoice_model_defaults()
    test_line_item_total()
    print("\nAll extraction tests passed.")
