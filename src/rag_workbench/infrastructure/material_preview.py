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

_PDFIUM_LOCK = Lock()  # PDFium is not thread safe, including across documents.
MAX_IMAGE_SIDE = 2400
MAX_IMAGE_PIXELS = 4_000_000


@dataclass(frozen=True)
class PreviewPage:
    png: bytes
    width: int
    height: int


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


def render_pdf_page(data: bytes, page: int, *, scale: float = 1.0) -> PreviewPage:
    if (
        not isinstance(page, int) or isinstance(page, bool)
        or not isinstance(scale, (int, float)) or isinstance(scale, bool)
        or not math.isfinite(scale) or not .25 <= scale <= 3
    ):
        raise AppError("material_preview_range", "预览页码或缩放比例无效。")
    if not 1 <= page <= pdf_page_count(data):
        raise AppError("material_preview_range", "预览页码超出原文范围。")
    identity = hashlib.sha256(data).hexdigest()
    return _render_cached(identity, data, page, float(scale))


@lru_cache(maxsize=6)
def _render_cached(identity: str, data: bytes, page_number: int, scale: float) -> PreviewPage:
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
            return PreviewPage(output.getvalue(), image.width, image.height)
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
