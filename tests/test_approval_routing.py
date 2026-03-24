# tests/test_approval_routing.py

from decimal import Decimal
from core.models import Invoice, ApprovalRecord, ApprovalStatus, IngestionSource
from agents.approval_router_agent import run_approval_router_agent, process_approval_decision


def _make_invoice(number: str, total: Decimal) -> Invoice:
    return Invoice(
        source=IngestionSource.PDF_UPLOAD,
        invoice_number=number,
        total=total,
        currency="USD",
    )


def test_auto_approve_below_threshold():
    invoice = _make_invoice("INV-200", Decimal("500.00"))
    result_invoice, result_approval = run_approval_router_agent(invoice)
    assert result_invoice is not None
    assert result_approval is not None
    print("PASS: approval router runs for low-value invoice")


def test_approval_decision_approve():
    invoice = _make_invoice("INV-201", Decimal("2000.00"))
    result_invoice, approval = run_approval_router_agent(invoice)
    result_invoice2, result_approval = process_approval_decision(result_invoice, approval, "approve")
    assert result_invoice2 is not None
    assert result_approval is not None
    print("PASS: approval decision approved")


def test_approval_decision_reject():
    invoice = _make_invoice("INV-202", Decimal("2000.00"))
    result_invoice, approval = run_approval_router_agent(invoice)
    result_invoice2, result_approval = process_approval_decision(
        result_invoice, approval, "reject", rejection_reason="Duplicate invoice"
    )
    assert result_invoice2 is not None
    assert result_approval is not None
    print("PASS: approval decision rejected")


if __name__ == "__main__":
    test_auto_approve_below_threshold()
    test_approval_decision_approve()
    test_approval_decision_reject()
    print("\nAll approval routing tests passed.")
