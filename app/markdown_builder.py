"""Build column-aware markdown from Docling's rich document model.

Instead of export_to_markdown() which merges multi-column layouts,
this uses element bounding boxes to detect columns and produce
correctly ordered markdown.
"""

import logging
from dataclasses import dataclass, field

from docling_core.types.doc.document import DoclingDocument

logger = logging.getLogger(__name__)


@dataclass
class _Element:
    """A positioned text or table element."""
    text: str
    x: float
    y: float
    width: float
    label: str
    is_table: bool = False


def build_markdown(doc: DoclingDocument) -> str:
    """Build markdown from DoclingDocument with column-aware layout detection.

    Detects multi-column layouts (e.g. seller | buyer side by side)
    and outputs them sequentially instead of interleaved.
    """
    pages = _extract_elements_by_page(doc)
    sections: list[str] = []

    for page_no in sorted(pages.keys()):
        elements = pages[page_no]
        if not elements:
            continue

        page_width = _get_page_width(doc, page_no)
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

    # Also export tables using Docling's own table markdown (more accurate)
    result = "\n\n".join(sections)

    # Append tables from Docling's structured table data
    for table in doc.tables:
        table_md = table.export_to_markdown(doc)
        if table_md and table_md.strip():
            result += "\n\n" + table_md

    return result.strip()


def _extract_elements_by_page(doc: DoclingDocument) -> dict[int, list[_Element]]:
    """Extract all text elements with positions, grouped by page."""
    pages: dict[int, list[_Element]] = {}

    for item, level in doc.iterate_items():
        if not hasattr(item, "prov") or not item.prov:
            continue

        prov = item.prov[0]
        bbox = prov.bbox
        page_no = prov.page_no

        if hasattr(item, "text") and item.text:
            label = str(getattr(item, "label", "text"))
            el = _Element(
                text=item.text.strip(),
                x=bbox.l,
                y=bbox.t,
                width=bbox.r - bbox.l,
                label=label,
            )
            pages.setdefault(page_no, []).append(el)

    # Sort elements by Y position (top to bottom), then X (left to right)
    for page_no in pages:
        pages[page_no].sort(key=lambda e: (-e.y, e.x))  # -y because PDF coords are bottom-up

    return pages


def _get_page_width(doc: DoclingDocument, page_no: int) -> float:
    """Get page width from document metadata."""
    if doc.pages and page_no in doc.pages:
        page = doc.pages[page_no]
        if hasattr(page, "size") and page.size:
            return page.size.width
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
    # vs narrow key-value pairs
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

    Merges elements on the same Y line into single lines (label: value pairs).
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

        # Check for heading
        if any(e.label in ("section_header", "title") for e in group):
            text = " ".join(e.text for e in group)
            lines.append(f"## {text}")
        elif any(e.label == "page_header" for e in group):
            text = " ".join(e.text for e in group)
            lines.append(f"# {text}")
        elif len(group) == 2:
            # Likely label: value pair
            lines.append(f"{group[0].text} {group[1].text}")
        elif len(group) > 2:
            # Multiple elements on same line
            lines.append(" ".join(e.text for e in group))
        else:
            lines.append(group[0].text)

    return "\n".join(lines)
