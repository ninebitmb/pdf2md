"""Pydantic response models and enums for the converter API."""

from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    INVOICE = "invoice"
    VAT_INVOICE = "vat_invoice"
    CREDIT_NOTE = "credit_note"
    PROFORMA = "proforma"
    UNKNOWN = "unknown"


class Language(StrEnum):
    LT = "lt"
    EN = "en"
    UNKNOWN = "unknown"


class ConvertResponse(BaseModel):
    success: bool
    markdown: str
    raw_markdown: str = ""
    pages: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    document_type: str
    language: str
    has_table: bool
    has_payment_info: bool
    processing_time_ms: float = Field(ge=0.0)
