"""Parse source blocks without rendering, following links or executing content."""

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.knowledge.chunks import BlockType, Heading

NAVIGATION_HEADINGS = {
    "toc",
    "contents",
    "table of contents",
    "navigation",
    "spis treści",
    "nawigacja",
}
OmissionReason = Literal[
    "structural_heading",
    "navigation_section",
    "anchor_navigation",
    "html_comment",
    "thematic_break",
]


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    block_type: BlockType
    heading_path: tuple[Heading, ...]


@dataclass(frozen=True)
class Omission:
    start: int
    end: int
    reason: OmissionReason


def line_offsets(text: str) -> list[int]:
    return [0, *(match.end() for match in re.finditer("\n", text))]


def heading_title(tokens: list[Token]) -> str:
    pieces = []
    for token in tokens:
        if token.type in {"text", "code_inline", "image"}:
            pieces.append(token.content)
        elif token.type in {"softbreak", "hardbreak"}:
            pieces.append(" ")
    title = " ".join("".join(pieces).split()) or "(untitled)"
    if len(title) > 200:
        raise CorpusError("heading_title_too_large")
    return title


def anchor_navigation(tokens: list[Token]) -> bool:
    """Only blocks consisting of same-document anchor links and punctuation."""
    found = False
    for token in tokens:
        if token.type != "inline":
            continue
        depth = 0
        for child in token.children or []:
            if child.type == "link_open":
                href = child.attrGet("href") or ""
                if not isinstance(href, str) or not href.startswith("#"):
                    return False
                depth += 1
                found = True
            elif child.type == "link_close":
                depth -= 1
            elif depth:
                if child.type == "image":
                    return False
            elif child.type in {"softbreak", "hardbreak"}:
                continue
            elif child.type != "text" or child.content.strip(" \t\n|.,:;·•←→–—-"):
                return False
    return found


def parse_blocks(text: str) -> tuple[list[Block], list[Omission]]:
    parser = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False})
    parser.enable("table")
    tokens = parser.parse(text)
    roots = [
        i
        for i, token in enumerate(tokens)
        if token.level == 0 and token.block and token.map is not None and token.nesting >= 0
    ]
    offsets = line_offsets(text)
    blocks: list[Block] = []
    omitted: list[Omission] = []
    headings: list[Heading] = []
    navigation_level: int | None = None
    previous_end = 0
    kinds: dict[str, BlockType] = {
        "paragraph_open": "paragraph",
        "bullet_list_open": "bullet_list",
        "ordered_list_open": "ordered_list",
        "blockquote_open": "blockquote",
        "fence": "fence",
        "code_block": "code_block",
        "table_open": "table",
        "html_block": "html_block",
    }

    def gap(start: int, end: int) -> None:
        if start < end and text[start:end].strip():
            if navigation_level is not None:
                omitted.append(Omission(start, end, "navigation_section"))
            else:
                blocks.append(Block(start, end, "reference_definition", tuple(headings)))

    for position, index in enumerate(roots):
        token = tokens[index]
        assert token.map is not None  # noqa: S101 - selected above
        first, last = token.map
        start = offsets[first]
        end = offsets[last] if last < len(offsets) else len(text)
        gap(previous_end, start)
        previous_end = end
        next_index = roots[position + 1] if position + 1 < len(roots) else len(tokens)
        subtree = tokens[index:next_index]
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = next(t for t in subtree if t.type == "inline")
            title = heading_title(inline.children or [])
            headings = [h for h in headings if h.level < level]
            headings.append(Heading(level=level, title=title))
            if navigation_level is not None and level <= navigation_level:
                navigation_level = None
            if title.casefold() in NAVIGATION_HEADINGS and navigation_level is None:
                navigation_level = level
            omitted.append(
                Omission(
                    start,
                    end,
                    "navigation_section" if navigation_level is not None else "structural_heading",
                )
            )
        elif navigation_level is not None:
            omitted.append(Omission(start, end, "navigation_section"))
        elif token.type == "hr":
            omitted.append(Omission(start, end, "thematic_break"))
        elif token.type == "html_block" and re.fullmatch(
            r"(?:\s*<!--[\s\S]*?-->\s*)+", text[start:end]
        ):
            omitted.append(Omission(start, end, "html_comment"))
        elif token.type in {"paragraph_open", "bullet_list_open", "ordered_list_open"} and (
            anchor_navigation(subtree)
        ):
            omitted.append(Omission(start, end, "anchor_navigation"))
        elif token.type in kinds:
            blocks.append(Block(start, end, kinds[token.type], tuple(headings)))
        else:
            raise CorpusError("unsupported_markdown_block")
    gap(previous_end, len(text))
    return blocks, omitted


def bounded_pieces(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """Prefer line/space boundaries and never split an encoded code point."""
    sizes = [0]
    for char in text[start:end]:
        sizes.append(sizes[-1] + len(char.encode("utf-8")))
    pieces = []
    cursor = start
    while cursor < end:
        cap = start + bisect_right(sizes, sizes[cursor - start] + limit) - 1
        boundary = min(end, cap)
        if boundary < end:
            newline = text.rfind("\n", cursor, boundary)
            space = text.rfind(" ", cursor, boundary)
            if newline >= cursor:
                boundary = newline + 1
            elif space >= cursor:
                boundary = space + 1
        trimmed_start, trimmed_end = cursor, boundary
        while trimmed_start < trimmed_end and text[trimmed_start] == "\n":
            trimmed_start += 1
        while trimmed_end > trimmed_start and text[trimmed_end - 1] == "\n":
            trimmed_end -= 1
        if text[trimmed_start:trimmed_end].strip():
            pieces.append((trimmed_start, trimmed_end))
        cursor = boundary
    return pieces
