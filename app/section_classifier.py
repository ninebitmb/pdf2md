"""Classify raw markdown blocks into invoice sections."""

from __future__ import annotations

import logging
import re

from app.patterns import BUYER_KEYWORDS, ENTITY_KEYWORDS, IBAN_RE, SECTION_KEYWORDS, SELLER_KEYWORDS, TABLE_HEADER_WORDS

logger = logging.getLogger(__name__)


def split_into_blocks(raw_markdown: str) -> list[str]:
    """Split raw markdown into logical blocks by markdown headers.

    Groups content under ## headers into single blocks.
    Non-header content before the first header becomes its own block.
    Content without headers is split by double newlines as fallback.
    """
    lines = raw_markdown.strip().split("\n")
    blocks: list[str] = []
    current_block: list[str] = []

    has_headers = any(line.strip().startswith("##") for line in lines)

    if not has_headers:
        # Fallback: split by double newlines then merge small blocks
        # that belong to the same section (e.g. "Pardavėjas:" followed by company data)
        raw_blocks = re.split(r"\n{2,}", raw_markdown.strip())
        raw_blocks = [b.strip() for b in raw_blocks if b.strip()]
        return _merge_label_blocks(raw_blocks)

    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##"):
            # Save previous block
            if current_block:
                text = "\n".join(current_block).strip()
                if text:
                    blocks.append(text)
            current_block = [stripped]
            in_table = False
        elif stripped.startswith("|"):
            # Table line — start new block if not already in a table
            if not in_table and current_block:
                # Check if current block already has non-table content
                non_table = [l for l in current_block if not l.strip().startswith("|")]
                if non_table:
                    text = "\n".join(non_table).strip()
                    if text:
                        blocks.append(text)
                    current_block = [stripped]
                else:
                    current_block.append(stripped)
            else:
                current_block.append(stripped)
            in_table = True
        elif stripped:
            if in_table:
                # Leaving table — start new content
                if current_block:
                    text = "\n".join(current_block).strip()
                    if text:
                        blocks.append(text)
                current_block = [stripped]
                in_table = False
            else:
                current_block.append(stripped)
        # Skip empty lines

    if current_block:
        text = "\n".join(current_block).strip()
        if text:
            blocks.append(text)

    return blocks


def _merge_label_blocks(blocks: list[str]) -> list[str]:
    """Merge small blocks that follow section label blocks.

    Handles patterns like:
      Block: "Pardavėjas:"
      Block: "Įmonės kodas 123..."
      Block: "MB Company Name"
    Merges into: "Pardavėjas:\nĮmonės kodas 123...\nMB Company Name"
    """
    if not blocks:
        return blocks

    section_labels = ENTITY_KEYWORDS

    merged: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        block_clean = re.sub(r"[*#:]+", "", block).strip().lower()

        # Check if this is a section label (short block ending with : or matching keyword)
        is_label = block_clean in section_labels or (
            block_clean.rstrip(":") in section_labels
        )

        if is_label:
            # Merge following blocks until next label or table
            combined = [block]
            i += 1
            while i < len(blocks):
                next_block = blocks[i]
                next_clean = re.sub(r"[*#:]+", "", next_block).strip().lower()

                # Stop merging at next section label, table, table header, or keyword block
                first_word = next_clean.split()[0] if next_clean.split() else ""
                is_next_label = (
                    next_clean.rstrip(":") in section_labels
                    or next_block.strip().startswith("|")
                    or first_word in TABLE_HEADER_WORDS
                    or re.match(r"^(?:pvm\s+sąskait|sąskait|invoice|pastab|mokėtina|iš viso|sąskaitą išrašė)", next_clean)
                )
                if is_next_label:
                    break

                combined.append(next_block)
                i += 1

            merged.append("\n".join(combined))
        else:
            merged.append(block)
            i += 1

    return merged


def _score_block(block: str, section: str) -> int:
    """Score how well a block matches a section by keyword hits."""
    text = block.lower()
    return sum(1 for kw in SECTION_KEYWORDS.get(section, []) if kw in text)


