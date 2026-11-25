from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Region(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=40)
    rect: tuple[int, int, int, int]
    panel_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonempty(self):
        x1, y1, x2, y2 = self.rect
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("rect must be a positive [x1,y1,x2,y2) rectangle")
        return self


class Layout(BaseModel):
    schema_version: str = "2.0"
    image_size: tuple[int, int]
    panels: list[Region] = Field(default_factory=list, max_length=100)
    shared_regions: list[Region] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_regions(self):
        if any(v <= 0 for v in self.image_size):
            raise ValueError("image_size must be positive")
        ids = [r.id for r in self.panels + self.shared_regions]
        if len(ids) != len(set(ids)):
            raise ValueError("region IDs must be unique")
        labels = [p.label for p in self.panels]
        if len(labels) != len(set(labels)):
            raise ValueError("panel labels must be unique; resolve ambiguous labels manually")
        panel_ids = {p.id for p in self.panels}
        for r in self.panels + self.shared_regions:
            if r.rect[2] > self.image_size[0] or r.rect[3] > self.image_size[1]:
                raise ValueError(f"out-of-bounds region: {r.id}")
        for r in self.shared_regions:
            if not r.panel_ids or not set(r.panel_ids) <= panel_ids:
                raise ValueError("shared region must refer to existing panels")
        return self


class ProviderConnection(BaseModel):
    base_url: str
    api_key: str = ""
    timeout_seconds: int = Field(default=90, ge=5, le=300)


class Provider(ProviderConnection):
    id: str = ""
    name: str = ""
    protocol: Literal["chat", "responses"] = "chat"
    model: str
    reasoning_effort: Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"] = "default"
    verified: bool = False


class Issue(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    region_id: str | None = None


class WorkRequest(BaseModel):
    image_id: str
    source: str
    output_dir: str
    provider: Provider
    max_calls: int = Field(default=24, ge=4, le=100)
    max_repairs: int = Field(default=3, ge=0, le=10)
    max_seconds: int = Field(default=600, ge=30, le=3600)
