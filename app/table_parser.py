"""Parse and reconstruct invoice item tables from raw markdown."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.normalizers import clean_field, normalize_amount, normalize_percentage
from app.patterns import TABLE_COLUMN_MAP, TABLE_SKIP_COLUMNS

logger = logging.getLogger(__name__)

STANDARD_COLUMNS = ["Nr.", "Aprašymas", "Kiekis", "Vnt.", "Kaina", "PVM %", "Suma"]


@dataclass
class TableResult:
    markdown: str
    grand_total: str = ""


def extract_table_from_blocks(
    blocks: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], TableResult | None]:
    """Extract item table blocks and return (remaining_blocks, table_result).

    Finds all blocks classified as 'items', parses them, and returns
    the remaining blocks plus a TableResult with clean markdown table
    and any extracted grand total from the table's total row.
    """
    item_blocks: list[str] = []
    remaining: list[tuple[str, str]] = []

    for section, text in blocks:
        if section == "items":
            item_blocks.append(text)
        else:
            remaining.append((section, text))

    if not item_blocks:
        return blocks, None

    combined = "\n\n".join(item_blocks)
    rows = _parse_pipe_table(combined)

    if not rows:
        logger.debug("No pipe table found, trying whitespace fallback")
        rows = _parse_whitespace_table(combined)
        if rows:
            logger.info("Used whitespace fallback parser (%d rows)", len(rows))

    if not rows:
        logger.debug("No table rows could be extracted")
        return blocks, None

    item_rows, grand_total = _filter_total_rows(rows)

    if not item_rows:
        logger.debug("All rows were filtered as totals")
        return blocks, None

    logger.debug("Extracted table: %d items, grand_total=%s", len(item_rows), grand_total or "N/A")
    table = _build_markdown_table(item_rows)
    return remaining, TableResult(markdown=table, grand_total=grand_total)


_clean_cell = clean_field


def _parse_pipe_table(text: str) -> list[dict[str, str]]:
    """Parse pipe-delimited markdown table into list of row dicts."""
    lines = text.strip().split("\n")
    pipe_lines = [l.strip() for l in lines if "|" in l]

    if len(pipe_lines) < 2:
        return []

    # Find header line (first pipe line that's not a separator)
    header_line = None
    data_lines: list[str] = []
    found_separator = False

    for line in pipe_lines:
        # Skip separator lines (|---|---|...)
        if re.match(r"^\|?\s*[-:]+\s*\|", line):
            found_separator = True
            continue

        if header_line is None and not found_separator:
            header_line = line
        elif found_separator:
            data_lines.append(line)

    # If no separator found, first line is header, rest are data
    if not found_separator and header_line:
        data_lines = pipe_lines[1:]
        data_lines = [l for l in data_lines if not re.match(r"^\|?\s*[-:]+\s*\|", l)]

    if not header_line:
        return []

    headers = _split_pipe_line(header_line)
    if not headers:
        return []

    # Map headers to standard column names
    mapped_headers = _map_headers(headers)

    rows: list[dict[str, str]] = []
    for line in data_lines:
        cells = _split_pipe_line(line)
        if not cells:
            continue

        row: dict[str, str] = {}
        all_cells: list[str] = []
        for i, cell in enumerate(cells):
            cleaned = _clean_cell(cell)
            all_cells.append(cleaned)
            if i < len(mapped_headers):
                col_name = mapped_headers[i]
                if col_name:
                    row[col_name] = cleaned
        # Store all raw cells for total detection
        row["_all_cells"] = "|".join(all_cells)
        if any(v for k, v in row.items() if k != "_all_cells" and v):
            rows.append(row)

    return rows


def _split_pipe_line(line: str) -> list[str]:
    """Split a pipe-delimited line into cells."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _map_headers(headers: list[str]) -> list[str | None]:
    """Map raw header names to standard column names."""
    mapped: list[str | None] = []
    for h in headers:
        h_clean = _clean_cell(h)
        h_lower = h_clean.lower()

        # Check if this is a column to skip
        if h_lower in TABLE_SKIP_COLUMNS:
            mapped.append(None)
            continue

        if h_lower in TABLE_COLUMN_MAP:
            mapped.append(TABLE_COLUMN_MAP[h_lower])
        else:
            # Fuzzy match: check if any key is contained in the header
            found = False
            for key, standard in TABLE_COLUMN_MAP.items():
                if key in h_lower or h_lower in key:
                    mapped.append(standard)
                    found = True
                    break
            if not found:
                skip = any(
                    skip_col in h_lower or h_lower in skip_col
                    for skip_col in TABLE_SKIP_COLUMNS
                )
                if skip:
                    mapped.append(None)
                else:
                    logger.warning("Unrecognized table column: '%s'", h_clean)
                    mapped.append(None)

    return mapped


