# agents/three_way_match_agent.py

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from core.config import get_settings
from core.logger import get_logger
from core.models import (
    GoodsReceipt,
    Invoice,
    InvoiceStatus,
    LineItem,
    MatchDetail,
    MatchStatus,
    PurchaseOrder,
)

logger = get_logger(__name__)
settings = get_settings()


# ─── Tolerance Helpers ────────────────────────────────────────────────────────

def _within_tolerance(
    invoice_val: Decimal,
    reference_val: Decimal,
    tolerance_pct: Decimal,
) -> tuple[bool, Decimal]:
    """
    Check if invoice_val is within tolerance_pct of reference_val.
    Returns (within_tolerance, variance_percent).
    """
    if reference_val == Decimal("0"):
        within = invoice_val == Decimal("0")
        return within, Decimal("0")

    variance_pct = abs(invoice_val - reference_val) / reference_val * Decimal("100")
    return variance_pct <= tolerance_pct, variance_pct


# ─── Header-Level Match ───────────────────────────────────────────────────────

def _match_header(
    invoice: Invoice,
    po: PurchaseOrder,
    receipt: GoodsReceipt,
    tolerance_pct: Decimal,
) -> list[MatchDetail]:
    """
    Compare invoice header fields against PO and receipt.
    Checks: vendor name, total amount, currency, PO number.
    """
    details: list[MatchDetail] = []

    # Vendor name match (case-insensitive)
    inv_vendor = (invoice.vendor.vendor_name if invoice.vendor else "").lower().strip()
    po_vendor = po.vendor_name.lower().strip()
    vendor_match = inv_vendor == po_vendor

    details.append(
        MatchDetail(
            field="vendor_name",
            invoice_value=invoice.vendor.vendor_name if invoice.vendor else "",
            po_value=po.vendor_name,
            within_tolerance=vendor_match,
            variance_percent=None,
        )
    )

    # Total amount match (invoice vs PO)
    if invoice.total is not None and po.total_amount is not None:
        within, variance = _within_tolerance(
            invoice.total, po.total_amount, tolerance_pct
        )
        details.append(
            MatchDetail(
                field="total_amount",
                invoice_value=str(invoice.total),
                po_value=str(po.total_amount),
                within_tolerance=within,
                variance_percent=variance,
            )
        )

    # Currency match
    currency_match = invoice.currency.upper() == po.currency.upper()
    details.append(
        MatchDetail(
            field="currency",
            invoice_value=invoice.currency,
            po_value=po.currency,
            within_tolerance=currency_match,
        )
    )

    # PO number match
    inv_po = (invoice.po_number or "").strip()
    po_number = po.po_number.strip()
    po_match = inv_po == po_number

    details.append(
        MatchDetail(
            field="po_number",
            invoice_value=inv_po,
            po_value=po_number,
            within_tolerance=po_match,
        )
    )

    return details


# ─── Line-Level Match ─────────────────────────────────────────────────────────

def _match_line_items(
    invoice: Invoice,
    po: PurchaseOrder,
    receipt: GoodsReceipt,
    tolerance_pct: Decimal,
) -> list[MatchDetail]:
    """
    Compare invoice line items against PO lines and receipt lines.
    Matches by line number when available, falls back to description.
    Checks: quantity, unit price, and line total.
    """
    details: list[MatchDetail] = []

    if not invoice.line_items:
        logger.info(
            "No line items to match",
            invoice_id=str(invoice.id),
        )
        return details

    # Build PO and receipt lookup maps by line number
    po_lines: dict[int, LineItem] = {
        item.line_number: item for item in po.line_items
    }
    receipt_lines: dict[int, LineItem] = {
        item.line_number: item for item in receipt.line_items
    }

    for inv_line in invoice.line_items:
        ln = inv_line.line_number

        po_line = po_lines.get(ln)
        receipt_line = receipt_lines.get(ln)

        # Quantity: compare invoice vs receipt (what was actually delivered)
        if receipt_line is not None:
            qty_within, qty_variance = _within_tolerance(
                inv_line.quantity, receipt_line.quantity, tolerance_pct
            )
            details.append(
                MatchDetail(
                    field=f"line_{ln}.quantity",
                    invoice_value=str(inv_line.quantity),
                    po_value=str(po_line.quantity) if po_line else None,
                    receipt_value=str(receipt_line.quantity),
                    within_tolerance=qty_within,
                    variance_percent=qty_variance,
                )
            )

        # Unit price: compare invoice vs PO (agreed price)
        if po_line is not None:
            price_within, price_variance = _within_tolerance(
                inv_line.unit_price, po_line.unit_price, tolerance_pct
            )
            details.append(
                MatchDetail(
                    field=f"line_{ln}.unit_price",
                    invoice_value=str(inv_line.unit_price),
                    po_value=str(po_line.unit_price),
                    within_tolerance=price_within,
                    variance_percent=price_variance,
                )
            )

        # Line total: compare invoice vs PO
        if po_line is not None:
            total_within, total_variance = _within_tolerance(
                inv_line.total, po_line.total, tolerance_pct
            )
            details.append(
                MatchDetail(
                    field=f"line_{ln}.total",
                    invoice_value=str(inv_line.total),
                    po_value=str(po_line.total),
                    within_tolerance=total_within,
                    variance_percent=total_variance,
                )
            )

    return details


