"""Pydantic response model for the /experiment endpoint."""

from pydantic import BaseModel, Field


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