def _has_table(block: str) -> bool:
    """Check if block contains a markdown table."""
    lines = block.strip().split("\n")
    pipe_lines = [line for line in lines if "|" in line]
    return len(pipe_lines) >= 2


def _has_iban(block: str) -> bool:
    """Check if block contains an IBAN."""
    return bool(IBAN_RE.search(block))


def _split_multi_column_blocks(blocks: list[str]) -> list[str]:
    """Pre-process blocks to split multi-column seller/buyer blocks.

    Detects patterns like:
      **Seller** **Buyer**
      MB NineBit   Spinal Technologies
      Code: 123   Code: 456
    And splits them into separate seller and buyer blocks.
    Also handles blocks where metadata + seller/buyer are merged.
    """
    result: list[str] = []
    seller_kw = SELLER_KEYWORDS
    buyer_kw = BUYER_KEYWORDS

    for block in blocks:
        lines = block.split("\n")

        # Find the line with both seller and buyer keywords
        split_line_idx = None
        for idx, line in enumerate(lines):
            line_lower = re.sub(r"\*+", "", line).lower().strip()
            has_seller = any(kw in line_lower for kw in seller_kw)
            has_buyer = any(kw in line_lower for kw in buyer_kw)
            if has_seller and has_buyer:
                split_line_idx = idx
                break

        if split_line_idx is not None:
            logger.debug("Multi-column seller/buyer detected at line %d", split_line_idx)
            pre_lines = lines[:split_line_idx]
            if pre_lines:
                pre_text = "\n".join(pre_lines).strip()
                if pre_text:
                    result.append(pre_text)

            seller_lines: list[str] = ["Pardavėjas"]
            buyer_lines: list[str] = ["Pirkėjas"]

            for line in lines[split_line_idx + 1:]:
                clean = re.sub(r"\*+", "", line).strip()
                if not clean:
                    continue
                field_pattern = (
                    r"(?=\b(?:company\s+code|įmonės\s+kodas|vat\s+code|"
                    r"pvm\s+(?:mokėtojo\s+)?kodas|address|adresas)\b)"
                )
                parts = re.split(field_pattern, clean, flags=re.IGNORECASE)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    seller_lines.append(parts[0])
                    buyer_lines.append(" ".join(parts[1:]))
                else:
                    col_parts = re.split(r"\s{2,}", clean)
                    if len(col_parts) >= 2:
                        seller_lines.append(col_parts[0].strip())
                        buyer_lines.append(" ".join(col_parts[1:]).strip())
                    else:
                        seller_lines.append(clean)

            result.append("\n".join(seller_lines))
            result.append("\n".join(buyer_lines))
        else:
            result.append(block)

    return result


def _split_at_second_code(lines: list[str], first_entity_label: str) -> list[str] | None:
    """Split lines at the second company code occurrence.

    Returns [first_entity_block, second_entity_block] or None if can't split.
    """
    first_lines: list[str] = []
    second_lines: list[str] = []
    found_first_code = False
    split_done = False

    for line in lines:
        if split_done:
            second_lines.append(line)
        elif re.search(r"(?:company\s+code|įmonės\s+kodas)", line, re.IGNORECASE):
            if found_first_code:
                # Second code — look back for the company name (line before this)
                if first_lines and not re.search(
                    r"(?:kodas|code|vat|pvm|iban|bank|address|adresas)",
                    first_lines[-1], re.IGNORECASE,
                ):
                    company_name_line = first_lines.pop()
                    second_lines.append(company_name_line)
                second_lines.append(line)
                split_done = True
            else:
                found_first_code = True
                first_lines.append(line)
        else:
            first_lines.append(line)

    if not split_done:
        return None

    return ["\n".join(first_lines), "\n".join(second_lines)]