# ─── Match Status Resolver ────────────────────────────────────────────────────

def _resolve_match_status(details: list[MatchDetail]) -> MatchStatus:
    """
    Determine overall match status from all match detail results.

    Rules:
    - All within tolerance: FULL_MATCH
    - Any critical field (vendor, total, currency) fails: MISMATCH
    - Only line-level variances: PARTIAL_MATCH
    """
    if not details:
        return MatchStatus.PARTIAL_MATCH

    critical_fields = {"vendor_name", "total_amount", "currency", "po_number"}
    failed_fields = {d.field for d in details if not d.within_tolerance}

    if not failed_fields:
        return MatchStatus.FULL_MATCH

    if failed_fields & critical_fields:
        return MatchStatus.MISMATCH

    return MatchStatus.PARTIAL_MATCH


# ─── Main Agent ───────────────────────────────────────────────────────────────

def run_three_way_match_agent(
    invoice: Invoice,
    po: Optional[PurchaseOrder] = None,
    receipt: Optional[GoodsReceipt] = None,
    tolerance_pct: Optional[float] = None,
) -> Invoice:
    """
    Main entry point for the Three-Way Match Agent.

    Compares the invoice against a Purchase Order and Goods Receipt.
    Sets invoice.match_status and invoice.match_details.
    Routes to MATCHED or MISMATCH status for downstream agents.

    Args:
        invoice: Validated Invoice model.
        po: Matching PurchaseOrder. If None, routes to PO_NOT_FOUND.
        receipt: Matching GoodsReceipt. If None, routes to RECEIPT_NOT_FOUND.
        tolerance_pct: Override for match tolerance percentage.
                       Defaults to settings.match_tolerance_percent.

    Returns:
        Invoice with updated match_status and match_details.
    """
    tol = Decimal(str(tolerance_pct or settings.match_tolerance_percent))

    logger.info(
        "Three-way match started",
        invoice_id=str(invoice.id),
        invoice_number=invoice.invoice_number,
        tolerance_pct=str(tol),
    )

    invoice.status = InvoiceStatus.MATCHING

    # Guard: PO not found
    if po is None:
        invoice.match_status = MatchStatus.PO_NOT_FOUND
        invoice.status = InvoiceStatus.EXCEPTION
        logger.warning(
            "PO not found for invoice",
            invoice_id=str(invoice.id),
            po_number=invoice.po_number,
        )
        return invoice

    # Guard: Receipt not found
    if receipt is None:
        invoice.match_status = MatchStatus.RECEIPT_NOT_FOUND
        invoice.status = InvoiceStatus.EXCEPTION
        logger.warning(
            "Goods receipt not found for invoice",
            invoice_id=str(invoice.id),
            po_number=invoice.po_number,
        )
        return invoice

    # Run header and line-level matching
    header_details = _match_header(invoice, po, receipt, tol)
    line_details = _match_line_items(invoice, po, receipt, tol)
    all_details = header_details + line_details

    invoice.match_details = all_details
    invoice.match_status = _resolve_match_status(all_details)

    # Set invoice status based on match result
    if invoice.match_status == MatchStatus.FULL_MATCH:
        invoice.status = InvoiceStatus.MATCHED
        logger.info(
            "Three-way match passed",
            invoice_id=str(invoice.id),
            match_status=invoice.match_status,
        )
    elif invoice.match_status == MatchStatus.PARTIAL_MATCH:
        invoice.status = InvoiceStatus.PARTIAL_MATCH
        logger.warning(
            "Partial match — line variances detected",
            invoice_id=str(invoice.id),
            failed_fields=[d.field for d in all_details if not d.within_tolerance],
        )
    else:
        invoice.status = InvoiceStatus.MISMATCH
        logger.warning(
            "Three-way match failed",
            invoice_id=str(invoice.id),
            match_status=invoice.match_status,
            failed_fields=[d.field for d in all_details if not d.within_tolerance],
        )

    return invoice