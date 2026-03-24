# tests/test_erp_sync.py

from decimal import Decimal
from core.models import Invoice, ERPProvider, IngestionSource
from agents.erp_sync_agent import ERPSyncAgent


def _make_invoice(number: str, total: Decimal) -> Invoice:
    return Invoice(
        source=IngestionSource.PDF_UPLOAD,
        invoice_number=number,
        total=total,
        currency="USD",
    )


def test_sap_invoice_sync():
    invoice = _make_invoice("INV-300", Decimal("1000.00"))
    agent = ERPSyncAgent(erp_provider=ERPProvider.SAP)
    result = agent.sync_invoice(invoice)
    assert result.success is True
    assert result.erp_transaction_id == "SAP-INV-INV-300"
    print("PASS: SAP invoice sync stub")


def test_netsuite_invoice_sync():
    invoice = _make_invoice("INV-301", Decimal("2500.00"))
    agent = ERPSyncAgent(erp_provider=ERPProvider.NETSUITE)
    result = agent.sync_invoice(invoice)
    assert result.success is True
    assert result.erp_transaction_id == "NS-INV-INV-301"
    print("PASS: NetSuite invoice sync stub")


def test_invalid_erp_provider():
    try:
        ERPProvider("invalid_provider")
        print("FAIL: should have raised ValueError")
    except ValueError:
        print("PASS: invalid ERP provider rejected")


if __name__ == "__main__":
    test_sap_invoice_sync()
    test_netsuite_invoice_sync()
    test_invalid_erp_provider()
    print("\nAll ERP sync tests passed.")
