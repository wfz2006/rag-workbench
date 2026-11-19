"""Import one numeric XY dataset per WebPlotDigitizer CSV."""

import csv
import hashlib
import io
import math
import re
from pathlib import PureWindowsPath

from rag_workbench.domain.digitized_curves import MAX_CURVE_POINTS, CurveSource, DigitizedCurve
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.material_preview import load_original, pdf_page_count
from rag_workbench.infrastructure.material_repository import MaterialsRepository

MAX_CSV_BYTES = 2 * 1024 * 1024
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def parse_xy_csv(raw: bytes, *, has_header: bool = False) -> tuple[tuple[float, float], ...]:
    if len(raw) > MAX_CSV_BYTES:
        raise AppError("curve_csv_size", "CSV 超过 2 MiB，请拆分成较小的数据集。")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    except UnicodeDecodeError as exc:
        raise AppError("curve_csv_encoding", "请将 CSV 保存为 UTF-8 编码后再导入。") from exc
    points = []
    while True:
        line = reader.line_num + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise AppError("curve_csv_row", f"CSV 第 {line} 行格式无效。") from exc
        if not fields or (len(fields) == 1 and not fields[0].strip()):
            continue
        values = [value.strip().replace("−", "-") for value in fields]
        if (len(values) == 2 and has_header and all(values)
                and not any(_NUMBER.fullmatch(value) for value in values)
                and all(any(char.isalpha() for char in value) for value in values)):
            has_header = False
            continue
        has_header = False
        if len(values) != 2 or not all(_NUMBER.fullmatch(value) for value in values):
            raise AppError("curve_csv_row", f"CSV 第 {line} 行需要两个以逗号分隔的数值。")
        x, y = map(float, values)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise AppError("curve_csv_row", f"CSV 第 {line} 行数值超出范围。")
        points.append((x, y))
        if len(points) > MAX_CURVE_POINTS:
            raise AppError("curve_csv_size", f"一条曲线最多 {MAX_CURVE_POINTS} 个点。")
    if not points:
        raise AppError("curve_csv_empty", "CSV 中没有可导入的 XY 数据。")
    return tuple(points)


def import_curve(
    repo: MaterialsRepository, source: CurveSource, raw: bytes, filename: str, *,
    has_header: bool = False,
) -> DigitizedCurve:
    source = CurveSource.model_validate(source.model_dump())
    points = parse_xy_csv(raw, has_header=has_header)
    document = repo.document(source.document_id)
    if document is None or document.extension != ".pdf":
        raise AppError("curve_pdf_required", "请选择曲线所在的原始 PDF。")
    if source.page > pdf_page_count(load_original(repo, document)):
        raise AppError("curve_pdf_page", "图表页码超出所选 PDF 的范围。")
    digest = hashlib.sha256(raw).hexdigest()
    identity = hashlib.sha256((source.model_dump_json() + digest).encode()).hexdigest()
    return repo.save_digitized_curve(DigitizedCurve(
        id=identity, source=source, points=points, csv_sha256=digest,
        csv_name=PureWindowsPath(filename).name[:255] or "curve.csv",
    ))
