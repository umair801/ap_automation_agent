# core/tasks.py

import structlog
from core.celery_app import celery_app
from core.models import ERPProvider

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=10)
def process_invoice_pipeline(self, invoice_data: dict, erp_provider: str = "quickbooks"):
    """
    Full async invoice processing pipeline.
    Triggered after ingestion. Runs extraction through ERP sync.
    """
    log = logger.bind(task="process_invoice_pipeline")

    try:
        from agents.extraction_agent import run_extraction_agent
        from agents.validation_agent import run_validation_agent
        from agents.three_way_match_agent import run_three_way_match_agent
        from agents.erp_sync_agent import ERPSyncAgent
        from agents.audit_logger_agent import AuditLoggerAgent
        from core.database import update_invoice_status

        audit = AuditLoggerAgent()

        # Re-hydrate invoice from dict
        from core.models import Invoice
        invoice = Invoice(**invoice_data)

        log.info("pipeline_started", invoice_number=invoice.invoice_number)

        # Validation
        validation_result = run_validation_agent(invoice)
        if not validation_result.is_valid:
            update_invoice_status(invoice.invoice_number, "exception")
            audit.log_validation_failed(invoice, "; ".join(validation_result.errors))
            log.warning("pipeline_validation_failed", invoice_number=invoice.invoice_number)
            return {"status": "exception", "reason": "validation_failed"}

        audit.log_validation_passed(invoice)

        # Three-way match
        from core.models import PurchaseOrder, GoodsReceipt
        po = PurchaseOrder(
            po_number=invoice.po_number or "UNKNOWN",
            vendor_name=invoice.vendor_name,
            total_amount=invoice.total_amount,
            line_items=invoice.line_items,
        )
        receipt = GoodsReceipt(
            receipt_number="GR-AUTO",
            po_number=invoice.po_number or "UNKNOWN",
            vendor_name=invoice.vendor_name,
            total_amount=invoice.total_amount,
            line_items=invoice.line_items,
        )
        match_result = run_three_way_match_agent(invoice, po, receipt)

        if match_result.status == "mismatch":
            update_invoice_status(invoice.invoice_number, "exception")
            audit.log_match_failed(invoice, match_result.summary)
            log.warning("pipeline_match_failed", invoice_number=invoice.invoice_number)
            return {"status": "exception", "reason": "match_failed"}

        update_invoice_status(invoice.invoice_number, "matched")
        audit.log_match_passed(invoice)

        # ERP sync
        provider = ERPProvider(erp_provider)
        erp_agent = ERPSyncAgent(erp_provider=provider)
        sync_result = erp_agent.sync_invoice(invoice)

        if sync_result.success:
            update_invoice_status(
                invoice.invoice_number,
                "approved",
                erp_transaction_id=sync_result.erp_transaction_id,
                erp_provider=erp_provider,
            )
            audit.log_erp_sync(invoice, sync_result)

        log.info(
            "pipeline_complete",
            invoice_number=invoice.invoice_number,
            erp_transaction_id=sync_result.erp_transaction_id,
        )

        return {
            "status": "success",
            "invoice_number": invoice.invoice_number,
            "erp_transaction_id": sync_result.erp_transaction_id,
        }

    except Exception as exc:
        log.error("pipeline_failed", error=str(exc))
        raise self.retry(exc=exc)