"""Build column-aware markdown from PDF using pdfplumber for text and Docling for tables.

pdfplumber provides more accurate text extraction with precise word positions,
while Docling excels at table structure detection. This hybrid approach
ensures no text elements are lost (especially horizontally positioned blocks).
"""

import logging
from dataclasses import dataclass

import pdfplumber
from docling_core.types.doc.document import DoclingDocument

logger = logging.getLogger(__name__)


@dataclass
class _Element:
    """A positioned text or table element."""
    text: str
    x: float
    y: float  # pdfplumber top-down coordinate (distance from page top)
    width: float
    label: str
    is_table: bool = False


@dataclass
class _TableRegion:
    """A table region from Docling with bounding box in pdfplumber coords."""
    page_no: int
    top: float
    bottom: float
    markdown: str


def build_markdown(doc: DoclingDocument, pdf_path: str) -> str:
    """Build markdown from PDF with column-aware layout detection.

    Uses pdfplumber for text extraction (more complete than Docling for
    horizontally spread text blocks) and Docling for table structure.
    """
    table_regions = _get_table_regions(doc)
    pages = _extract_elements_by_page(pdf_path, table_regions)
    sections: list[str] = []

    for page_no in sorted(pages.keys()):
        elements = pages[page_no]
        if not elements:
            continue

        page_width = _get_page_width_pdfplumber(pdf_path, page_no)
        columns = _detect_columns(elements, page_width)

        if len(columns) > 1:
            logger.debug("Page %d: detected %d columns", page_no, len(columns))
            for col_elements in columns:
                md = _elements_to_markdown(col_elements)
                if md.strip():
                    sections.append(md)
        else:
            md = _elements_to_markdown(elements)
            if md.strip():
                sections.append(md)

    result = "\n\n".join(sections)

    # Append tables from Docling's structured table data
    for table in doc.tables:
        table_md = table.export_to_markdown(doc)
        if table_md and table_md.strip():
            result += "\n\n" + table_md

    return _normalize_quotes(result.strip())


def _normalize_quotes(text: str) -> str:
    """Replace typographic quotes with standard ASCII double quotes."""
    for ch in "„""\u201e\u201c\u201d\u00ab\u00bb":
        text = text.replace(ch, '"')
    return text


def _get_table_regions(doc: DoclingDocument) -> list[_TableRegion]:
    """Extract table bounding boxes from Docling, converted to pdfplumber coords."""
    regions = []

    page_heights: dict[int, float] = {}
    if doc.pages:
        for page_no, page in doc.pages.items():
            if hasattr(page, "size") and page.size:
                page_heights[page_no] = page.size.height

    for table in doc.tables:
        if not table.prov:
            continue
        prov = table.prov[0]
        bbox = prov.bbox
        page_no = prov.page_no
        page_height = page_heights.get(page_no, 841.89)

        # Convert Docling coords (y bottom-up) to pdfplumber (y top-down)
        top = page_height - bbox.t
        bottom = page_height - bbox.b

        md = table.export_to_markdown(doc)
        if md and md.strip():
            regions.append(_TableRegion(
                page_no=page_no,
                top=top,
                bottom=bottom,
                markdown=md.strip(),
            ))

    return regions


def _is_in_table(top: float, page_no: int, table_regions: list[_TableRegion], margin: float = 2.0) -> bool:
    """Check if a y position falls within any table region."""
    for tr in table_regions:
        if tr.page_no == page_no and (tr.top - margin) <= top <= (tr.bottom + margin):
            return True
    return False


