import io
import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import PurePath, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.domain.models import Section

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx", ".epub", ".html", ".htm"}
UPLOAD_EXTENSIONS = tuple(sorted(extension.lstrip(".") for extension in ALLOWED_EXTENSIONS))
SUPPORTED_FORMATS_LABEL = "TXT、Markdown、PDF、DOCX、EPUB、HTML"
TEXT_EXTENSIONS = {".txt", ".md"}
HTML_EXTENSIONS = {".html", ".htm"}

ZIP_MAX_ENTRIES = 20_000
ZIP_MAX_UNCOMPRESSED = 200 * 1024 * 1024
XML_MAX_BYTES = 64 * 1024 * 1024
OPF_MAX_BYTES = 4 * 1024 * 1024
NAV_PROPERTIES = {"nav"}
XHTML_TYPES = {"application/xhtml+xml", "text/html"}


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
        raise AppError("unsupported_type", f"仅支持 {SUPPORTED_FORMATS_LABEL}。")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise AppError("file_too_large", f"文件大小超过 {settings.max_upload_mb} MB 限制。")
    if not data:
        raise AppError("empty_document", "文件为空。")
    if extension == ".pdf":
        sections = _parse_pdf(data, settings)
    elif extension in TEXT_EXTENSIONS:
        sections = _parse_plain_text(data)
    elif extension == ".docx":
        sections = _parse_docx(data)
    elif extension == ".epub":
        sections = _parse_epub(data)
    else:
        sections = _parse_html(data)
    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise AppError("empty_document", "文档没有可索引的文本。")
    if sum(len(s.text) for s in sections) > settings.max_text_chars:
        raise AppError("text_too_large", "提取文本超过安全上限，请拆分文件后上传。")
    return sections


def _parse_plain_text(data: bytes) -> list[Section]:
    if data.startswith((b"%PDF", b"PK\x03\x04", b"MZ", b"\x89PNG")):
        raise AppError("invalid_content", "文件内容与文本格式不符，请检查实际文件类型。")
    return [Section(text=clean_text(_decode_text(data)))]


def _decode_text(data: bytes) -> str:
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
    text = None
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
    return text


