# integrations/xero_client.py

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import get_settings
from core.logger import get_logger
from core.models import Invoice, PaymentRecord

logger = get_logger(__name__)
settings = get_settings()

XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"


class XeroAuthError(Exception):
    pass


class XeroAPIError(Exception):
    pass


# ─── Token Management ─────────────────────────────────────────────────────────

def _refresh_xero_token() -> tuple[str, str]:
    """
    Refresh the Xero OAuth2 access token using the stored refresh token.
    Returns (access_token, tenant_id).
    """
    import base64

    credentials = base64.b64encode(
        f"{settings.xero_client_id}:{settings.xero_client_secret}".encode()
    ).decode()

    try:
        response = httpx.post(
            XERO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.xero_refresh_token,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        access_token = data.get("access_token")

        if not access_token:
            raise XeroAuthError("No access token in Xero refresh response")

        logger.info("Xero token refreshed successfully")
        return access_token

    except httpx.HTTPStatusError as e:
        logger.error("Xero token refresh failed", error=str(e))
        raise XeroAuthError(f"Xero token refresh failed: {e}")


def _get_tenant_id(access_token: str) -> str:
    """
    Retrieve the Xero tenant ID (organisation ID) for the connected account.
    Required on every Xero API request as the Xero-tenant-id header.
    """
    try:
        response = httpx.get(
            XERO_CONNECTIONS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        response.raise_for_status()
        connections = response.json()

        if not connections:
            raise XeroAuthError("No Xero organisations connected to this token")

        tenant_id = connections[0].get("tenantId")
        logger.info("Xero tenant ID retrieved", tenant_id=tenant_id)
        return tenant_id

    except httpx.HTTPStatusError as e:
        logger.error("Failed to retrieve Xero tenant ID", error=str(e))
        raise XeroAuthError(f"Xero tenant ID retrieval failed: {e}")


def _get_headers(access_token: str, tenant_id: str) -> dict:
    """Build standard Xero API request headers."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Xero-tenant-id": tenant_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ─── Contact (Vendor) Operations ──────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def find_or_create_contact(
    vendor_name: str,
    vendor_email: Optional[str] = None,
    access_token: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    Find an existing Xero Contact by name, or create a new one.
    In Xero, vendors are represented as Contacts.
    Returns the Xero Contact object.
    """
    token = access_token or _refresh_xero_token()
    tid = tenant_id or _get_tenant_id(token)
    headers = _get_headers(token, tid)

    # Search for existing contact
    try:
        response = httpx.get(
            f"{XERO_API_BASE}/Contacts",
            headers=headers,
            params={"where": f'Name=="{vendor_name}"', "summaryOnly": "true"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        contacts = data.get("Contacts", [])

        if contacts:
            logger.info(
                "Contact found in Xero",
                vendor_name=vendor_name,
                xero_id=contacts[0].get("ContactID"),
            )
            return contacts[0]

    except Exception as e:
        logger.warning("Xero contact search failed", vendor_name=vendor_name, error=str(e))

    # Create new contact
    contact_payload = {
        "Contacts": [
            {
                "Name": vendor_name,
                "IsSupplier": True,
                **({"EmailAddress": vendor_email} if vendor_email else {}),
            }
        ]
    }

    try:
        response = httpx.post(
            f"{XERO_API_BASE}/Contacts",
            headers=headers,
            json=contact_payload,
            timeout=15,
        )
        response.raise_for_status()
        contacts = response.json().get("Contacts", [])

        if not contacts:
            raise XeroAPIError("Xero returned empty contacts list after creation")

        contact = contacts[0]
        logger.info(
            "Contact created in Xero",
            vendor_name=vendor_name,
            xero_id=contact.get("ContactID"),
        )
        return contact

    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to create Xero contact",
            vendor_name=vendor_name,
            error=e.response.text[:500],
        )
        raise XeroAPIError(f"Xero contact creation failed: {e}")


# ─── Invoice (Bill) Operations ────────────────────────────────────────────────

def _build_xero_invoice_payload(
    invoice: Invoice,
    contact: dict,
) -> dict:
    """
    Map our Invoice model to the Xero ACCPAY (Accounts Payable) invoice format.
    Xero uses Type=ACCPAY for vendor bills.
    """
    line_items = []

    if invoice.line_items:
        for item in invoice.line_items:
            line_items.append({
                "Description": item.description,
                "Quantity": float(item.quantity),
                "UnitAmount": float(item.unit_price),
                "LineAmount": float(item.total),
                "AccountCode": "200",  # Default expense account code
            })
    else:
        line_items.append({
            "Description": f"Invoice {invoice.invoice_number}",
            "Quantity": 1,
            "UnitAmount": float(invoice.total or 0),
            "LineAmount": float(invoice.total or 0),
            "AccountCode": "200",
        })

    payload = {
        "Type": "ACCPAY",
        "Contact": {"ContactID": contact.get("ContactID")},
        "InvoiceNumber": invoice.invoice_number,
        "Date": str(invoice.invoice_date or datetime.utcnow().date()),
        "DueDate": str(invoice.due_date or ""),
        "CurrencyCode": invoice.currency,
        "LineAmountTypes": "Exclusive",
        "LineItems": line_items,
        "Status": "AUTHORISED",
        "Reference": invoice.po_number or "",
        "Url": f"https://ap.datawebify.com/invoices/{invoice.id}",
    }

    return {"Invoices": [payload]}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def create_invoice_in_xero(
    invoice: Invoice,
    access_token: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    Create an ACCPAY invoice (vendor bill) in Xero.
    Finds or creates the contact automatically.
    Returns the created Xero Invoice object.
    """
    if not settings.xero_client_id:
        raise XeroAPIError("Xero client ID not configured")

    token = access_token or _refresh_xero_token()
    tid = tenant_id or _get_tenant_id(token)
    headers = _get_headers(token, tid)

    vendor_name = invoice.vendor.vendor_name if invoice.vendor else "Unknown Vendor"
    vendor_email = invoice.vendor.vendor_email if invoice.vendor else None

    contact = find_or_create_contact(vendor_name, vendor_email, token, tid)
    payload = _build_xero_invoice_payload(invoice, contact)

    try:
        response = httpx.post(
            f"{XERO_API_BASE}/Invoices",
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        invoices = response.json().get("Invoices", [])

        if not invoices:
            raise XeroAPIError("Xero returned empty invoices list after creation")

        xero_invoice = invoices[0]
        logger.info(
            "Invoice created in Xero",
            invoice_number=invoice.invoice_number,
            xero_invoice_id=xero_invoice.get("InvoiceID"),
            amount=str(invoice.total),
        )
        return xero_invoice

    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to create invoice in Xero",
            invoice_number=invoice.invoice_number,
            error=e.response.text[:500],
        )
        raise XeroAPIError(f"Xero invoice creation failed: {e}")


# ─── Payment Operations ───────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def create_payment_in_xero(
    payment: PaymentRecord,
    xero_invoice_id: str,
    access_token: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """
    Record a payment against a Xero invoice.
    Returns the created Xero Payment object.
    """
    token = access_token or _refresh_xero_token()
    tid = tenant_id or _get_tenant_id(token)
    headers = _get_headers(token, tid)

    payload = {
        "Invoice": {"InvoiceID": xero_invoice_id},
        "Account": {"Code": "090"},  # Bank account code
        "Date": str(payment.scheduled_date),
        "Amount": float(payment.amount),
        "CurrencyRate": 1.0,
        "Reference": f"AP-AI-{payment.id}",
    }

    try:
        response = httpx.post(
            f"{XERO_API_BASE}/Payments",
            headers=headers,
            json={"Payments": [payload]},
            timeout=20,
        )
        response.raise_for_status()
        payments = response.json().get("Payments", [])

        if not payments:
            raise XeroAPIError("Xero returned empty payments list after creation")

        xero_payment = payments[0]
        logger.info(
            "Payment created in Xero",
            payment_id=str(payment.id),
            xero_payment_id=xero_payment.get("PaymentID"),
            amount=str(payment.amount),
        )
        return xero_payment

    except httpx.HTTPStatusError as e:
        logger.error(
            "Failed to create payment in Xero",
            payment_id=str(payment.id),
            error=e.response.text[:500],
        )
        raise XeroAPIError(f"Xero payment creation failed: {e}")


# ─── Health Check ─────────────────────────────────────────────────────────────

def check_xero_connection() -> bool:
    """
    Verify Xero API connectivity by fetching organisation info.
    Returns True if connected, False otherwise.
    """
    try:
        token = _refresh_xero_token()
        tid = _get_tenant_id(token)
        headers = _get_headers(token, tid)

        response = httpx.get(
            f"{XERO_API_BASE}/Organisation",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        orgs = response.json().get("Organisations", [])

        if orgs:
            logger.info(
                "Xero connection verified",
                org_name=orgs[0].get("Name"),
            )
        return True

    except Exception as e:
        logger.error("Xero connection check failed", error=str(e))
        return False