def _fix_merged_entity_blocks(blocks: list[str]) -> list[str]:
    """Fix Docling output where seller/buyer content is merged into one block.

    Handles two patterns:
    1. Empty '## Seller' followed by '## Buyer' with merged content
    2. Single 'Pardavėjas:' block containing both seller and buyer data (2 company codes)
    """
    result: list[str] = []
    i = 0
    seller_kw = SELLER_KEYWORDS
    buyer_kw = BUYER_KEYWORDS
    all_entity_kw = seller_kw | buyer_kw

    while i < len(blocks):
        block = blocks[i]
        block_clean = re.sub(r"[#*:]+", "", block.split("\n")[0]).strip().lower()

        # Pattern 2: single block with entity keyword + 2 company codes
        if block_clean in all_entity_kw or block_clean.rstrip(":") in all_entity_kw:
            lines = block.split("\n")
            code_count = sum(
                1 for l in lines
                if re.search(r"(?:company\s+code|įmonės\s+kodas)", l, re.IGNORECASE)
            )
            if code_count >= 2:
                # Split at the second company code
                split_result = _split_at_second_code(lines, block_clean)
                if split_result:
                    result.extend(split_result)
                    i += 1
                    logger.debug("Split single merged entity block (2 codes)")
                    continue

        # Pattern 3: orphaned entity data block followed by empty entity header
        # e.g. [company data block] then [Pirkėjas:] — merge data into the header
        if (block_clean not in all_entity_kw
                and block_clean.rstrip(":") not in all_entity_kw
                and i + 1 < len(blocks)):
            next_block = blocks[i + 1]
            next_clean = re.sub(r"[#*:]+", "", next_block.split("\n")[0]).strip().lower()
            if next_clean in all_entity_kw or next_clean.rstrip(":") in all_entity_kw:
                # Check if current block has company codes (it's entity data)
                if re.search(r"(?:company\s+code|įmonės\s+kodas)", block, re.IGNORECASE):
                    # Merge: put the entity header before data
                    merged = next_block.split("\n")[0] + "\n" + block
                    result.append(merged)
                    i += 2  # Skip both blocks
                    logger.debug("Merged orphaned entity data with following header '%s'", next_clean)
                    continue

        # Pattern 1: empty header followed by merged block
        if block_clean in all_entity_kw and i + 1 < len(blocks):
            next_block = blocks[i + 1]
            next_clean = re.sub(r"[#*:]+", "", next_block.split("\n")[0]).strip().lower()

            # Next block is the other entity with merged content?
            is_seller_then_buyer = block_clean in seller_kw and next_clean in buyer_kw
            is_buyer_then_seller = block_clean in buyer_kw and next_clean in seller_kw

            if is_seller_then_buyer or is_buyer_then_seller:
                lines = next_block.split("\n")
                # Count company codes — if >1, content is merged
                code_count = sum(
                    1 for l in lines
                    if re.search(r"(?:company\s+code|įmonės\s+kodas)", l, re.IGNORECASE)
                )
                if code_count >= 2:
                    # Find the split point: second company code
                    first_entity_lines: list[str] = []
                    second_entity_lines: list[str] = [lines[0]]  # Keep ## header
                    found_first_code = False
                    split_done = False
                    for line in lines[1:]:
                        if split_done:
                            second_entity_lines.append(line)
                        elif re.search(r"(?:company\s+code|įmonės\s+kodas)", line, re.IGNORECASE):
                            if found_first_code:
                                # This is the second code — split here
                                # The line before this is likely the second entity name
                                # Move it to second entity
                                if first_entity_lines:
                                    last = first_entity_lines.pop()
                                    if not re.search(r"(?:code|kodas|vat|pvm|iban|bank|address)", last, re.IGNORECASE):
                                        second_entity_lines.append(last)
                                    else:
                                        first_entity_lines.append(last)
                                second_entity_lines.append(line)
                                split_done = True
                            else:
                                found_first_code = True
                                first_entity_lines.append(line)
                        else:
                            first_entity_lines.append(line)

                    if split_done:
                        # Reconstruct: first entity gets the empty header block's role
                        first_header = block.split("\n")[0]
                        result.append(first_header + "\n" + "\n".join(first_entity_lines))
                        result.append("\n".join(second_entity_lines))
                        i += 2
                        logger.debug("Split merged entity block into seller + buyer")
                        continue

        result.append(block)
        i += 1

    return result


