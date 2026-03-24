# api/ingestion_router.py

import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse

from core.database import insert_invoice
from core.config import get_settings
from parsers.pdf_parser import parse_pdf
from agents.extraction_agent import run_extraction_agent
from agents.validation_agent import run_validation_agent
from core.models import InvoiceStatus

logger = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/ingest", tags=["Ingestion"])


# ------------------------------------------------------------------
# PDF Upload Endpoint
# ------------------------------------------------------------------

@router.post("/pdf")
async def ingest_pdf(file: Annotated[UploadFile, File(description="Invoice PDF file")]):
    """Accept a PDF invoice upload and run it through the extraction pipeline."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    log = logger.bind(endpoint="ingest_pdf", filename=file.filename)
    log.info("pdf_upload_received")

    try:
        contents = await file.read()
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        temp_path = f"/tmp/{uuid.uuid4()}_{file.filename}"
        with open(temp_path, "wb") as f:
            f.write(contents)

        parse_result = parse_pdf(temp_path)
        extracted_text = parse_result.text
        if not extracted_text:
            raise HTTPException(
                status_code=422, detail="Could not extract text from PDF."
            )

        invoice = run_extraction_agent(extracted_text)
        invoice.source = "pdf_upload"

        validation_result = run_validation_agent(invoice)

        invoice_data = {
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "total_amount": float(invoice.total_amount) if invoice.total_amount else None,
            "subtotal": float(invoice.subtotal) if invoice.subtotal else None,
            "tax_amount": float(invoice.tax_amount) if invoice.tax_amount else None,
            "currency": invoice.currency,
            "po_number": invoice.po_number,
            "payment_terms": invoice.payment_terms,
            "status": InvoiceStatus.VALIDATED if validation_result.is_valid else InvoiceStatus.EXCEPTION,
            "source": "pdf_upload",
        }
        insert_invoice(invoice_data)

        log.info(
            "pdf_invoice_processed",
            invoice_number=invoice.invoice_number,
            valid=validation_result.is_valid,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "invoice_number": invoice.invoice_number,
                "vendor_name": invoice.vendor_name,
                "total_amount": float(invoice.total_amount) if invoice.total_amount else None,
                "validation_passed": validation_result.is_valid,
                "validation_errors": validation_result.errors,
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        log.error("pdf_ingest_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(exc)}")


# ------------------------------------------------------------------
# Email Webhook Endpoint
# ------------------------------------------------------------------

@router.post("/email-webhook")
async def ingest_email_webhook(request: Request):
    """Receive an email webhook payload from Gmail or Outlook."""
    log = logger.bind(endpoint="ingest_email_webhook")

    try:
        payload = await request.json()
        log.info("email_webhook_received", keys=list(payload.keys()))

        sender = payload.get("from", "unknown")
        body = payload.get("body", "")
        attachments = payload.get("attachments", [])

        if not body and not attachments:
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "No body or attachments found."},
            )

        invoice = run_extraction_agent(body)
        invoice.source = "email_webhook"

        validation_result = run_validation_agent(invoice)

        invoice_data = {
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "total_amount": float(invoice.total_amount) if invoice.total_amount else None,
            "currency": invoice.currency,
            "po_number": invoice.po_number,
            "status": InvoiceStatus.VALIDATED if validation_result.is_valid else InvoiceStatus.EXCEPTION,
            "source": "email_webhook",
        }
        insert_invoice(invoice_data)

        log.info(
            "email_invoice_processed",
            invoice_number=invoice.invoice_number,
            sender=sender,
            valid=validation_result.is_valid,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "invoice_number": invoice.invoice_number,
                "vendor_name": invoice.vendor_name,
                "validation_passed": validation_result.is_valid,
            },
        )

    except Exception as exc:
        log.error("email_webhook_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Email webhook failed: {str(exc)}")


# ------------------------------------------------------------------
# EDI File Upload Endpoint
# ------------------------------------------------------------------

@router.post("/edi")
async def ingest_edi(file: Annotated[UploadFile, File(description="EDI 810 invoice file")]):
    """Accept an EDI 810 invoice file and parse it into the pipeline."""
    log = logger.bind(endpoint="ingest_edi", filename=file.filename)
    log.info("edi_upload_received")

    try:
        contents = await file.read()
        if len(contents) == 0:
            raise HTTPException(status_code=400, detail="Uploaded EDI file is empty.")

        edi_text = contents.decode("utf-8", errors="replace")

        if "ST*810" not in edi_text and "810" not in edi_text[:100]:
            raise HTTPException(
                status_code=400,
                detail="File does not appear to be a valid EDI 810 invoice.",
            )

        invoice = run_extraction_agent(edi_text)
        invoice.source = "edi"

        validation_result = run_validation_agent(invoice)

        invoice_data = {
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "invoice_date": str(invoice.invoice_date) if invoice.invoice_date else None,
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "total_amount": float(invoice.total_amount) if invoice.total_amount else None,
            "currency": invoice.currency,
            "po_number": invoice.po_number,
            "status": InvoiceStatus.VALIDATED if validation_result.is_valid else InvoiceStatus.EXCEPTION,
            "source": "edi",
        }
        insert_invoice(invoice_data)

        log.info(
            "edi_invoice_processed",
            invoice_number=invoice.invoice_number,
            valid=validation_result.is_valid,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "invoice_number": invoice.invoice_number,
                "vendor_name": invoice.vendor_name,
                "validation_passed": validation_result.is_valid,
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        log.error("edi_ingest_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"EDI ingestion failed: {str(exc)}")