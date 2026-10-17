from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class QueryPlan(StrictModel):
    operation: Literal["status", "temperatures", "downtime", "spills", "costs", "maintenance", "search"]
    asset_ids: list[str] = Field(default_factory=list, max_length=2000)
    area: str | None = None
    start: date | None = None
    end: date = date(2026, 10, 16)
    only_nonproducing: bool = False
    limit: int = Field(default=20, ge=1, le=2000)
    view: Literal["card", "table", "line", "bar", "map", "heatmap"] = "table"
    search: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def dates_valid(self):
        if self.start and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class Inspection(StrictModel):
    inspected_at: datetime
    ambient_temp_c: float = Field(ge=-80, le=200)
    wellhead_temp_c: float = Field(ge=-80, le=300)
    method: Literal["manual", "drone"] = "manual"
    spill_detected: bool = False
    spill_est_volume_bbl: float | None = Field(default=None, ge=0)
    spill_area_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def consistent(self):
        if self.inspected_at.tzinfo is None:
            raise ValueError("Inspection timestamp must include a timezone")
        if not self.spill_detected and (self.spill_est_volume_bbl is not None or self.spill_area_pct is not None):
            raise ValueError("Spill measurements require spill_detected")
        return self


class KnowledgeCommand(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    user_id: str
    asset_id: str
    expected_version: int = Field(ge=0)
    kind: Literal["inspection", "correction", "note"]
    inspection: Inspection | None = None
    target_id: str | None = None
    field: Literal["wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl", "spill_detected"] | None = None
    value: float | bool | None = None
    reason: str = Field(min_length=3, max_length=4000)

    @model_validator(mode="after")
    def payload_valid(self):
        if self.kind == "inspection" and not self.inspection:
            raise ValueError("New inspections require inspection data")
        if self.kind == "correction":
            if not self.target_id or not self.field or self.value is None:
                raise ValueError("Corrections require target_id, field and value")
            if self.field == "spill_detected":
                if type(self.value) is not bool:
                    raise ValueError("spill_detected must be a boolean")
            elif type(self.value) is bool:
                raise ValueError("A numeric field cannot be a boolean")
            elif self.field == "spill_est_volume_bbl" and self.value < 0:
                raise ValueError("Spill volume cannot be negative")
            elif self.field.endswith("temp_c") and not -80 <= self.value <= 300:
                raise ValueError("Temperature outside supported range")
        return self


class ChatRequest(StrictModel):
    user_id: str
    thread_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=8000)
    as_of: date = date(2026, 10, 16)
