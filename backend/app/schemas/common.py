"""Shared response envelopes and primitives."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class SatvaModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(SatvaModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PageParams(BaseModel):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class Message(SatvaModel):
    message: str


class Disclaimer(SatvaModel):
    """Attached to every result-bearing response.

    Non-negotiable rule 13 requires SATVA to state plainly that it is a
    screening aid. Returning it from the API rather than hard-coding it in each
    client means the wording can never drift between the Android app, the
    dashboards and the complaint document.
    """

    short: str
    long: str
    is_statutory_test: bool = False
    is_certification: bool = False


class GeoPoint(SatvaModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class AuditableTimestamps(SatvaModel):
    created_at: datetime
    updated_at: datetime
