# agents/payment_scheduler_agent.py

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from core.config import get_settings
from core.logger import get_logger
from core.models import (
    Invoice,
    InvoiceStatus,
    PaymentRecord,
    PaymentStatus,
)

logger = get_logger(__name__)
settings = get_settings()


# ─── Payment Date Logic ───────────────────────────────────────────────────────

def _calculate_payment_date(
    invoice: Invoice,
    lead_days: int = 2,
) -> date:
    """
    Calculate the optimal payment date for an invoice.

    Strategy:
    - If due date is in the future and more than lead_days away,
      schedule payment lead_days before due date to allow processing time.
    - If due date is today or within lead_days, schedule for tomorrow.
    - If due date is already past, schedule for tomorrow and flag as overdue.

    Args:
        invoice: Approved invoice with a due date.
        lead_days: Number of days before due date to schedule payment.

    Returns:
        Calculated payment date.
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)

    if invoice.due_date is None:
        logger.warning(
            "Invoice has no due date — scheduling for tomorrow",
            invoice_id=str(invoice.id),
        )
        return tomorrow

    days_until_due = (invoice.due_date - today).days

    if days_until_due > lead_days:
        # Pay lead_days before due date
        payment_date = invoice.due_date - timedelta(days=lead_days)
        logger.info(
            "Payment scheduled before due date",
            invoice_id=str(invoice.id),
            due_date=str(invoice.due_date),
            payment_date=str(payment_date),
            days_until_due=days_until_due,
        )
        return payment_date

    elif days_until_due > 0:
        # Due very soon — pay tomorrow
        logger.warning(
            "Invoice due soon — scheduling urgent payment",
            invoice_id=str(invoice.id),
            due_date=str(invoice.due_date),
            days_until_due=days_until_due,
        )
        return tomorrow

    else:
        # Already overdue — pay tomorrow and log warning
        logger.warning(
            "Invoice is overdue — scheduling immediate payment",
            invoice_id=str(invoice.id),
            due_date=str(invoice.due_date),
            days_overdue=abs(days_until_due),
        )
        return tomorrow


def _is_overdue(invoice: Invoice) -> bool:
    """Return True if invoice due date is in the past."""
    if invoice.due_date is None:
        return False
    return invoice.due_date < date.today()


# ─── Batch Assignment ─────────────────────────────────────────────────────────

def _assign_batch_id(payment_date: date) -> str:
    """
    Generate a batch ID grouping payments by scheduled date.
    All payments scheduled for the same date share a batch ID.
    Format: BATCH-YYYYMMDD
    """
    return f"BATCH-{payment_date.strftime('%Y%m%d')}"


# ─── Priority Scoring ─────────────────────────────────────────────────────────

def _priority_score(invoice: Invoice) -> int:
    """
    Assign a priority score to an invoice for payment ordering.
    Lower score = higher priority.

    Rules:
    - Overdue invoices: score 0 (highest priority)
    - Due within 3 days: score 1
    - Due within 7 days: score 2
    - Due within 30 days: score 3
    - Due beyond 30 days: score 4
    """
    if invoice.due_date is None:
        return 3

    days_until_due = (invoice.due_date - date.today()).days

    if days_until_due < 0:
        return 0
    elif days_until_due <= 3:
        return 1
    elif days_until_due <= 7:
        return 2
    elif days_until_due <= 30:
        return 3
    else:
        return 4


# ─── Single Invoice Scheduler ─────────────────────────────────────────────────

def schedule_payment(
    invoice: Invoice,
    payment_method: str = "ACH",
    lead_days: int = 2,
) -> tuple[Invoice, PaymentRecord]:
    """
    Schedule a payment for a single approved invoice.

    Creates a PaymentRecord with scheduled date, batch ID, and method.
    Updates invoice status to SCHEDULED.

    Args:
        invoice: Approved invoice ready for payment.
        payment_method: Payment method (ACH, Check, Wire).
        lead_days: Days before due date to schedule payment.

    Returns:
        Tuple of (updated Invoice, PaymentRecord).
    """
    logger.info(
        "Scheduling payment",
        invoice_id=str(invoice.id),
        invoice_number=invoice.invoice_number,
        total=str(invoice.total),
        due_date=str(invoice.due_date),
    )

    payment_date = _calculate_payment_date(invoice, lead_days)
    batch_id = _assign_batch_id(payment_date)
    vendor_name = invoice.vendor.vendor_name if invoice.vendor else "Unknown Vendor"

    payment = PaymentRecord(
        id=uuid4(),
        invoice_id=invoice.id,
        vendor_name=vendor_name,
        amount=invoice.total or Decimal("0"),
        currency=invoice.currency,
        scheduled_date=payment_date,
        payment_method=payment_method,
        batch_id=batch_id,
        status=PaymentStatus.SCHEDULED,
    )

    invoice.status = InvoiceStatus.SCHEDULED
    invoice.payment_id = payment.id

    logger.info(
        "Payment scheduled",
        invoice_id=str(invoice.id),
        payment_id=str(payment.id),
        scheduled_date=str(payment_date),
        batch_id=batch_id,
        overdue=_is_overdue(invoice),
    )

    return invoice, payment


# ─── Batch Scheduler ──────────────────────────────────────────────────────────

def run_payment_scheduler_agent(
    invoices: list[Invoice],
    payment_method: str = "ACH",
    lead_days: int = 2,
) -> tuple[list[Invoice], list[PaymentRecord]]:
    """
    Main entry point for the Payment Scheduler Agent.

    Processes a list of approved invoices, sorts by priority,
    schedules each payment, and returns grouped payment batches.

    Args:
        invoices: List of approved invoices to schedule.
        payment_method: Default payment method for all payments.
        lead_days: Days before due date to schedule payment.

    Returns:
        Tuple of (updated invoices list, payment records list).
    """
    if not invoices:
        logger.info("No invoices to schedule")
        return [], []

    # Filter to only approved invoices
    approved = [inv for inv in invoices if inv.status == InvoiceStatus.APPROVED]
    skipped = len(invoices) - len(approved)

    if skipped > 0:
        logger.warning(
            "Some invoices skipped — not in APPROVED status",
            skipped=skipped,
            total=len(invoices),
        )

    # Sort by priority score (ascending = highest priority first)
    approved.sort(key=_priority_score)

    updated_invoices: list[Invoice] = []
    payment_records: list[PaymentRecord] = []
    batch_summary: dict[str, int] = {}

    for invoice in approved:
        try:
            updated_inv, payment = schedule_payment(
                invoice, payment_method, lead_days
            )
            updated_invoices.append(updated_inv)
            payment_records.append(payment)

            batch_id = payment.batch_id
            batch_summary[batch_id] = batch_summary.get(batch_id, 0) + 1

        except Exception as e:
            logger.error(
                "Failed to schedule payment for invoice",
                invoice_id=str(invoice.id),
                invoice_number=invoice.invoice_number,
                error=str(e),
            )

    # Log batch summary
    logger.info(
        "Payment scheduling complete",
        total_scheduled=len(payment_records),
        batches=len(batch_summary),
        batch_summary=batch_summary,
    )

    return updated_invoices, payment_records


# ─── Batch Report Generator ───────────────────────────────────────────────────

def generate_payment_run_report(
    payment_records: list[PaymentRecord],
) -> dict:
    """
    Generate a summary report for a payment run.
    Used for AP manager review before ERP submission.

    Returns a dict with totals by batch, currency, and method.
    """
    if not payment_records:
        return {"total_payments": 0, "total_amount": "0.00", "batches": {}}

    batches: dict[str, dict] = {}
    grand_total = Decimal("0")

    for payment in payment_records:
        batch_id = payment.batch_id or "UNASSIGNED"

        if batch_id not in batches:
            batches[batch_id] = {
                "payment_count": 0,
                "total_amount": Decimal("0"),
                "currency": payment.currency,
                "scheduled_date": str(payment.scheduled_date),
                "payment_method": payment.payment_method,
            }

        batches[batch_id]["payment_count"] += 1
        batches[batch_id]["total_amount"] += payment.amount
        grand_total += payment.amount

    # Convert Decimals to strings for serialization
    for batch in batches.values():
        batch["total_amount"] = str(batch["total_amount"])

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "total_payments": len(payment_records),
        "total_amount": str(grand_total),
        "currency": payment_records[0].currency if payment_records else "USD",
        "batches": batches,
    }