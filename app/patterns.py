"""Invoice keyword patterns and regex for LT/EN document parsing."""

import re

# --- Section keyword mappings ---
# Each key maps to a list of trigger phrases (lowercase) that indicate a section.

SECTION_KEYWORDS: dict[str, list[str]] = {
    "metadata": [
        "sąskaita faktūra",
        "pvm sąskaita",
        "sąskaita-faktūra",
        "invoice",
        "credit note",
        "kreditinė",
        "proforma",
        "išankstinė",
        "serija",
        "invoice number",
        "invoice date",
        "sąskaitos nr",
        "sąskaitos data",
        "sąskaitos numeris",
        "payment term",
        "apmokėjimo terminas",
        "mokėjimo terminas",
        "due date",
        "issue date",
        "išrašymo data",
    ],
    "seller": [
        "pardavėjas",
        "tiekėjas",
        "seller",
        "supplier",
        "vendor",
        "from:",
        "išrašė",
    ],
    "buyer": [
        "pirkėjas",
        "buyer",
        "gavėjas",
        "recipient",
        "klientas",
        "customer",
        "client",
        "bill to",
        "to:",
    ],
    "items": [
        "prekės",
        "paslaugos",
        "items",
        "services",
        "description",
        "aprašymas",
        "pavadinimas",
        "quantity",
        "kiekis",
        "unit price",
        "vieneto kaina",
    ],
    "totals": [
        "suma be pvm",
        "pvm suma",
        "viso mokėti",
        "viso",
        "iš viso",
        "bendra suma",
        "total without vat",
        "total",
        "subtotal",
        "vat amount",
        "mokėtina suma",
        "grand total",
        "total in words",
        "suma žodžiais",
    ],
    "payment": [
        "mokėjimo rekvizitai",
        "mokėjimo instrukcijos",
        "payment instructions",
        "payment details",
        "bank details",
        "banko rekvizitai",
        "banking details",
    ],
    "notes": [
        "pastaba",
        "pastabos",
        "note",
        "notes",
        "additional comment",
        "papildomas komentaras",
        "reverse charge",
        "article 196",
        "pvz netaikomas",
        "pvm netaikomas",
        "atvirkštinis apmokestinimas",
    ],
    "document_info": [
        "išrašė",
        "issued by",
        "prepared by",
        "sąskaitą priėmė",
        "invoice accepted",
        "parašas",
        "signature",
    ],
}

# --- Document type detection ---

DOCUMENT_TYPE_PATTERNS: list[tuple[str, str]] = [
    # (regex_pattern, document_type)
    (r"kreditin[ėe]\s+pvm\s+sąskait[aą]\s*[-–]?\s*faktūr[aą]", "credit_note"),
    (r"kreditin[ėe]\s+sąskait[aą]", "credit_note"),
    (r"credit\s+note", "credit_note"),
    (r"pvm\s+sąskait[aą]\s*[-–]?\s*faktūr[aą]", "vat_invoice"),
    (r"sąskait[aą]\s*[-–]?\s*faktūr[aą]", "invoice"),
    (r"išankstin[ėe]\s+sąskait[aą]", "proforma"),
    (r"proforma\s+invoice", "proforma"),
    (r"proforma", "proforma"),
    (r"commercial\s+invoice", "invoice"),
    (r"vat\s+invoice", "vat_invoice"),
    (r"tax\s+invoice", "vat_invoice"),
    (r"invoice", "invoice"),
]

DOCUMENT_TYPE_TITLES: dict[str, str] = {
    "vat_invoice": "PVM sąskaita faktūra",
    "invoice": "Sąskaita faktūra",
    "credit_note": "Kreditinė sąskaita",
    "proforma": "Išankstinė sąskaita",
}

# --- Regex patterns for field extraction ---