def _merge_orphan_entity_data(blocks: list[str]) -> list[str]:
    """Merge orphaned company data blocks with adjacent empty entity headers.

    Handles: [seller block] [Ninebit MB + codes] [Pirkėjas:] [table]
    Merges [Ninebit MB + codes] into [Pirkėjas: Ninebit MB + codes]
    """
    all_kw = ENTITY_KEYWORDS
    result: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        block_first = re.sub(r"[#*:]+", "", block.split("\n")[0]).strip().lower()

        # Check if this is an entity data block (has company codes but not an entity keyword)
        is_entity_header = block_first in all_kw or block_first.rstrip(":") in all_kw
        has_code = bool(re.search(r"(?:company\s+code|įmonės\s+kodas)", block, re.IGNORECASE))

        if not is_entity_header and has_code and i + 1 < len(blocks):
            next_block = blocks[i + 1]
            next_first = re.sub(r"[#*:]+", "", next_block.split("\n")[0]).strip().lower()
            next_is_header = next_first in all_kw or next_first.rstrip(":") in all_kw

            if next_is_header:
                # Merge: put header before data
                header_line = next_block.split("\n")[0]
                merged = header_line + "\n" + block
                result.append(merged)
                i += 2
                logger.debug("Merged orphan entity data with header '%s'", next_first)
                continue

        result.append(block)
        i += 1

    return result


def classify_blocks(blocks: list[str]) -> list[tuple[str, str]]:
    """Classify each block into a section type.

    Returns list of (section_type, block_text) tuples.
    Section types: metadata, seller, buyer, items, totals, payment, notes, document_info, unknown.
    """
    blocks = _fix_merged_entity_blocks(blocks)
    blocks = _merge_orphan_entity_data(blocks)
    blocks = _split_multi_column_blocks(blocks)

    classified: list[tuple[str, str]] = []
    section_order = [
        "metadata", "seller", "buyer", "items",
        "totals", "payment", "notes", "document_info",
    ]

    for block in blocks:
        if _has_table(block):
            classified.append(("items", block))
            continue

        # IBAN detection — but not if block is clearly seller/buyer
        if _has_iban(block):
            block_lower = re.sub(r"\*+", "", block).lower()
            is_entity = any(kw in block_lower for kw in ENTITY_KEYWORDS)
            if not is_entity:
                classified.append(("payment", block))
                continue

        # Score all sections
        scores: dict[str, int] = {}
        for section in section_order:
            score = _score_block(block, section)
            if score > 0:
                scores[section] = score

        if scores:
            best = max(scores, key=lambda s: scores[s])
            classified.append((best, block))
        else:
            classified.append(("unknown", block))

    classified = _resolve_unknowns(classified)
    classified = _merge_consecutive(classified)

    logger.debug("Classified %d blocks: %s",
                 len(classified),
                 [(s, t[:50]) for s, t in classified])

    return classified


def _resolve_unknowns(
    classified: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Assign unknown blocks to the nearest preceding known section."""
    result: list[tuple[str, str]] = []
    last_known = "metadata"

    for section, text in classified:
        if section == "unknown":
            logger.debug("Unknown block assigned to '%s': %s...", last_known, text[:60])
            result.append((last_known, text))
        else:
            last_known = section
            result.append((section, text))

    return result


def _merge_consecutive(
    classified: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Merge consecutive blocks with the same section type."""
    if not classified:
        return classified

    merged: list[tuple[str, str]] = []
    current_section, current_text = classified[0]

    for section, text in classified[1:]:
        if section == current_section:
            current_text += "\n\n" + text
        else:
            merged.append((current_section, current_text))
            current_section = section
            current_text = text

    merged.append((current_section, current_text))
    return merged
