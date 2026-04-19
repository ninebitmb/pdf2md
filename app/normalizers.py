"""Value normalization functions for invoice data."""

import logging
import re

logger = logging.getLogger(__name__)


def normalize_amount(raw: str) -> str:
    """Normalize monetary amount to plain decimal format.

    '1 234,56 €' → '1234.56'
    '1,234.56 EUR' → '1234.56'
    '1016.00 €' → '1016.00'
    Returns the raw stripped value if parsing fails.
    """
    # Remove currency symbols and letters
    s = re.sub(r"[€$£]", "", raw)
    s = re.sub(r"\b(?:EUR|USD|GBP|Eur)\b", "", s)
    s = s.strip()

    if not s:
        logger.debug("normalize_amount: empty after stripping currency from %r", raw)
        return raw.strip()

    # Detect format: European (1.234,56) vs English (1,234.56)
    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_comma > last_dot:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        parts = s.split(",")
        after_comma = parts[-1].strip()
        if len(parts) == 2 and len(after_comma) <= 2:
            # "1234,56" → decimal separator (1-2 digits after comma)
            s = s.replace(",", ".")
        else:
            # "1,000" or "1,234,567" → thousands separator (3 digits after comma)
            s = s.replace(",", "")

    # Remove spaces from numbers
    s = re.sub(r"\s", "", s)

    # Remove any remaining non-numeric chars except dot and minus
    s = re.sub(r"[^\d.\-]", "", s)

    if not s or not re.search(r"\d", s):
        logger.warning("normalize_amount: could not parse number from %r", raw)
        return raw.strip()

    # Ensure proper decimal format
    if "." not in s:
        s += ".00"
    else:
        integer, decimal = s.rsplit(".", 1)
        if not integer:
            integer = "0"
        decimal = decimal[:2].ljust(2, "0")
        s = f"{integer}.{decimal}"

    return s


def normalize_iban(raw: str) -> str:
    """Remove all whitespace from IBAN."""
    return re.sub(r"\s", "", raw).upper()


def normalize_date(raw: str) -> str:
    """Standardize date to YYYY-MM-DD format. Returns raw if unparseable."""
    raw = raw.strip()
    normalized = raw.replace("/", "-").replace(".", "-")
    parts = normalized.split("-")

    if len(parts) != 3:
        logger.debug("normalize_date: cannot parse %r", raw)
        return raw

    if len(parts[0]) == 4:
        return normalized
    elif len(parts[2]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"

    logger.debug("normalize_date: unknown format %r", raw)
    return raw


def normalize_percentage(raw: str) -> str:
    """Extract percentage as plain number. '21 %' → '21', '0%' → '0'."""
    match = re.search(r"(\d+(?:[.,]\d+)?)", raw)
    if match:
        return match.group(1).replace(",", ".")
    logger.debug("normalize_percentage: no number found in %r", raw)
    return raw.strip()


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_STRIKETHROUGH_RE = re.compile(r"~~(.+?)~~")
_ITALIC_RE = re.compile(r"_(.+?)_")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html_tags(raw: str) -> str:
    """Remove all HTML tags from text."""
    return _HTML_TAG_RE.sub(" ", raw)


def strip_markdown_formatting(raw: str) -> str:
    """Remove markdown formatting: bold, italic, strikethrough."""
    s = raw.replace("**", "")
    s = _STRIKETHROUGH_RE.sub(r"\1", s)
    s = _ITALIC_RE.sub(r"\1", s)
    return s


def clean_field(raw: str) -> str:
    """Clean text: strip HTML, markdown formatting, collapse whitespace."""
    s = strip_html_tags(raw)
    s = strip_markdown_formatting(s)
    s = _WHITESPACE_RE.sub(" ", s)
    return s.strip()

