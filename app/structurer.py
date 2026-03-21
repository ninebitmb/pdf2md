"""Main orchestrator: transforms raw markdown into structured invoice markdown."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.normalizers import clean_field, normalize_amount, normalize_date, normalize_iban
from app.patterns import (
    ADDRESS_RE,
    BANK_CODE_RE,
    BANK_NAME_RE,
    COMPANY_CODE_RE,
    CURRENCY_RE,
    DATE_RE,
    DOCUMENT_TYPE_PATTERNS,
    DOCUMENT_TYPE_TITLES,
    EN_KEYWORDS,
    GRAND_TOTAL_RE,
    IBAN_RE,
    INVOICE_DATE_RE,
    INVOICE_NUMBER_FALLBACK_RE,
    INVOICE_NUMBER_RE,
    INVOICE_NUMBER_SPECIFIC_RE,
    LT_KEYWORDS,
    PAYMENT_TERM_RE,
    SWIFT_RE,
    TOTAL_IN_WORDS_RE,
    TOTAL_WITHOUT_VAT_RE,
    VAT_AMOUNT_RE,
    VAT_CODE_RE,
    NON_NAME_PHRASES,
    TABLE_HEADER_WORDS,
)
from app.section_classifier import classify_blocks, split_into_blocks
from app.table_parser import extract_table_from_blocks

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredResult:
    markdown: str
    pages: int
    confidence: float
    document_type: str
    language: str
    has_table: bool
    has_payment_info: bool


@dataclass
class EntityInfo:
    """Company/person info for seller or buyer."""
    name: str = ""
    code: str = ""
    vat: str = ""
    address: str = ""


@dataclass
class InvoiceData:
    """Intermediate storage for extracted invoice fields."""

    # Metadata
    document_type: str = "invoice"
    invoice_number: str = ""
    invoice_date: str = ""
    payment_term: str = ""

    # Entities
    seller: EntityInfo = field(default_factory=EntityInfo)
    buyer: EntityInfo = field(default_factory=EntityInfo)

    # Table
    items_table: str = ""

    # Totals
    total_without_vat: str = ""
    vat_amount: str = ""
    grand_total: str = ""
    total_in_words: str = ""

    # Payment
    bank_name: str = ""
    iban: str = ""
    swift: str = ""
    bank_code: str = ""
    currency: str = ""

    # Notes
    notes: list[str] = field(default_factory=list)

    # Document info
    issued_by: str = ""

    # Language
    language: str = "lt"


def structure_invoice(raw_markdown: str, pages: int = 1) -> StructuredResult:
    """Transform raw markdown into structured invoice markdown."""
    blocks = split_into_blocks(raw_markdown)
    classified = classify_blocks(blocks)

    remaining_blocks, table_result = extract_table_from_blocks(classified)

    data = InvoiceData()

    if table_result:
        data.items_table = table_result.markdown
        if table_result.grand_total:
            data.grand_total = table_result.grand_total

    data.document_type = _detect_document_type(raw_markdown)
    data.language = _detect_language(raw_markdown)

    for section, text in remaining_blocks:
        if section == "metadata":
            _extract_metadata(text, data)
        elif section == "seller":
            _extract_entity(text, data.seller)
        elif section == "buyer":
            _extract_entity(text, data.buyer)
        elif section == "totals":
            _extract_totals(text, data)
        elif section == "payment":
            _extract_payment(text, data)
        elif section == "notes":
            data.notes.append(_clean_field(text))
        elif section == "document_info":
            _extract_document_info(text, data)

    # Fallback: scan full text for missing critical fields
    if not data.invoice_number:
        _extract_metadata(raw_markdown, data)
    if not data.iban:
        _extract_payment(raw_markdown, data)
    if not data.grand_total:
        _extract_totals(raw_markdown, data)

    # Fallback: try to find seller/buyer from full text if not found
    if not data.seller.name:
        _extract_entity_from_full_text(raw_markdown, data.seller, "seller")
    if not data.buyer.name:
        _extract_entity_from_full_text(raw_markdown, data.buyer, "buyer")

    confidence = _compute_confidence(data)
    markdown = _assemble_markdown(data)

    logger.info("Structured invoice: type=%s lang=%s confidence=%.2f table=%s payment=%s",
                data.document_type, data.language, confidence,
                bool(data.items_table), bool(data.iban))

    return StructuredResult(
        markdown=markdown,
        pages=pages,
        confidence=confidence,
        document_type=data.document_type,
        language=data.language,
        has_table=bool(data.items_table),
        has_payment_info=bool(data.iban),
    )


# --- Detection ---

def _detect_document_type(text: str) -> str:
    """Detect document type from first 20 non-table lines, fallback to full text."""
    lines = text.strip().split("\n")
    header_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        header_lines.append(stripped)
        if len(header_lines) >= 20:
            break
    header_text = " ".join(header_lines).lower()

    for pattern, doc_type in DOCUMENT_TYPE_PATTERNS:
        if re.search(pattern, header_text):
            return doc_type

    non_table = " ".join(
        raw_line for raw_line in lines if not raw_line.strip().startswith("|")
    ).lower()
    for pattern, doc_type in DOCUMENT_TYPE_PATTERNS:
        if re.search(pattern, non_table):
            return doc_type
    return "invoice"


def _detect_language(text: str) -> str:
    """Detect document language (lt or en). Returns 'lt' on tie."""
    text_lower = text.lower()
    lt_score = sum(1 for kw in LT_KEYWORDS if kw in text_lower)
    en_score = sum(1 for kw in EN_KEYWORDS if kw in text_lower)
    result = "lt" if lt_score >= en_score else "en"
    logger.debug("Language detection: lt=%d en=%d → %s", lt_score, en_score, result)
    return result


# --- Field extraction ---

def _extract_metadata(text: str, data: InvoiceData) -> None:
    """Extract invoice number, dates from metadata block."""
    if not data.invoice_number:
        for pattern in [INVOICE_NUMBER_SPECIFIC_RE, INVOICE_NUMBER_RE, INVOICE_NUMBER_FALLBACK_RE]:
            m = pattern.search(text)
            if m:
                data.invoice_number = _clean_invoice_number(m.group(1))
                logger.debug("Invoice number: %s", data.invoice_number)
                break

    if not data.invoice_date:
        m = INVOICE_DATE_RE.search(text)
        if m:
            data.invoice_date = normalize_date(m.group(1))
        else:
            # Fallback: first date-like pattern in text (not inside tables)
            non_table = "\n".join(
                line for line in text.split("\n") if not line.strip().startswith("|")
            )
            m = DATE_RE.search(non_table)
            if m:
                data.invoice_date = normalize_date(m.group(1))
                logger.debug("Invoice date (fallback): %s", data.invoice_date)

    if not data.payment_term:
        m = PAYMENT_TERM_RE.search(text)
        if m:
            data.payment_term = normalize_date(m.group(1))


_FIELD_KEYWORDS = {"kodas", "code", "vat", "pvm", "adresas", "address",
                    "bankas", "bank", "iban", "swift", "tel", "fax", "email", "el. paštas"}
_ADDRESS_HINT_RE = re.compile(
    r"(?:g\.|gatvė|str\.|straße|street|road|avenue|alėja|pr\.\s|"
    r"\d+[A-Za-z]?,\s*(?:LT-)?\d{4,5})", re.IGNORECASE,
)


def _is_company_name_candidate(line: str) -> bool:
    """Check if a line looks like a plausible company name."""
    s = _clean_field(line)
    low = s.lower().strip(":").strip()
    if not s or len(s) < 3 or len(s) > 120:
        return False
    # Reject markdown headers
    if s.startswith("#"):
        return False
    # Reject section labels
    if low in TABLE_HEADER_WORDS or low in NON_NAME_PHRASES:
        return False
    # Reject entity keywords themselves ("Seller", "Pardavėjas", "Pirkėjas:")
    from app.patterns import ENTITY_KEYWORDS
    if low in ENTITY_KEYWORDS:
        return False
    # Reject lines starting with known field keywords
    first_word = low.split()[0] if low.split() else ""
    if first_word in _FIELD_KEYWORDS or first_word in {"įmonės", "pvm", "mokėtina", "kasos", "visas"}:
        return False
    # Reject pure numbers
    if re.match(r"^\d[\d\s.,]*$", s):
        return False
    # Reject pure addresses (no company legal form)
    if _ADDRESS_HINT_RE.search(s) and not re.search(r"(?:UAB|MB|AB|VšĮ|IĮ|Ltd|GmbH|Inc|SIA|OÜ)\b", s, re.IGNORECASE):
        return False
    # Reject single common words
    if len(s.split()) == 1 and low in {"įmonės", "pavadinimas", "data", "suma", "viso", "pastaba", "pastabos"}:
        return False
    return True


def _extract_entity(text: str, entity: EntityInfo) -> None:
    """Extract company details into an EntityInfo object."""
    clean = _clean_field(text)
    lines = text.strip().split("\n")
    cleaned_lines = [_clean_field(l) for l in lines]

    # Phase 1: Try "**Keyword:** Name" inline format
    name = ""
    from app.patterns import ENTITY_KEYWORDS as _entity_kw
    for kw in _entity_kw:
        m = re.search(
            rf"\*{{0,2}}{re.escape(kw)}:?\*{{0,2}}[:\s]+(.+?)(?:\s+(?:CI|Invoice|No|Nr|Date|Kodas|Code|VAT|PVM)\b|$)",
            text, re.IGNORECASE,
        )
        if m:
            candidate = _clean_field(m.group(1))
            if _is_company_name_candidate(candidate):
                name = candidate
                break

    # Phase 2: Scan all lines for company name candidate (before AND after codes)
    if not name:
        for line_clean in cleaned_lines:
            if _is_company_name_candidate(line_clean):
                name = line_clean
                break

    if name:
        entity.name = name

    m = COMPANY_CODE_RE.search(clean)
    if m:
        value = m.group(1).strip()
        value = re.split(r"\n", value)[0].strip()
        entity.code = _clean_field(value)

    m = VAT_CODE_RE.search(clean)
    if m:
        entity.vat = m.group(1).strip()

    m = ADDRESS_RE.search(clean)
    if m:
        entity.address = _clean_address(m.group(1))
    else:
        for line_clean in cleaned_lines:
            if _ADDRESS_HINT_RE.search(line_clean):
                addr = _clean_address(line_clean)
                if len(addr) > 5:
                    entity.address = addr
                    break


def _extract_totals(text: str, data: InvoiceData) -> None:
    """Extract total amounts from totals block."""
    if not data.total_without_vat:
        m = TOTAL_WITHOUT_VAT_RE.search(text)
        if m:
            data.total_without_vat = normalize_amount(m.group(1))

    if not data.vat_amount:
        m = VAT_AMOUNT_RE.search(text)
        if m:
            data.vat_amount = normalize_amount(m.group(1))

    if not data.grand_total:
        m = GRAND_TOTAL_RE.search(text)
        if m:
            data.grand_total = normalize_amount(m.group(1))

    if not data.total_in_words:
        m = TOTAL_IN_WORDS_RE.search(text)
        if m:
            data.total_in_words = m.group(1).strip()


def _extract_entity_from_full_text(text: str, entity: EntityInfo, role: str) -> None:
    """Fallback: extract entity from full raw text using inline patterns.

    Handles formats like: '**Seller:** Company Name' or '|Pirkėjas:|Company Name|'
    """
    role_patterns = {
        "seller": [r"pardavėjas", r"seller", r"tiekėjas", r"supplier"],
        "buyer": [r"pirkėjas", r"buyer", r"gavėjas", r"customer", r"klientas"],
    }
    for kw in role_patterns.get(role, []):
        # Try "Keyword: Value" or "**Keyword:** Value" inline format
        m = re.search(
            rf"(?:\*{{0,2}}{kw}\*{{0,2}})[:\s|]+([^|*\n]{{3,80}})",
            text, re.IGNORECASE,
        )
        if m:
            candidate = _clean_field(m.group(1))
            if _is_company_name_candidate(candidate):
                entity.name = candidate
                logger.debug("Entity %s name (fallback): %s", role, candidate)
                break

    # Also try to extract code/vat from full text if entity name found but codes missing
    if entity.name:
        # Search near the entity name
        name_idx = text.lower().find(entity.name.lower())
        if name_idx >= 0:
            context = text[name_idx:name_idx + 500]
            if not entity.code:
                m = COMPANY_CODE_RE.search(context)
                if m:
                    entity.code = _clean_field(m.group(1).split("\n")[0])
            if not entity.vat:
                m = VAT_CODE_RE.search(context)
                if m:
                    entity.vat = m.group(1).strip()


def _extract_payment(text: str, data: InvoiceData) -> None:
    """Extract bank/payment details."""
    if not data.iban:
        m = IBAN_RE.search(text)
        if m:
            data.iban = normalize_iban(m.group(1))

    if not data.swift:
        m = SWIFT_RE.search(text)
        if m:
            data.swift = m.group(1).strip()

    if not data.bank_name:
        m = BANK_NAME_RE.search(text)
        if m:
            value = m.group(1).strip().strip('"').strip("'").strip(",")
            if value and len(value) > 2:
                data.bank_name = value

    if not data.bank_code:
        m = BANK_CODE_RE.search(text)
        if m:
            data.bank_code = m.group(1).strip()

    if not data.currency:
        m = CURRENCY_RE.search(text)
        if m:
            data.currency = m.group(1).upper()

    # Fallback: detect bank name from known Lithuanian banks
    if not data.bank_name:
        bank_names = [
            ("swedbank", "AB Swedbank"),
            ("seb", "AB SEB bankas"),
            ("luminor", "Luminor Bank AS"),
            ("šiaulių bankas", "AB Šiaulių bankas"),
            ("citadele", "AS Citadele banka"),
            ("revolut", "Revolut"),
        ]
        text_lower = text.lower()
        for keyword, full_name in bank_names:
            if keyword in text_lower:
                data.bank_name = full_name
                break


def _extract_document_info(text: str, data: InvoiceData) -> None:
    """Extract document info (issued by, etc.)."""
    m = re.search(
        r"(?:išrašė|issued\s*by|prepared\s*by)[:\s]*(.+)",
        text, re.IGNORECASE,
    )
    if m:
        data.issued_by = m.group(1).strip()


# --- Confidence ---

def _compute_confidence(data: InvoiceData) -> float:
    """Compute confidence score (0-1) based on field completeness and plausibility."""
    checks = [
        _is_plausible_value(data.invoice_number, max_len=50),  # Critical
        bool(data.invoice_date),  # Critical
        _is_plausible_value(data.seller.name, min_len=2, max_len=200),  # Critical
        _is_plausible_value(data.buyer.name, min_len=2, max_len=200),  # Critical
        bool(data.items_table),  # Important
        _is_plausible_amount(data.grand_total),  # Important
        bool(data.iban),  # Useful
        bool(data.seller.code),  # Useful
        bool(data.buyer.code),  # Useful
        bool(data.seller.vat or data.buyer.vat),  # Useful
    ]
    weights = [0.15, 0.10, 0.15, 0.15, 0.15, 0.10, 0.05, 0.05, 0.05, 0.05]
    return sum(w for check, w in zip(checks, weights) if check)


def _is_plausible_value(value: str, min_len: int = 1, max_len: int = 100) -> bool:
    """Check if a value looks plausible (not empty, not too long, has substance)."""
    if not value:
        return False
    stripped = value.strip()
    return min_len <= len(stripped) <= max_len


def _is_plausible_amount(value: str) -> bool:
    """Check if amount looks like a valid number."""
    if not value:
        return False
    try:
        num = float(value)
        return num > 0
    except ValueError:
        return False


# --- Helpers ---

def _clean_invoice_number(raw: str) -> str:
    """Clean invoice number: take first line, strip trailing metadata."""
    number = raw.split("\n")[0].strip()
    number = re.sub(r"\s+\d{4}[-./]\d{2}[-./]\d{2}.*$", "", number)
    number = re.sub(r"\s+\d{2}[-./]\d{2}[-./]\d{4}.*$", "", number)
    number = re.sub(r"\s+(?:Invoice|Payment|Date|Data|Term).*$", "", number, flags=re.IGNORECASE)
    number = re.sub(r"\s+\d{4}\s*$", "", number)
    return number.strip()


def _clean_address(raw: str) -> str:
    """Clean address: remove trailing non-address info."""
    addr = _clean_field(raw)
    for cut_pattern in [
        r"\s+(?:Origin|Destination|Date|CI\s*No|Invoice|No\.|Nr\.).*$",
        r"\s+\d{2}/\d{2}/\d{4}$",
        r"\s+\d{4}-\d{2}-\d{2}$",
    ]:
        addr = re.sub(cut_pattern, "", addr, flags=re.IGNORECASE)
    return addr.strip().rstrip(",").strip()


_clean_field = clean_field


# --- Assembly ---

def _entity_section(entity: EntityInfo, heading: str) -> str | None:
    """Build a markdown section for a seller or buyer entity."""
    lines: list[str] = []
    if entity.name:
        lines.append(f"- **Pavadinimas:** {entity.name}")
    if entity.code:
        lines.append(f"- **Įmonės kodas:** {entity.code}")
    if entity.vat:
        lines.append(f"- **PVM kodas:** {entity.vat}")
    if entity.address:
        lines.append(f"- **Adresas:** {entity.address}")
    if lines:
        return f"## {heading}\n" + "\n".join(lines)
    return None


def _assemble_markdown(data: InvoiceData) -> str:
    """Assemble final structured markdown from extracted data."""
    sections: list[str] = []

    title = DOCUMENT_TYPE_TITLES.get(data.document_type, "Sąskaita faktūra")
    sections.append(f"# {title}")

    meta_lines: list[str] = []
    if data.invoice_number:
        meta_lines.append(f"- **Numeris:** {data.invoice_number}")
    if data.invoice_date:
        meta_lines.append(f"- **Data:** {data.invoice_date}")
    if data.payment_term:
        meta_lines.append(f"- **Apmokėjimo terminas:** {data.payment_term}")
    doc_type_labels = {
        "vat_invoice": "PVM sąskaita faktūra",
        "invoice": "Invoice",
        "credit_note": "Credit Note",
        "proforma": "Proforma",
    }
    meta_lines.append(f"- **Tipas:** {doc_type_labels.get(data.document_type, 'Invoice')}")
    if meta_lines:
        sections.append("## Metaduomenys\n" + "\n".join(meta_lines))

    seller_section = _entity_section(data.seller, "Pardavėjas")
    if seller_section:
        sections.append(seller_section)

    buyer_section = _entity_section(data.buyer, "Pirkėjas")
    if buyer_section:
        sections.append(buyer_section)

    if data.items_table:
        sections.append("## Prekės / Paslaugos\n\n" + data.items_table)

    total_lines: list[str] = []
    if data.total_without_vat:
        total_lines.append(f"- **Suma be PVM:** {data.total_without_vat}")
    if data.vat_amount:
        total_lines.append(f"- **PVM suma:** {data.vat_amount}")
    if data.grand_total:
        total_lines.append(f"- **Viso:** {data.grand_total}")
    if data.total_in_words:
        total_lines.append(f"- **Suma žodžiais:** {data.total_in_words}")
    if total_lines:
        sections.append("## Sumos\n" + "\n".join(total_lines))

    payment_lines: list[str] = []
    if data.bank_name:
        payment_lines.append(f"- **Bankas:** {data.bank_name}")
    if data.iban:
        payment_lines.append(f"- **IBAN:** {data.iban}")
    if data.swift:
        payment_lines.append(f"- **BIC/SWIFT:** {data.swift}")
    if data.bank_code:
        payment_lines.append(f"- **Banko kodas:** {data.bank_code}")
    if data.currency:
        payment_lines.append(f"- **Valiuta:** {data.currency}")
    if payment_lines:
        sections.append("## Mokėjimo rekvizitai\n" + "\n".join(payment_lines))

    if data.notes:
        notes_text = "\n".join(n for n in data.notes if n.strip())
        if notes_text:
            sections.append("## Pastabos\n" + notes_text)

    if data.issued_by:
        sections.append("## Dokumento informacija\n" + f"- **Išrašė:** {data.issued_by}")

    return "\n\n".join(sections) + "\n"