@contextmanager
def _open_package(data: bytes, code: str) -> Iterator[zipfile.ZipFile]:
    if not data.startswith(b"PK\x03\x04"):
        raise AppError(code, "文件不是有效的 ZIP 容器，请检查实际文件类型。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise AppError(code, "ZIP 容器无法解析，可能已损坏。") from exc
    try:
        infos = archive.infolist()
        if len(infos) > ZIP_MAX_ENTRIES:
            raise AppError("package_too_complex", "压缩包条目过多，请拆分后上传。")
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (
                not name
                or name.startswith("/")
                or ".." in parts
                or re.match(r"^[A-Za-z]:", name)
            ):
                raise AppError("unsafe_package", "压缩包包含不安全路径，已拒绝。")
            if info.flag_bits & 0x1:
                raise AppError("encrypted_package", "暂不支持加密的压缩包。")
            total += info.file_size
            if total > ZIP_MAX_UNCOMPRESSED:
                raise AppError("package_too_complex", "压缩包解压后过大，请拆分后上传。")
        yield archive
    finally:
        archive.close()


def _read_member(
    archive: zipfile.ZipFile, name: str, code: str, *, max_bytes: int
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise AppError(code, f"压缩包缺少必要文件：{name}") from exc
    if info.file_size > max_bytes:
        raise AppError("text_too_large", "压缩包内文件过大，请拆分后上传。")
    try:
        data = archive.read(name)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise AppError(code, "压缩包内文件无法读取，可能已损坏。") from exc
    if len(data) > max_bytes:
        raise AppError("text_too_large", "压缩包内文件过大，请拆分后上传。")
    return data


def _xml_root(data: bytes, code: str) -> ET.Element:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise AppError(code, "XML 包含不支持的文档类型或实体声明。")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise AppError(code, "XML 结构无法解析，可能已损坏。") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_docx(data: bytes) -> list[Section]:
    with _open_package(data, "invalid_docx") as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise AppError("invalid_docx", "DOCX 缺少 word/document.xml，可能不是 Word 文档。")
        root = _xml_root(
            _read_member(archive, "word/document.xml", "invalid_docx", max_bytes=XML_MAX_BYTES),
            "invalid_docx",
        )
    body = next((child for child in root if _local(child.tag) == "body"), None)
    if body is None:
        raise AppError("invalid_docx", "DOCX 缺少正文结构。")
    blocks = list(_docx_blocks(body))
    text = clean_text("\n\n".join(blocks))
    return [Section(text=text)] if text else []


def _docx_blocks(container: ET.Element):
    for child in container:
        name = _local(child.tag)
        if name == "p":
            text = _docx_paragraph(child)
            if text:
                yield text
        elif name == "tbl":
            yield from _docx_table(child)
        elif name in {"sdt", "sdtContent", "customXml", "ins"}:
            yield from _docx_blocks(child)


def _docx_paragraph(paragraph: ET.Element) -> str:
    style = ""
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    for node in paragraph.iter():
        if _local(node.tag) == "pStyle":
            style = (node.attrib.get(f"{{{word_ns}}}val") or "").strip()
            if not style:
                style = (node.attrib.get("val") or "").strip()
            break
    parts: list[str] = []
    for node in paragraph.iter():
        name = _local(node.tag)
        if name == "t":
            parts.append(node.text or "")
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    text = "".join(parts).strip()
    if not text:
        return ""
    heading = re.fullmatch(r"(?:heading|标题)\s*([1-9])", style, flags=re.I)
    if heading:
        return f"{'#' * int(heading.group(1))} {text}"
    if any(_local(node.tag) == "numPr" for node in paragraph.iter()):
        return f"- {text}"
    return text


def _docx_table(table: ET.Element) -> list[str]:
    rows: list[str] = []
    for row in table:
        if _local(row.tag) != "tr":
            continue
        cells = []
        for cell in row:
            if _local(cell.tag) != "tc":
                continue
            cells.append(" ".join(_docx_blocks(cell)))
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _parse_epub(data: bytes) -> list[Section]:
    with _open_package(data, "invalid_epub") as archive:
        names = set(archive.namelist())
        if "META-INF/container.xml" not in names:
            raise AppError("invalid_epub", "EPUB 缺少 META-INF/container.xml。")
        container = _xml_root(
            _read_member(
                archive,
                "META-INF/container.xml",
                "invalid_epub",
                max_bytes=OPF_MAX_BYTES,
            ),
            "invalid_epub",
        )
        rootfile = next(
            (
                element.attrib.get("full-path", "").strip()
                for element in container.iter()
                if _local(element.tag) == "rootfile"
            ),
            "",
        )
        if not rootfile or rootfile not in names:
            raise AppError("invalid_epub", "EPUB 的 OPF 清单缺失或路径无效。")
        opf = _xml_root(
            _read_member(archive, rootfile, "invalid_epub", max_bytes=OPF_MAX_BYTES),
            "invalid_epub",
        )
        base = posixpath.dirname(rootfile)
        manifest: dict[str, dict[str, str]] = {}
        for element in opf.iter():
            if _local(element.tag) != "item":
                continue
            identity = (element.attrib.get("id") or "").strip()
            href = (element.attrib.get("href") or "").strip()
            if identity and href:
                manifest[identity] = {
                    "href": href,
                    "media_type": (element.attrib.get("media-type") or "").strip().lower(),
                    "properties": (element.attrib.get("properties") or "").lower(),
                }
        spine = [
            (element.attrib.get("idref") or "").strip()
            for element in opf.iter()
            if _local(element.tag) == "itemref"
        ]
        sections: list[Section] = []
        for idref in spine:
            item = manifest.get(idref)
            if item is None:
                continue
            if set(item["properties"].split()) & NAV_PROPERTIES:
                continue
            if item["media_type"] not in XHTML_TYPES:
                continue
            path = _resolve_package_path(base, item["href"])
            if path is None or path not in names:
                continue
            raw = _read_member(archive, path, "invalid_epub", max_bytes=XML_MAX_BYTES)
            text = _html_text(_decode_text(raw))
            if text.strip():
                sections.append(Section(text=clean_text(text)))
    if not sections:
        raise AppError("empty_document", "EPUB 没有可索引的正文。")
    return sections


def _resolve_package_path(base: str, href: str) -> str | None:
    cleaned = unquote(href.split("#", 1)[0]).replace("\\", "/").strip()
    if not cleaned or "\x00" in cleaned:
        return None
    path = posixpath.normpath(posixpath.join(base, cleaned))
    parts = PurePosixPath(path).parts
    if path.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", path):
        return None
    return path


def _parse_html(data: bytes) -> list[Section]:
    if data.startswith((b"%PDF", b"PK\x03\x04", b"MZ", b"\x89PNG")):
        raise AppError("invalid_content", "文件内容与 HTML 格式不符，请检查实际文件类型。")
    text = _decode_text(data)
    if "<" not in text:
        raise AppError("invalid_content", "文件不是有效的 HTML 文本。")
    cleaned = clean_text(_html_text(text))
    return [Section(text=cleaned)] if cleaned else []


class _HTMLTextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "nav", "aside", "footer"}
    BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "ul",
        "ol",
        "table",
        "tr",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "header",
        "body",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.pre = 0
        self.heading: int | None = None
        self.row_cells = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in {"pre", "code"}:
            self.pre += 1
        heading = re.fullmatch(r"h([1-6])", tag)
        if heading:
            self.heading = int(heading.group(1))
            self.parts.append("\n\n" + "#" * self.heading + " ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            if self.row_cells:
                self.parts.append(" | ")
            self.row_cells += 1
        elif tag == "li":
            self.parts.append("- ")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in {"pre", "code"}:
            self.pre = max(0, self.pre - 1)
        if self.heading is not None:
            self.parts.append("\n")
            self.heading = None
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "tr":
            self.row_cells = 0

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_text(data: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(data)
    parser.close()
    return parser.text()


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
