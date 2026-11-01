"""Full-document parsing for material extraction, independent of RAG retrieval."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.materials import EvidenceBlock
from rag_workbench.infrastructure.parsing import parse_document, safe_display_name


@dataclass
class ParsedLiterature:
    parser: str
    blocks: list[EvidenceBlock]
    parsed_json: str | None = None


def parse_literature(
    name: str, data: bytes, settings: Settings, parser_name: str = "text"
) -> ParsedLiterature:
    name = safe_display_name(name)
    extension = Path(name).suffix.lower()
    if extension not in {".pdf", ".txt", ".md"}:
        raise AppError("material_file_type", "材料文献首版支持 PDF、TXT 和 Markdown。")
    if not data or len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AppError("material_file_size", "文件为空或超过上传大小上限。")
    doc_id = hashlib.sha256(data).hexdigest()
    if parser_name == "text":
        sections = parse_document(name, data, settings)
        blocks = []
        for section in sections:
            # Fixed source segments: the model batching never changes persisted source IDs.
            for offset in range(0, len(section.text), 12000):
                text = section.text[offset:offset + 12000]
                blocks.append(_block(doc_id, len(blocks), text, section.page))
        return ParsedLiterature("text/pypdf" if extension == ".pdf" else "text/plain", blocks)
    if parser_name != "docling" or extension != ".pdf":
        raise AppError("material_parser", "Docling 结构化解析仅用于 PDF，请选择正确解析方式。")
    if not data.startswith(b"%PDF-"):
        raise AppError("invalid_pdf", "内容不是有效的 PDF 文件。")
    # Check page count / encryption before expensive model-backed conversion; scans are allowed.
    from io import BytesIO

    from pypdf import PdfReader

    try:
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted or len(pdf.pages) > settings.max_pdf_pages:
            raise AppError("material_pdf_limits", "PDF 已加密或超过页数上限。")
    except AppError:
        raise
    except Exception as exc:
        raise AppError("invalid_pdf", "PDF 无法读取。") from exc
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise AppError(
            "docling_not_installed",
            "尚未安装可选 Docling 解析器。可先使用文本层解析；"
            "需要 OCR/结构化表格时，按材料模块说明配置可选解析组件。",
        ) from exc
    try:
        with TemporaryDirectory(prefix="rag-material-") as folder:
            path = Path(folder) / "paper.pdf"
            path.write_bytes(data)
            result = DocumentConverter().convert(path)
        document = result.document
        blocks = []
        total = 0
        for item, _level in document.iterate_items():
            kind = "table" if hasattr(item, "export_to_dataframe") else "text"
            text = (
                item.export_to_markdown(doc=document)
                if kind == "table" else getattr(item, "text", "")
            )
            if not text or not text.strip():
                continue
            total += len(text)
            if total > settings.max_text_chars:
                raise AppError("text_too_large", "文献提取文本超过上限，请拆分文件。")
            provenance = getattr(item, "prov", [])
            page = provenance[0].page_no if provenance else None
            bbox = _bounding_box(document, provenance[0]) if provenance else None
            blocks.append(_block(doc_id, len(blocks), text, page, kind, bbox))
        if not blocks:
            raise AppError("empty_document", "文献未提取到可核对的文本或表格。")
        return ParsedLiterature("docling", blocks, json.dumps(document.export_to_dict()))
    except AppError:
        raise
    except Exception as exc:
        raise AppError("docling_failed", "结构化解析失败，请检查解析环境和文献格式。") from exc


def _block(doc_id, index, text, page, kind="text", bbox=None):
    block_id = hashlib.sha256(f"{doc_id}:{index}:{text}".encode()).hexdigest()
    return EvidenceBlock(
        id=block_id, document_id=doc_id, text=text, page=page, kind=kind, bbox=bbox,
    )


def _bounding_box(document, provenance):
    box = getattr(provenance, "bbox", None)
    page = document.pages.get(provenance.page_no)
    if box is None or page is None:
        return None
    width, height = page.size.width, page.size.height
    if not width or not height:
        return None
    origin = str(getattr(box.coord_origin, "value", box.coord_origin)).upper()
    top, bottom = box.t, box.b
    if "BOTTOM" in origin:
        top, bottom = height - box.t, height - box.b
    left, right = sorted((box.l / width, box.r / width))
    top, bottom = sorted((top / height, bottom / height))
    if not all(0 <= value <= 1 for value in (left, top, right, bottom)):
        return None
    return left, top, right, bottom
