# agents/erp_sync_agent.py

import time
import structlog
from typing import Any
from core.models import Invoice, PaymentRecord, ERPSyncResult, ERPProvider
from integrations.quickbooks_client import (
    find_or_create_vendor,
    create_bill_in_quickbooks,
    create_bill_payment_in_quickbooks,
)
from integrations.xero_client import (
    find_or_create_contact,
    create_invoice_in_xero,
    create_payment_in_xero,
)

logger = structlog.get_logger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2


class ERPSyncAgent:
    """Routes approved invoices and payments to the configured ERP system."""

    def __init__(self, erp_provider: ERPProvider) -> None:
        self.erp_provider = erp_provider
        self.log = logger.bind(agent="erp_sync_agent", erp=erp_provider.value)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def sync_invoice(self, invoice: Invoice) -> ERPSyncResult:
        self.log.info("sync_invoice_start", invoice_id=invoice.invoice_number)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._route_invoice(invoice)
                self.log.info(
                    "sync_invoice_success",
                    invoice_id=invoice.invoice_number,
                    erp_transaction_id=result.erp_transaction_id,
                    attempt=attempt,
                )
                return result
            except Exception as exc:
                self.log.warning(
                    "sync_invoice_retry",
                    invoice_id=invoice.invoice_number,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == MAX_RETRIES:
                    self.log.error(
                        "sync_invoice_failed",
                        invoice_id=invoice.invoice_number,
                        error=str(exc),
                    )
                    return ERPSyncResult(
                        invoice_number=invoice.invoice_number,
                        erp_provider=self.erp_provider,
                        success=False,
                        erp_transaction_id=None,
                        error_message=str(exc),
                    )
                time.sleep(RETRY_DELAY_SECONDS * attempt)

        return ERPSyncResult(
            invoice_number=invoice.invoice_number,
            erp_provider=self.erp_provider,
            success=False,
            erp_transaction_id=None,
            error_message="Max retries exceeded",
        )

    def sync_payment(self, payment: PaymentRecord) -> ERPSyncResult:
        self.log.info("sync_payment_start", payment_id=payment.payment_id)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = self._route_payment(payment)
                self.log.info(
                    "sync_payment_success",
                    payment_id=payment.payment_id,
                    erp_transaction_id=result.erp_transaction_id,
                    attempt=attempt,
                )
                return result
            except Exception as exc:
                self.log.warning(
                    "sync_payment_retry",
                    payment_id=payment.payment_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == MAX_RETRIES:
                    self.log.error(
                        "sync_payment_failed",
                        payment_id=payment.payment_id,
                        error=str(exc),
                    )
                    return ERPSyncResult(
                        invoice_number=payment.invoice_number,
                        erp_provider=self.erp_provider,
                        success=False,
                        erp_transaction_id=None,
                        error_message=str(exc),
                    )
                time.sleep(RETRY_DELAY_SECONDS * attempt)

        return ERPSyncResult(
            invoice_number=payment.invoice_number,
            erp_provider=self.erp_provider,
            success=False,
            erp_transaction_id=None,
            error_message="Max retries exceeded",
        )

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    def _route_invoice(self, invoice: Invoice) -> ERPSyncResult:
        if self.erp_provider == ERPProvider.QUICKBOOKS:
            return self._sync_invoice_quickbooks(invoice)
        elif self.erp_provider == ERPProvider.XERO:
            return self._sync_invoice_xero(invoice)
        elif self.erp_provider == ERPProvider.SAP:
            return self._sync_invoice_sap(invoice)
        elif self.erp_provider == ERPProvider.NETSUITE:
            return self._sync_invoice_netsuite(invoice)
        else:
            raise ValueError(f"Unsupported ERP provider: {self.erp_provider}")

    def _route_payment(self, payment: PaymentRecord) -> ERPSyncResult:
        if self.erp_provider == ERPProvider.QUICKBOOKS:
            return self._sync_payment_quickbooks(payment)
        elif self.erp_provider == ERPProvider.XERO:
            return self._sync_payment_xero(payment)
        elif self.erp_provider == ERPProvider.SAP:
            return self._sync_payment_sap(payment)
        elif self.erp_provider == ERPProvider.NETSUITE:
            return self._sync_payment_netsuite(payment)
        else:
            raise ValueError(f"Unsupported ERP provider: {self.erp_provider}")

    # ------------------------------------------------------------------
    # QuickBooks
    # ------------------------------------------------------------------

    def _sync_invoice_quickbooks(self, invoice: Invoice) -> ERPSyncResult:
        vendor_name = invoice.vendor.vendor_name if invoice.vendor else "Unknown"
        vendor_id = find_or_create_vendor(vendor_name)
        line_items = self._map_line_items_quickbooks(invoice)
        bill_id = create_bill_in_quickbooks(
            vendor_id=vendor_id,
            line_items=line_items,
            due_date=str(invoice.due_date) if invoice.due_date else None,
            invoice_number=invoice.invoice_number,
        )
        return ERPSyncResult(
            invoice_number=invoice.invoice_number,
            erp_provider=ERPProvider.QUICKBOOKS,
            success=True,
            erp_transaction_id=bill_id,
            error_message=None,
        )

    def _sync_payment_quickbooks(self, payment: PaymentRecord) -> ERPSyncResult:
        vendor_id = find_or_create_vendor(payment.vendor_name)
        payment_id = create_bill_payment_in_quickbooks(
            vendor_id=vendor_id,
            amount=float(payment.amount),
            payment_date=str(payment.payment_date),
        )
        return ERPSyncResult(
            invoice_number=payment.invoice_number,
            erp_provider=ERPProvider.QUICKBOOKS,
            success=True,
            erp_transaction_id=payment_id,
            error_message=None,
        )

    def _map_line_items_quickbooks(self, invoice: Invoice) -> list[dict[str, Any]]:
        return [
            {
                "Description": item.description,
                "Amount": float(item.total),
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "1"},
                },
            }
            for item in invoice.line_items
        ]

    # ------------------------------------------------------------------
    # Xero
    # ------------------------------------------------------------------

    def _sync_invoice_xero(self, invoice: Invoice) -> ERPSyncResult:
        line_items = self._map_line_items_xero(invoice)
        vendor_name = invoice.vendor.vendor_name if invoice.vendor else "Unknown"
        xero_invoice_id = create_invoice_in_xero(
            contact_name=vendor_name,
            line_items=line_items,
            due_date=str(invoice.due_date) if invoice.due_date else None,
            invoice_number=invoice.invoice_number,
            currency=invoice.currency or "USD",
        )
        return ERPSyncResult(
            invoice_number=invoice.invoice_number,
            erp_provider=ERPProvider.XERO,
            success=True,
            erp_transaction_id=xero_invoice_id,
            error_message=None,
        )

    def _sync_payment_xero(self, payment: PaymentRecord) -> ERPSyncResult:
        payment_id = create_payment_in_xero(
            invoice_number=payment.invoice_number,
            amount=float(payment.amount),
            payment_date=str(payment.payment_date),
        )
        return ERPSyncResult(
            invoice_number=payment.invoice_number,
            erp_provider=ERPProvider.XERO,
            success=True,
            erp_transaction_id=payment_id,
            error_message=None,
        )

    def _map_line_items_xero(self, invoice: Invoice) -> list[dict[str, Any]]:
        return [
            {
                "Description": item.description,
                "Quantity": float(item.quantity),
                "UnitAmount": float(item.unit_price),
                "AccountCode": "200",
            }
            for item in invoice.line_items
        ]

    # ------------------------------------------------------------------
    # SAP (stub)
    # ------------------------------------------------------------------

    def _sync_invoice_sap(self, invoice: Invoice) -> ERPSyncResult:
        self.log.info("sap_invoice_stub", invoice_id=invoice.invoice_number)
        return ERPSyncResult(
            invoice_number=invoice.invoice_number,
            erp_provider=ERPProvider.SAP,
            success=True,
            erp_transaction_id=f"SAP-INV-{invoice.invoice_number}",
            error_message=None,
        )

    def _sync_payment_sap(self, payment: PaymentRecord) -> ERPSyncResult:
        self.log.info("sap_payment_stub", payment_id=payment.payment_id)
        return ERPSyncResult(
            invoice_number=payment.invoice_number,
            erp_provider=ERPProvider.SAP,
            success=True,
            erp_transaction_id=f"SAP-PAY-{payment.payment_id}",
            error_message=None,
        )

    # ------------------------------------------------------------------
    # NetSuite (stub)
    # ------------------------------------------------------------------

    def _sync_invoice_netsuite(self, invoice: Invoice) -> ERPSyncResult:
        self.log.info("netsuite_invoice_stub", invoice_id=invoice.invoice_number)
        return ERPSyncResult(
            invoice_number=invoice.invoice_number,
            erp_provider=ERPProvider.NETSUITE,
            success=True,
            erp_transaction_id=f"NS-INV-{invoice.invoice_number}",
            error_message=None,
        )

    def _sync_payment_netsuite(self, payment: PaymentRecord) -> ERPSyncResult:
        self.log.info("netsuite_payment_stub", payment_id=payment.payment_id)
        return ERPSyncResult(
            invoice_number=payment.invoice_number,
            erp_provider=ERPProvider.NETSUITE,
            success=True,
            erp_transaction_id=f"NS-PAY-{payment.payment_id}",
            error_message=None,
        )