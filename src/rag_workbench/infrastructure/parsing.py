import io
import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import PurePath, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from pypdf import PdfReader, mult
from pypdf.errors import PdfReadError
from pypdf.generic import DictionaryObject, NameObject
from pypdf.generic._font import Font

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
_PDF_LAYOUT = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    "\u00ad": None, "\u200b": None, "\ufeff": None, "\u00a0": " ", "\u202f": " ",
})
_WRAPPED_HYPHEN = re.compile(r"(?<=[A-Za-z0-9])([-‐‑])[ \t]*\n[ \t]*(?=[A-Za-z])")
_WRAPPED_WORD = re.compile(r"\b([a-z]{2,})-[ \t]*\n[ \t]*([a-z]{2,})\b")
_PUBLISHER_FOOTER = re.compile(
    r"^[ \t]*Downloaded from (?:https?://)?pubs\.acs\.org/\S+[ \t]+by [^\n]+(?:\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_SUPERSCRIPT = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻"
_SUPERSCRIPT_TO_TEXT = str.maketrans(_SUPERSCRIPT, "0123456789+-")
_TEXT_TO_SUPERSCRIPT = str.maketrans("0123456789+-", _SUPERSCRIPT)
# Verified embedded symbol subsets with incomplete Unicode maps. Glyph names such as /C0
# are private to their font, so another embedded program may assign a different shape.
_ADV_SYMBOLS = {"/C0": "−", "/C2": "×", "/C14": "°"}
_PDF_FONT_REPAIRS = {
    "2eb4cfa6a65188b0a7c708fc914e8eecc69f5bc5dee5171dd48a57dc3aafc056": {
        **_ADV_SYMBOLS, "/C24": "∼",
    },
    "227dc13bdd9969aa68e11e4f409096d7d4d245698545c284f392a27c9b3b0777": _ADV_SYMBOLS,
    "86c9d0a613a68f7e117022d28a9079cec826c71946cf60048c48537d981de504": _ADV_SYMBOLS,
    "5b9f83464b18607917467839357192496eb59c4141f71293580970140861e9d8": _ADV_SYMBOLS,
    "bae22a4dea0dec7219942f05cbfdfae3f06689fe87d6bc45a720a7d932603f52": {
        **_ADV_SYMBOLS, "¼": "=",
    },
    "a8e612262b443fd94b0c84dd2ab3ce3612d796f3982f3c360e7ea275f7cc5cd1": {
        **_ADV_SYMBOLS, "¼": "=", "½": "[", "ð": "(", "Þ": ")", "/C138": "]",
    },
    "664aadd3d3b8ee944f467e1e367448176f07131b0aa78e3e705a261cd5e85544": {"/C176": "°"},
    "628f5d9e5ef162359d2558496cd473c5337879891619f3ca6d8c55a7e6bdfa06": {"/C176": "°"},
    "497e4abef7d065a39af0a3d8a826ae86c4f655d0725ad5e5c94655f9630c220f": {"/C19": "\u0301"},
    "3431f50fb3da954139672e7593952665492394ed48efa5b076d76391e84c23c7": {"/C19": "\u0301"},
    "14995c9a71db1375c023f9174948fcc594575e090776e49ec64634c82cbcccc0": {
        "/C18": "(", "/C19": ")",
    },
    "9c950919c4f3ab7f9ffbf1e2ad6f74d24162e3852a86013ea623cb06b588a940": {
        "/C12": "β", "/C18": "θ", "/C22": "μ",
    },
    # pypdf emits three copies of the publisher's double-bond shorthand as QQQ.
    "b2102c225ec99df53c198515413b18257f1e6cd3de8c901bb31db8df4b6b697b": {"QQQ": "Q"},
}
_REPAIRABLE_FONT = re.compile(r"Adv(?:P4C4E(?:74|59|46)|symbol|MTSYan|MTMI|Els-ent6)$")
_ESCAPED_GLYPH = re.compile(r"/C[0-9]+")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf_layout(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\u00ad[ \t]*\n[ \t]*", "", text)
    text = text.translate(_PDF_LAYOUT)
    if _WRAPPED_WORD.search(text):
        words = set(re.findall(r"\b[A-Za-z]+\b", text.casefold()))

        def join_if_attested(match: re.Match[str]) -> str:
            joined = match[1] + match[2]
            return joined if joined in words else match[0]

        text = _WRAPPED_WORD.sub(join_if_attested, text)
    return _WRAPPED_HYPHEN.sub(r"\1", text)


def clean_pdf_text(text: str) -> str:
    text = _PUBLISHER_FOOTER.sub("", _pdf_layout(text))
    return clean_text(re.sub(r"(?m)^[ \t]+$", "", text))


def comparable_pdf_text(text: str) -> str:
    """Fold reversible typography for evidence matching without merging numeric tokens."""
    text = unicodedata.normalize("NFC", _pdf_layout(text)).replace("−", "-")
    text = re.sub(
        rf"(?<=10)([{_SUPERSCRIPT}]+)",
        lambda match: "^" + match[1].translate(_SUPERSCRIPT_TO_TEXT),
        text,
    )
    # Canonicalize known mobility spellings while keeping the base unit and the
    # scale. Generic NFKC would erase the difference between 10⁴ and 104.
    text = re.sub(
        r"(?<!\w)(cm|m)(?:[²2]|\^2)\s*[·⋅]?\s*"
        r"V(?:⁻¹|\^?-1)\s*[·⋅]?\s*s(?:⁻¹|\^?-1)(?!\w)",
        r"\g<1>2 V-1 s-1",
        text,
    )
    text = re.sub(
        r"(?<!\w)(cm|m)(?:[²2]|\^2)\s*/\s*(?:V\s*[·⋅]?\s*s|\(V\s*[·⋅]?\s*s\))(?!\w)",
        r"\g<1>2/Vs",
        text,
    )
    return " ".join(text.split())


def quote_in_source(quote: str, text: str) -> bool:
    if not quote.strip():
        return False
    excerpt = comparable_pdf_text(quote)
    return bool(excerpt) and excerpt in comparable_pdf_text(text)


def _font_repairs(font, repairs: dict[int, dict[str, str]]) -> dict[str, str]:
    if font is None or not _REPAIRABLE_FONT.search(str(font.get("/BaseFont"))):
        return {}
    identity = id(font)
    if identity not in repairs:
        descriptor = font.get("/FontDescriptor")
        embedded = descriptor.get_object().get("/FontFile3") if descriptor is not None else None
        data = embedded.get_object().get_data() if embedded is not None else b""
        repairs[identity] = _PDF_FONT_REPAIRS.get(sha256(data).hexdigest(), {})
    return repairs[identity]


def _repair_visitor_span(text: str, repairs: dict[str, str]) -> str:
    if replacement := repairs.get(text.strip()):
        return text.replace(text.strip(), replacement, 1)
    if not repairs:
        return text
    # In a merged visitor span a digit may be either a glyph name suffix or normal text.
    # Replace only whole names here. Raw operands can disambiguate more in _PdfTextRuns.
    text = _ESCAPED_GLYPH.sub(lambda match: repairs.get(match[0], match[0]), text)
    return "".join(repairs.get(char, char) for char in text)


@dataclass(frozen=True, slots=True)
class _PdfTextRun:
    text: str
    repairs: dict[str, str]
    x: float
    y: float
    height: float
    width: float


class _PdfTextRuns:
    """Read the positions pypdf sees before it coalesces PDF text operations."""

    def __init__(self, page, repairs: dict[int, dict[str, str]]):
        resources = page.get("/Resources")
        resources = resources.get_object() if resources is not None else None
        fonts = resources.get("/Font") if isinstance(resources, DictionaryObject) else None
        self.fonts = fonts.get_object() if fonts is not None else {}
        self.repairs = repairs
        self.decoded_fonts: dict[str, Font] = {}
        self.font = None
        self.size = self.rise = 0.0
        self.char_spacing = self.word_spacing = 0.0
        self.horizontal_scale = 1.0
        self.tm = None
        self.advance = 0.0
        self.stack = []
        self.runs: list[_PdfTextRun] = []
        self.disabled = False

    def visit(self, operator, args, cm, tm):
        if self.disabled:
            return
        if operator == b"Do" and args:
            # The after-visitor is called for nested forms too. Their unrecognised
            # text will fail the final alignment, which disables geometry for the page.
            self.tm = None
            return
        if operator == b"q":
            self.stack.append((
                self.font, self.size, self.rise,
                self.char_spacing, self.word_spacing, self.horizontal_scale,
            ))
        elif operator == b"Q" and self.stack:
            (
                self.font, self.size, self.rise,
                self.char_spacing, self.word_spacing, self.horizontal_scale,
            ) = self.stack.pop()
        elif operator == b"Tf" and len(args) >= 2:
            self.font, self.size = args[0], float(args[1])
        elif operator == b"Ts" and args:
            self.rise = float(args[0])
        elif operator == b"Tc" and args:
            self.char_spacing = float(args[0])
        elif operator == b"Tw" and args:
            self.word_spacing = float(args[0])
        elif operator == b"Tz" and args:
            self.horizontal_scale = float(args[0]) / 100
        elif operator in {b"Tm", b"Td", b"TD", b"T*", b"BT", b"ET"}:
            self.tm = None
        elif operator in {b"Tj", b"TJ", b"'", b'"'} and args:
            with suppress(
                AttributeError, LookupError, NotImplementedError, PdfReadError,
                TypeError, ValueError,
            ):
                self._record(args[0] if operator == b"TJ" else args[-1:], cm, tm)

    def _record(self, operands, cm, tm):
        if not isinstance(self.fonts, dict) or self.font not in self.fonts:
            self.disabled = True
            return
        resource = self.fonts[self.font]
        if self.font not in self.decoded_fonts:
            self.decoded_fonts[self.font] = Font.from_font_resource(resource)
        font = self.decoded_fonts[self.font]
        a, b, c, d, x, y = mult(tm, cm)
        horizontal = a > 0 and d > 0 and abs(b) + abs(c) < 0.001
        height = d * self.size if horizontal else 0.0
        scale = a * self.horizontal_scale
        if self.tm != (a, b, c, d, x, y):
            self.advance = 0.0
            self.tm = a, b, c, d, x, y
        x += self.advance
        replacements = _font_repairs(resource, self.repairs)
        for operand in operands:
            if isinstance(operand, (int, float)):
                x -= operand * scale * self.size / 1000
                continue
            if isinstance(operand, NameObject) or not isinstance(operand, (str, bytes)):
                continue
            if isinstance(operand, bytes) and isinstance(font.encoding, dict):
                encoded = [font.encoding.get(byte, chr(byte)) for byte in operand]
            elif isinstance(operand, bytes):
                encoded = operand.decode(font.encoding, errors="surrogateescape")
            else:
                encoded = operand
            # For one-byte encodings, preserve the exact glyph boundary of private names.
            glyphs = [font.character_map.get(char, char) for char in encoded]
            has_repairs = replacements and any(glyph in replacements for glyph in glyphs)
            units = glyphs if has_repairs else ["".join(glyphs)]
            codes = operand.decode("latin1") if isinstance(operand, bytes) else operand
            for index, unit in enumerate(units):
                codes_in_unit = (
                    codes[index:index + 1] if has_repairs and len(codes) == len(units) else codes
                )
                width = scale * (
                    self.size * font.get_text_width(codes_in_unit) / 1000
                    + self.char_spacing * len(codes_in_unit)
                    + self.word_spacing * codes_in_unit.count(" ")
                )
                self.runs.append(
                    _PdfTextRun(unit, replacements, x, y + d * self.rise, height, width),
                )
                x += width
        self.advance = x - self.tm[4]

    def apply(self, original: str) -> str | None:
        if not self.runs:
            return None
        # pypdf owns reading order and spacing. Use source geometry only when the same
        # non-space characters line up one-for-one with its extracted text.
        stripped = ["".join(run.text.split()) for run in self.runs]
        offsets = [index for index, char in enumerate(original) if not char.isspace()]
        if "".join(stripped) != "".join(original[index] for index in offsets):
            return None
        patches = []
        start = 0
        previous = None
        raised_sign = None
        recent = ""
        for run, text in zip(self.runs, stripped, strict=True):
            end = start + len(text)
            if not text:
                continue
            left, right = offsets[start], offsets[end - 1] + 1
            gap = original[offsets[start - 1] + 1:left] if start else ""
            replacement = run.repairs.get(text, text)
            if replacement == "\u0301":
                # These embedded subsets draw the accent just before its base letter.
                if (
                    start and original[offsets[start - 1]].isalpha()
                    and right < len(original) and original[right].isalpha() and "\n" not in gap
                ):
                    patches.append((left - len(gap), right + 1, original[right] + replacement))
            else:
                superscript = None
                if raised_sign is not None and previous is not None and not gap:
                    sign_start, sign = raised_sign
                    if _same_script_line(run, previous) and re.fullmatch(r"[0-9]{1,3}", text):
                        superscript = (sign + text).translate(_TEXT_TO_SUPERSCRIPT)
                        if patches and patches[-1][0] == sign_start:
                            patches.pop()
                        patches.append((sign_start, right, superscript))
                raised_sign = None
                if previous is not None and "\n" not in gap and _raised(run, previous):
                    superscript = _scientific_exponent(recent, replacement)
                    if superscript:
                        patches.append((left - len(gap), right, superscript))
                    elif replacement in {"−", "-", "+"} and re.search(
                        r"(?<![\w.])(?:10|V|s)$", recent,
                    ):
                        raised_sign = left, replacement.replace("−", "-")
                if superscript is None and replacement != text:
                    patches.append((left, right, replacement))
            displayed = replacement if replacement != text else original[left:right]
            recent = (recent + gap + displayed)[-80:]
            previous = run
            start = end
        parts = []
        offset = 0
        for left, right, replacement in patches:
            if left < offset:
                # Overlapping glyph repairs cannot be applied independently.
                return None
            parts.extend((original[offset:left], replacement))
            offset = right
        parts.append(original[offset:])
        return "".join(parts)


def _raised(run: _PdfTextRun, base: _PdfTextRun) -> bool:
    return (
        0 < run.height <= base.height * 0.85
        and 0.19 * base.height <= run.y - base.y <= 0.7 * base.height
        and -0.25 * base.height <= run.x - base.x - base.width <= 0.55 * base.height
    )


def _same_script_line(run: _PdfTextRun, previous: _PdfTextRun) -> bool:
    return (
        abs(run.y - previous.y) < 0.1 * previous.height
        and abs(run.height - previous.height) < 0.12 * previous.height
        and abs(run.x - previous.x - previous.width) < 0.35 * previous.height
    )


def _scientific_exponent(recent: str, text: str) -> str | None:
    if (
        re.search(r"(?<![\w.])10$", recent) and re.fullmatch(r"[+−-]?[0-9]{1,3}", text)
        or re.search(r"(?<!\w)[Vs]$", recent) and text in {"−1", "-1"}
    ):
        return text.replace("−", "-").translate(_TEXT_TO_SUPERSCRIPT)
    if re.search(r"(?<![\w/])c?m$", recent) and text == "2":
        return "²"
    return None


def _pdf_page_text(page, symbol_fonts: dict[int, dict[str, str]]) -> str:
    original_parts: list[str] = []
    parts: list[str] = []
    raise_exponent = False
    previous_y = None
    runs = _PdfTextRuns(page, symbol_fonts)

    def visit(text, cm, tm, font, size):
        nonlocal raise_exponent, previous_y
        if not text:
            return
        original_parts.append(text)
        y = cm[1] * tm[4] + cm[3] * tm[5] + cm[5]
        if raise_exponent and y == previous_y:
            exponent = re.match(r"[0-9]+(?![.0-9])", text)
            if exponent:
                text = exponent[0].translate(_TEXT_TO_SUPERSCRIPT) + text[exponent.end():]
                parts[-1] = parts[-1].replace("−", "⁻")
        raise_exponent = False
        repairs = _font_repairs(font, symbol_fonts)
        repaired = _repair_visitor_span(text, repairs)
        if repaired != text:
            raise_exponent = (
                repaired.strip() == "−" and previous_y is not None and y > previous_y
                and bool(parts and re.search(r"(?<!\d)10$", parts[-1]))
            )
        parts.append(repaired)
        previous_y = y

    original = page.extract_text(visitor_text=visit, visitor_operand_after=runs.visit) or ""
    if (positioned := runs.apply(original)) is not None:
        return positioned
    return "".join(parts) if "".join(original_parts) == original else original


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
        symbol_fonts: dict[int, dict[str, str]] = {}
        total = 0
        for i, page in enumerate(reader.pages, 1):
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 20 * 1024 * 1024:
                raise AppError("pdf_too_complex", "PDF 页面解压后过大，请简化后上传。")
            text = clean_pdf_text(_pdf_page_text(page, symbol_fonts))
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