# Invoice number
INVOICE_NUMBER_RE = re.compile(
    r"(?:(?:sąskaitos?\s*(?:faktūros?\s*)?)?(?:nr\.?|numeris|number|no\.?|ci\s*no\.?))[:\s]*"
    r"([A-Z]{0,5}\s*[\w][\w\s/-]*\d[\w]*)",
    re.IGNORECASE,
)
# Specific patterns for known formats
INVOICE_NUMBER_SPECIFIC_RE = re.compile(
    r"(?:invoice\s+number|sąskaitos\s+numeris|sf\s+nr\.?|serija\s+\w+\s+nr\.?)[:\s]*"
    r"([\w][\w\s/-]*\w)",
    re.IGNORECASE,
)
# Fallback: "Invoice NB 2" style — but exclude "Invoice date", "Invoice accepted" etc.
INVOICE_NUMBER_FALLBACK_RE = re.compile(
    r"(?:invoice|sąskaita)\s+(?!date|data|accepted|priėmė|išrašė|number|numeris)"
    r"([A-Z]{1,5}\s*[\d][\w/-]*)",
    re.IGNORECASE,
)

# Dates (YYYY-MM-DD or DD.MM.YYYY or DD/MM/YYYY)
DATE_RE = re.compile(
    r"(\d{4}[-./]\d{2}[-./]\d{2}|\d{2}[-./]\d{2}[-./]\d{4})"
)

INVOICE_DATE_RE = re.compile(
    r"(?:invoice\s*date|sąskaitos\s*data|data|date|išrašymo\s*data)[:\s]*"
    r"(\d{4}[-./]\d{2}[-./]\d{2}|\d{2}[-./]\d{2}[-./]\d{4})",
    re.IGNORECASE,
)

PAYMENT_TERM_RE = re.compile(
    r"(?:payment\s*term|apmokėjimo\s*terminas|mokėjimo\s*terminas|due\s*date|"
    r"apmokėti\s*iki)[:\s]*"
    r"(\d{4}[-./]\d{2}[-./]\d{2}|\d{2}[-./]\d{2}[-./]\d{4})",
    re.IGNORECASE,
)

# Company identifiers
COMPANY_CODE_RE = re.compile(
    r"(?:įmonės\s*kodas|company\s*code|reg(?:istration)?\.?\s*(?:no|code|nr))[:\s]*"
    r"([\w\s().-]{3,30}?)(?=\s+(?:PVM|VAT|įmonės|company|adresas|address|bankas|bank)|$|\n)",
    re.IGNORECASE,
)

VAT_CODE_RE = re.compile(
    r"(?:pvm\s*(?:mokėtojo\s*)?kodas|vat\s*(?:code|no|number|id|reg))[:\s]*"
    r"([A-Z]{2}[\w]+)",
    re.IGNORECASE,
)

ADDRESS_RE = re.compile(
    r"(?:adresas|address)[:\s]*(.+)",
    re.IGNORECASE,
)

# Banking
IBAN_RE = re.compile(
    r"(?:IBAN[:\s]*)?([A-Z]{2}\d{2}\s*(?:\d{4}\s*){3,6}\d{1,4})",
    re.IGNORECASE,
)

SWIFT_RE = re.compile(
    r"(?:BIC|SWIFT|BIC/SWIFT)[:\s]*([A-Z]{6}[A-Z0-9]{2,5})",
    re.IGNORECASE,
)

BANK_NAME_RE = re.compile(
    r'(?:bankas|bank)[:\s]*["\']?(.+?)(?:["\']?\s*$)',
    re.IGNORECASE | re.MULTILINE,
)

BANK_CODE_RE = re.compile(
    r"(?:banko\s*kodas|bank\s*code)[:\s]*(\d+)",
    re.IGNORECASE,
)

CURRENCY_RE = re.compile(
    r"(?:valiuta|currency)[:\s]*([A-Z]{3})",
    re.IGNORECASE,
)

# Amounts
AMOUNT_RE = re.compile(
    r"(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€|Eur|USD|\$|GBP|£)?",
)

# Total patterns
TOTAL_WITHOUT_VAT_RE = re.compile(
    r"(?:suma\s*be\s*pvm|total\s*(?:without|excl(?:uding)?\.?)\s*vat|"
    r"subtotal|bendra\s*suma\s*be\s*pvm|bendra\s*suma\s*€?\s*be\s*pvm)[:\s*]*"
    r"(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€|Eur)?",
    re.IGNORECASE,
)

