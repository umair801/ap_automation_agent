# tests/test_exception_handling.py

from decimal import Decimal
from core.models import Invoice, IngestionSource
from agents.audit_logger_agent import AuditLoggerAgent


def _make_invoice(number: str) -> Invoice:
    return Invoice(
        source=IngestionSource.PDF_UPLOAD,
        invoice_number=number,
        total=Decimal("1000.00"),
        currency="USD",
    )


def test_audit_log_invoice_received():
    invoice = _make_invoice("INV-400")
    audit = AuditLoggerAgent()
    entry = audit.log_invoice_received(invoice)
    assert entry["invoice_number"] == "INV-400"
    assert entry["event"] == "invoice_received"
    assert len(audit.audit_trail) == 1
    print("PASS: audit log invoice received")


def test_audit_log_validation_failed():
    invoice = _make_invoice("INV-401")
    audit = AuditLoggerAgent()
    entry = audit.log_validation_failed(invoice, "Missing due date")
    assert entry["event"] == "validation_failed"
    assert "Missing due date" in entry["details"]
    print("PASS: audit log validation failed")


def test_audit_trail_accumulates():
    invoice = _make_invoice("INV-402")
    audit = AuditLoggerAgent()
    audit.log_invoice_received(invoice)
    audit.log_extraction_complete(invoice)
    audit.log_validation_passed(invoice)
    assert len(audit.audit_trail) == 3
    print("PASS: audit trail accumulates correctly")


def test_daily_summary_generates():
    invoice = _make_invoice("INV-403")
    audit = AuditLoggerAgent()
    audit.log_invoice_received(invoice)
    summary = audit.generate_daily_summary()
    assert "invoice_received" in summary
    assert "Total audit events" in summary
    print("PASS: daily summary generates correctly")


if __name__ == "__main__":
    test_audit_log_invoice_received()
    test_audit_log_validation_failed()
    test_audit_trail_accumulates()
    test_daily_summary_generates()
    print("\nAll exception handling tests passed.")
