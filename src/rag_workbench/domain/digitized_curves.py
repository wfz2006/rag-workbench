from typing import Literal

from pydantic import Field, FiniteFloat, field_validator

from rag_workbench.domain.materials import SourceId
from rag_workbench.domain.models import Contract, utc_now

MAX_CURVE_POINTS = 20000


class CurveSource(Contract):
    document_id: SourceId
    figure: str = Field(min_length=1, max_length=120)
    page: int = Field(ge=1, strict=True)
    series: str = Field(min_length=1, max_length=160)
    x_name: str = Field(min_length=1, max_length=120)
    y_name: str = Field(min_length=1, max_length=120)
    x_unit: str = Field(min_length=1, max_length=80)
    y_unit: str = Field(min_length=1, max_length=80)

    @field_validator("figure", "series", "x_name", "y_name", "x_unit", "y_unit", mode="before")
    @classmethod
    def strip_fields(cls, value):
        return value.strip() if isinstance(value, str) else value


class DigitizedCurve(Contract):
    id: SourceId
    source: CurveSource
    csv_name: str
    csv_sha256: SourceId
    points: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(
        min_length=1, max_length=MAX_CURVE_POINTS,
    )
    origin: Literal["digitized"] = "digitized"
    created_at: str = Field(default_factory=utc_now)