def _filter_total_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    """Separate item rows from total rows. Returns (item_rows, grand_total_amount)."""
    filtered: list[dict[str, str]] = []
    grand_total = ""

    for row in rows:
        is_total = False
        # Check all cells (including unmapped ones via _all_cells)
        check_values = list(row.values())
        all_cells_str = row.get("_all_cells", "")
        if all_cells_str:
            check_values.extend(all_cells_str.split("|"))

        for value in check_values:
            v_lower = value.lower().strip()
            if v_lower in ("total", "viso", "iš viso", "bendra suma", "subtotal", "total："):
                is_total = True
                break
            if v_lower.startswith("total") or v_lower.startswith("viso"):
                is_total = True
                break
            # Match "Suma 228.10 EUR", "PVM 21% 47.90 EUR", "Bendra suma su PVM"
            if re.match(r"^(?:suma|pvm|bendra\s+suma|apmokėjimui|datums|bezpvn|pvn)\b", v_lower):
                is_total = True
                break
            # Date-only rows are usually totals/metadata rows in some PDFs
            if re.match(r"^\d{2}\.\d{2}\.\d{4}$", v_lower.strip()):
                is_total = True
                break

        if is_total:
            for col in ("Suma", "Kaina"):
                val = row.get(col, "").strip()
                if val and re.search(r"\d", val):
                    grand_total = normalize_amount(val)
                    break
        else:
            desc = row.get("Aprašymas", "").strip()
            if desc or not any(row.get(c) for c in ["Kaina", "Suma"]):
                filtered.append(row)

    # Clean up _all_cells from output rows
    for row in filtered:
        row.pop("_all_cells", None)

    return filtered, grand_total


def _parse_whitespace_table(text: str) -> list[dict[str, str]]:
    """Fallback: parse whitespace-aligned tabular data."""
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 2:
        return []

    rows: list[dict[str, str]] = []
    for line in lines:
        amounts = re.findall(r"\d+[.,]\d{2}", line)
        if amounts:
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 3:
                row: dict[str, str] = {
                    "Aprašymas": _clean_cell(parts[0]),
                }
                nums = [p for p in parts[1:] if re.search(r"\d", p)]
                if len(nums) >= 3:
                    row["Kiekis"] = nums[0]
                    row["Kaina"] = nums[-2]
                    row["Suma"] = nums[-1]
                elif len(nums) == 2:
                    row["Kaina"] = nums[0]
                    row["Suma"] = nums[1]
                elif len(nums) == 1:
                    row["Suma"] = nums[0]
                rows.append(row)

    return rows


def _build_markdown_table(rows: list[dict[str, str]]) -> str:
    """Build a standard markdown table from parsed rows."""
    if not rows:
        return ""

    # Determine which columns have data
    used_columns: list[str] = []
    for col in STANDARD_COLUMNS:
        if any(row.get(col) for row in rows):
            used_columns.append(col)

    # Ensure at least Aprašymas and Suma
    if "Aprašymas" not in used_columns:
        used_columns.insert(0, "Aprašymas")
    if "Suma" not in used_columns:
        used_columns.append("Suma")
    if "Nr." not in used_columns:
        used_columns.insert(0, "Nr.")

    # Build header
    header = "| " + " | ".join(used_columns) + " |"
    separator = "|" + "|".join("-----" for _ in used_columns) + "|"

    # Build rows
    table_rows: list[str] = []
    for i, row in enumerate(rows):
        cells: list[str] = []
        for col in used_columns:
            value = row.get(col, "")
            if col == "Nr." and not value:
                value = str(i + 1)
            elif col in ("Kaina", "Suma"):
                if value:
                    value = normalize_amount(value)
            elif col == "PVM %":
                if value:
                    value = normalize_percentage(value)
            elif col == "Kiekis":
                # Clean quantity: remove non-numeric except decimal
                if value:
                    value = re.sub(r"[^\d.,]", "", value).strip() or value
            cells.append(value)
        table_rows.append("| " + " | ".join(cells) + " |")

    return "\n".join([header, separator] + table_rows)
