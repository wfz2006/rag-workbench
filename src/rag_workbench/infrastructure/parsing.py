import io
import re
import unicodedata
from pathlib import PurePath

from pypdf import PdfReader

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Section

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def safe_display_name(name: str) -> str:
    # Neither POSIX nor Windows input paths are used as storage paths.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    if not name or len(name) > 240:
        raise AppError("invalid_name", "文件名为空或过长，请重命名后上传。")
    return name


def parse_document(name: str, data: bytes, settings: Settings) -> list[Section]:
    extension = PurePath(safe_display_name(name)).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AppError("unsupported_type", "仅支持 TXT、Markdown 和可提取文本的 PDF。")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AppError("file_too_large", f"文件大小超过 {settings.max_upload_mb} MB 限制。")
    if not data:
        raise AppError("empty_document", "文件为空。")
    if extension == ".pdf":
        sections = _parse_pdf(data, settings)
    else:
        if data.startswith((b"%PDF", b"PK\x03\x04", b"MZ", b"\x89PNG")):
            raise AppError("invalid_content", "文件内容与文本格式不符，请检查实际文件类型。")
        text = None
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            encodings = ("utf-16",)
        elif b"\x00" in data:
            # BOM-less UTF-16 is accepted only with a strong ASCII-lane signature.
            even, odd = data[::2], data[1::2]
            if len(data) % 2 or not even:
                raise AppError("invalid_encoding", "无法识别文本编码，请另存为 UTF-8。")
            if odd.count(0) / len(odd) >= 0.2 and even.count(0) / len(even) < 0.05:
                encodings = ("utf-16-le",)
            elif even.count(0) / len(even) >= 0.2 and odd.count(0) / len(odd) < 0.05:
                encodings = ("utf-16-be",)
            else:
                raise AppError("invalid_content", "包含无法确定编码的空字节，请另存为 UTF-8。")
        else:
            encodings = ("utf-8-sig", "gb18030")
        for encoding in encodings:
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise AppError("invalid_encoding", "无法识别文本编码，请另存为 UTF-8。")
        controls = sum(unicodedata.category(c) == "Cc" and c not in "\r\n\t" for c in text)
        if controls > 2 and controls / max(len(text), 1) > 0.05:
            raise AppError("invalid_content", "文件含过多二进制控制字符，不是有效文本。")
        sections = [Section(text=clean_text(text))]
    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise AppError("empty_document", "文档没有可索引的文本。")
    if sum(len(s.text) for s in sections) > settings.max_text_chars:
        raise AppError("text_too_large", "提取文本超过安全上限，请拆分文件后上传。")
    return sections


def _parse_pdf(data: bytes, settings: Settings) -> list[Section]:
    if not data.startswith(b"%PDF-"):
        raise AppError("invalid_pdf", "内容不是有效的 PDF 文件。")
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise AppError("encrypted_pdf", "暂不支持加密 PDF，请解密后上传。")
        if len(reader.pages) > settings.max_pdf_pages:
            raise AppError("too_many_pages", "PDF 页数超过限制，请拆分后上传。")
        sections: list[Section] = []
        total = 0
        for i, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 20 * 1024 * 1024:
                raise AppError("pdf_too_complex", "PDF 页面解压后过大，请简化后上传。")
            text = clean_text(page.extract_text() or "")
            total += len(text)
            if total > settings.max_text_chars:
                raise AppError("text_too_large", "PDF 文本超过上限，请拆分后上传。")
            sections.append(Section(text=text, page=i))
        if not any(s.text for s in sections):
            raise AppError("ocr_required", "PDF 无可提取文本，可能是扫描件；暂不支持 OCR。")
        return sections
    except AppError:
        raise
    except Exception as exc:
        raise AppError("invalid_pdf", "PDF 无法解析，可能已损坏，请重新导出。") from exc