def _extract_elements_by_page(
    pdf_path: str,
    table_regions: list[_TableRegion],
) -> dict[int, list[_Element]]:
    """Extract text elements with positions using pdfplumber, grouped by page.

    Words inside Docling-detected table regions are excluded since tables
    are rendered separately using Docling's structured table markdown.
    """
    pages: dict[int, list[_Element]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            page_no = page_idx + 1
            words = page.extract_words()
            if not words:
                continue

            # Detect median font size for heading detection
            heights = [w["height"] for w in words]
            median_h = sorted(heights)[len(heights) // 2] if heights else 10.0

            elements: list[_Element] = []
            for w in words:
                top = w["top"]

                # Skip words inside table bounding boxes
                if _is_in_table(top, page_no, table_regions):
                    continue

                is_heading = w["height"] > median_h * 1.3
                label = "section_header" if is_heading else "text"

                elements.append(_Element(
                    text=w["text"],
                    x=w["x0"],
                    y=top,
                    width=w["x1"] - w["x0"],
                    label=label,
                ))

            if elements:
                elements.sort(key=lambda e: (e.y, e.x))
                pages[page_no] = elements

    return pages


def _get_page_width_pdfplumber(pdf_path: str, page_no: int) -> float:
    """Get page width from pdfplumber."""
    with pdfplumber.open(pdf_path) as pdf:
        if page_no <= len(pdf.pages):
            return pdf.pages[page_no - 1].width
    return 595.0  # A4 default


def _detect_columns(elements: list[_Element], page_width: float) -> list[list[_Element]]:
    """Detect if elements form multiple side-by-side content columns.

    Only splits when there are clearly separate content areas (e.g. seller | buyer).
    Does NOT split key-value layouts where labels are left and values are right.
    """
    if len(elements) < 6:
        return [elements]

    # Group elements by Y position (same-line elements)
    y_groups: dict[int, list[_Element]] = {}
    for e in elements:
        y_key = round(e.y / 3) * 3  # 3px tolerance
        y_groups.setdefault(y_key, []).append(e)

    # Count how many lines have wide-spread elements (potential columns)
    wide_lines = 0
    narrow_lines = 0
    for group in y_groups.values():
        if len(group) < 2:
            continue
        xs = [e.x for e in group]
        spread = max(xs) - min(xs)
        if spread > page_width * 0.4:
            wide_lines += 1
        else:
            narrow_lines += 1

    # If mostly key-value pairs (narrow spread), don't split into columns
    if narrow_lines > wide_lines * 2:
        return [elements]

    # Need at least 3 wide-spread lines to consider it a multi-column layout
    if wide_lines < 3:
        return [elements]

    # Find the split point using X positions of wide-spread lines only
    wide_elements: list[_Element] = []
    for group in y_groups.values():
        xs = [e.x for e in group]
        if len(group) >= 2 and max(xs) - min(xs) > page_width * 0.4:
            wide_elements.extend(group)

    if len(wide_elements) < 6:
        return [elements]

    x_positions = sorted(set(round(e.x) for e in wide_elements))
    max_gap = 0.0
    split_x = 0.0
    for i in range(len(x_positions) - 1):
        gap = x_positions[i + 1] - x_positions[i]
        if gap > max_gap:
            max_gap = gap
            split_x = (x_positions[i] + x_positions[i + 1]) / 2

    if max_gap < page_width * 0.15:
        return [elements]

    left = [e for e in elements if e.x < split_x]
    right = [e for e in elements if e.x >= split_x]

    if len(left) < 3 or len(right) < 3:
        return [elements]

    # Verify Y overlap (columns must be side-by-side)
    left_ys = {round(e.y, -1) for e in left}
    right_ys = {round(e.y, -1) for e in right}
    if len(left_ys & right_ys) < 2:
        return [elements]

    logger.debug("Column split at x=%.0f (gap=%.0f, wide_lines=%d)",
                 split_x, max_gap, wide_lines)

    return [left, right]


def _elements_to_markdown(elements: list[_Element]) -> str:
    """Convert positioned elements to markdown text.

    Merges elements on the same Y line into single lines.
    """
    # Group elements by Y position (within 3px tolerance)
    y_groups: list[list[_Element]] = []
    current_group: list[_Element] = []
    current_y: float | None = None

    for el in elements:
        if current_y is not None and abs(el.y - current_y) < 3:
            current_group.append(el)
        else:
            if current_group:
                y_groups.append(current_group)
            current_group = [el]
            current_y = el.y

    if current_group:
        y_groups.append(current_group)

    lines: list[str] = []
    for group in y_groups:
        # Sort by X within the same line
        group.sort(key=lambda e: e.x)

        text = " ".join(e.text for e in group)

        # Check for heading
        if any(e.label in ("section_header", "title") for e in group):
            lines.append(f"## {text}")
        elif any(e.label == "page_header" for e in group):
            lines.append(f"# {text}")
        else:
            lines.append(text)

    return "\n".join(lines)
