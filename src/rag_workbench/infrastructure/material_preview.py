"""Read-only verified originals and bounded, local PDFium page images."""

import hashlib
import io
import math
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

from pypdf import PdfReader

from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.parsing import comparable_pdf_text

_PDFIUM_LOCK = Lock()  # PDFium is not thread safe, including across documents.
MAX_IMAGE_SIDE = 2400
MAX_IMAGE_PIXELS = 4_000_000


@dataclass(frozen=True)
class PreviewPage:
    png: bytes
    width: int
    height: int
    quote_boxes: tuple[tuple[float, float, float, float], ...] = ()


def load_original(repo, document) -> bytes:
    """Never trust a path, size or file that no longer matches its immutable source ID."""
    try:
        folder = repo.originals.resolve()
        path = folder / f"{document.id}{document.extension}"
        if path.resolve().parent != folder:
            raise AppError("material_original_corrupt", "原件路径无效，未打开文件。")
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise AppError("material_original_missing", "原件缺失，请重新导入原文件恢复预览。") from exc
    except OSError as exc:
        raise AppError("material_original_unreadable", "原件无法读取，请检查磁盘和权限。") from exc
    if len(data) != document.size or hashlib.sha256(data).hexdigest() != document.id:
        raise AppError("material_original_corrupt", "原件校验失败，未预览不一致的文件。")
    return data


def pdf_page_count(data: bytes) -> int:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("encrypted")
        count = len(reader.pages)
        if count < 1:
            raise ValueError("empty")
        return count
    except Exception as exc:
        raise AppError("material_preview_pdf", "PDF 页信息无法读取，请检查原件。") from exc


def render_pdf_page(data: bytes, page: int, *, scale: float = 1.0, quote: str = "") -> PreviewPage:
    if (
        not isinstance(page, int) or isinstance(page, bool)
        or not isinstance(scale, (int, float)) or isinstance(scale, bool)
        or not math.isfinite(scale) or not .25 <= scale <= 3
    ):
        raise AppError("material_preview_range", "预览页码或缩放比例无效。")
    if not 1 <= page <= pdf_page_count(data):
        raise AppError("material_preview_range", "预览页码超出原文范围。")
    identity = hashlib.sha256(data).hexdigest()
    return _render_cached(identity, data, page, float(scale), quote)


@lru_cache(maxsize=6)
def _render_cached(
    identity: str, data: bytes, page_number: int, scale: float, quote: str,
) -> PreviewPage:
    # SHA, page and scale are explicit cache dimensions. Cached data remain local.
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise AppError(
            "material_preview_unavailable",
            "尚未安装 PDF 页图预览组件。请安装项目 preview 可选依赖；仍可核对下方原文和下载原件。",
        ) from exc
    document = page = bitmap = image = None
    with _PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(data)
            page = document[page_number - 1]
            width, height = page.get_size()
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValueError("invalid dimensions")
            effective_scale = min(
                scale, (MAX_IMAGE_SIDE - 2) / max(width, height),
                math.sqrt((MAX_IMAGE_PIXELS - 2 * MAX_IMAGE_SIDE) / (width * height)),
            )
            if not math.isfinite(effective_scale) or effective_scale <= 0:
                raise ValueError("invalid render scale")
            bitmap = page.render(scale=effective_scale)
            image = bitmap.to_pil()
            output = io.BytesIO()
            image.save(output, format="PNG")
            boxes = _quote_boxes(page, bitmap, quote, image.width, image.height)
            return PreviewPage(output.getvalue(), image.width, image.height, boxes)
        except Exception as exc:
            raise AppError(
                "material_preview_failed",
                "当前 PDF 页无法生成预览；可继续核对解析文字或下载原件。",
            ) from exc
        finally:
            for resource in (image, bitmap, page, document):
                if resource is not None:
                    with suppress(Exception):
                        resource.close()


def _quote_boxes(page, bitmap, quote, width, height):
    text = comparable_pdf_text(quote)
    if not 4 <= len(text) <= 2000:
        return ()
    textpage = None
    try:
        textpage = page.get_textpage()
        matches = set()
        for candidate in dict.fromkeys((" ".join(quote.split()), text)):
            search = textpage.search(candidate, match_case=True, consecutive=True)
            try:
                while (match := search.get_next()) is not None:
                    if comparable_pdf_text(textpage.get_text_range(*match)) != text:
                        continue
                    matches.add(match)
                    if len(matches) > 1:
                        return ()
            finally:
                search.close()
        if not matches:
            return ()
        count = textpage.count_rects(*matches.pop())
        if not 1 <= count <= 64:
            return ()
        convert = bitmap.get_posconv(page)
        boxes = []
        for index in range(count):
            left, bottom, right, top = textpage.get_rect(index)
            points = [convert.to_bitmap(x, y) for x in (left, right) for y in (bottom, top)]
            xs, ys = zip(*points, strict=True)
            box = min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height
            x0, y0, x1, y1 = box
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                return ()
            boxes.append(box)
        return tuple(boxes)
    except Exception:
        # Quote location is optional and must not prevent the original from rendering.
        return ()
    finally:
        if textpage is not None:
            with suppress(Exception):
                textpage.close()