VAT_AMOUNT_RE = re.compile(
    r"(?:pvm\s*suma|vat\s*amount|pvm\s*\(\d+\s*%?\))[:\s]*"
    r"(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€|Eur)?",
    re.IGNORECASE,
)

GRAND_TOTAL_RE = re.compile(
    r"(?:viso\s*(?:mokėti|su\s*pvm)?|iš\s*viso(?:\s*su\s*pvm)?|"
    r"bendra\s*suma(?:\s*su\s*pvm)?|grand\s*total|"
    r"total\s*(?:amount|usd|eur)?|mokėtina\s*suma|"
    r"suma\s*apmokėjimui|iš\s*viso\s*su\s*pvm\s*\(?\w*\)?)"
    r"[:\s*]*(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€|Eur|USD|\$)?",
    re.IGNORECASE,
)

TOTAL_IN_WORDS_RE = re.compile(
    r"(?:suma\s*žodžiais|total\s*in\s*words|total\s*(?:usd|eur)\s*:|"
    r"mokėtina\s*suma\s*žodžiais|bendra\s*suma\s*(?:eur|€))[:\s]*(.+)",
    re.IGNORECASE,
)

# Language detection
LT_KEYWORDS = [
    "pardavėjas", "pirkėjas", "sąskaita", "faktūra", "pvm",
    "įmonės kodas", "adresas", "mokėjimo", "viso", "prekės",
]
EN_KEYWORDS = [
    "seller", "buyer", "invoice", "company code", "address",
    "payment", "total", "items", "description", "quantity",
]

# Table column header mappings (source → standard)
TABLE_COLUMN_MAP: dict[str, str] = {
    # Nr.
    "nr": "Nr.",
    "nr.": "Nr.",
    "no": "Nr.",
    "no.": "Nr.",
    "#": "Nr.",
    "eil. nr.": "Nr.",
    "eil.nr.": "Nr.",
    "eil. nr": "Nr.",
    "line": "Nr.",
    # Aprašymas
    "aprašymas": "Aprašymas",
    "pavadinimas": "Aprašymas",
    "prekė": "Aprašymas",
    "paslauga": "Aprašymas",
    "prekės/paslaugos pavadinimas": "Aprašymas",
    "description": "Aprašymas",
    "item": "Aprašymas",
    "service": "Aprašymas",
    "product": "Aprašymas",
    "prekės pavadinimas": "Aprašymas",
    # Kiekis
    "kiekis": "Kiekis",
    "quantity": "Kiekis",
    "quantity(pcs)": "Kiekis",
    "qty": "Kiekis",
    "qty.": "Kiekis",
    # Vnt.
    "vnt": "Vnt.",
    "vnt.": "Vnt.",
    "unit": "Vnt.",
    "mat. vnt.": "Vnt.",
    "matavimo vnt.": "Vnt.",
    "mato vnt.": "Vnt.",
    # Kaina
    "kaina": "Kaina",
    "vieneto kaina": "Kaina",
    "price": "Kaina",
    "unit price": "Kaina",
    "price (excl. vat)": "Kaina",
    "unit price(usd)": "Kaina",
    "unit price(eur)": "Kaina",
    "kaina be pvm": "Kaina",
    "kaina (be pvm)": "Kaina",
    "total price (excl. vat)": "Suma",
    # PVM %
    "pvm": "PVM %",
    "pvm %": "PVM %",
    "pvm, %": "PVM %",
    "pvm tarifas": "PVM %",
    "vat": "PVM %",
    "vat %": "PVM %",
    "vat rate": "PVM %",
    "tax": "PVM %",
    "tax %": "PVM %",
    # Suma
    "suma": "Suma",
    "suma be pvm": "Suma",
    "suma (be pvm)": "Suma",
    "total": "Suma",
    "amount": "Suma",
    "subtotal": "Suma",
    "total price": "Suma",
    "line total": "Suma",
    "suma su pvm": "Suma",
    "total price(usd)": "Suma",
    "total price(eur)": "Suma",
}

# Columns to skip (not relevant for invoice items)
TABLE_SKIP_COLUMNS: set[str] = {
    "id no.", "id no", "id", "sku", "hs code", "eu hs code",
    "proforma no.", "proforma no", "proforma",
}
