"""
文档解析服务：将上传的 PDF / DOCX / TXT 文件提取为纯文本，并尽量保留结构。
"""

import io
import re
from pathlib import Path

from app.schemas.knowledge import DocumentParseResponse, DocumentSection

_TITLE_RE = re.compile(r"^\s*((\d+(\.\d+)*)|[一二三四五六七八九十]+[、.])?\s*[\u4e00-\u9fffA-Za-z][^。！？!?]{0,80}$")


def parse_bytes(filename: str, content: bytes) -> DocumentParseResponse:
    """
    根据文件名后缀选择对应的解析器，返回结构化文档。
    """
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        text, sections = _parse_pdf(content)
    elif suffix == ".docx":
        text, sections = _parse_docx(content)
    else:
        text = _decode_text(content, filename)
        sections = _build_sections_from_text(text)

    cleaned_text = _normalize_text(text)
    normalized_sections = _normalize_sections(sections, cleaned_text)

    return DocumentParseResponse(
        filename=filename,
        filetype=suffix.lstrip("."),
        text=cleaned_text,
        char_count=len(cleaned_text),
        preview=cleaned_text[:200],
        sections=normalized_sections,
    )


def _decode_text(content: bytes, filename: str) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码文件：{filename}")


def _parse_pdf(content: bytes) -> tuple[str, list[DocumentSection]]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise ImportError("请先安装 pymupdf：pip install pymupdf") from exc

    page_texts: list[str] = []
    sections: list[DocumentSection] = []
    paragraph_counter = 0

    with fitz.open(stream=content, filetype="pdf") as doc:
        for page_index, page in enumerate(doc, start=1):
            page_text = _normalize_text(page.get_text("text"))
            if not page_text.strip():
                continue

            page_texts.append(f"[Page {page_index}]\n{page_text}")
            for block in _split_structured_blocks(page_text):
                paragraph_counter += 1
                title = _guess_section_title(block)
                sections.append(
                    DocumentSection(
                        id=f"pdf-p{page_index}-s{paragraph_counter}",
                        order=len(sections),
                        title=title,
                        heading_path=[title] if title else [],
                        page_start=page_index,
                        page_end=page_index,
                        paragraph_start=paragraph_counter,
                        paragraph_end=paragraph_counter,
                        text=block,
                    )
                )

    return "\n\n".join(page_texts), sections


def _parse_docx(content: bytes) -> tuple[str, list[DocumentSection]]:
    try:
        from docx import Document
    except ImportError as exc:
        raise ImportError("请先安装 python-docx：pip install python-docx") from exc

    doc = Document(io.BytesIO(content))
    paragraphs = [_normalize_text(para.text) for para in doc.paragraphs if para.text.strip()]
    text = "\n\n".join(paragraphs)
    sections = _build_sections_from_text(text)
    return text, sections


def _build_sections_from_text(text: str) -> list[DocumentSection]:
    sections: list[DocumentSection] = []
    heading_stack: list[str] = []
    paragraph_counter = 0

    for block in _split_structured_blocks(text):
        paragraph_counter += 1
        title = _guess_section_title(block)
        if title:
            heading_stack = [title]

        sections.append(
            DocumentSection(
                id=f"sec-{paragraph_counter}",
                order=len(sections),
                title=title,
                heading_path=list(heading_stack),
                paragraph_start=paragraph_counter,
                paragraph_end=paragraph_counter,
                text=block,
            )
        )

    return sections


def _split_structured_blocks(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized.strip():
        return []

    raw_blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    blocks: list[str] = []
    buffer: list[str] = []

    for block in raw_blocks:
        if _is_title_like(block):
            if buffer:
                blocks.append("\n".join(buffer).strip())
                buffer = []
            blocks.append(block)
            continue

        if len(block) < 80 and buffer:
            buffer.append(block)
            continue

        if buffer:
            blocks.append("\n".join(buffer).strip())
            buffer = []
        buffer.append(block)

    if buffer:
        blocks.append("\n".join(buffer).strip())

    return [item for item in blocks if item.strip()]


def _normalize_sections(
    sections: list[DocumentSection],
    fallback_text: str,
) -> list[DocumentSection]:
    normalized = [section for section in sections if section.text.strip()]
    if normalized:
        return [
            section.model_copy(
                update={
                    "order": index,
                    "text": _normalize_text(section.text),
                    "title": _normalize_text(section.title),
                    "heading_path": [_normalize_text(item) for item in section.heading_path if _normalize_text(item)],
                }
            )
            for index, section in enumerate(normalized)
        ]

    if fallback_text.strip():
        return [
            DocumentSection(
                id="sec-1",
                order=0,
                title="",
                heading_path=[],
                paragraph_start=1,
                paragraph_end=1,
                text=fallback_text,
            )
        ]

    return []


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _is_title_like(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped and len(stripped) <= 100 and "\n" not in stripped and _TITLE_RE.match(stripped))


def _guess_section_title(text: str) -> str:
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if _is_title_like(first_line):
        return first_line
    return ""
