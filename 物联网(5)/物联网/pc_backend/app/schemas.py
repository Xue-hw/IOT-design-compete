from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LightData(StrictModel):
    lux: float = Field(ge=0)
    label: str = Field(min_length=1, max_length=32)


class ImuData(StrictModel):
    valid: bool = True
    face: int
    mode: str = Field(min_length=1, max_length=32)
    activity: float = Field(ge=0)


class FocusData(StrictModel):
    valid: bool = True
    state: str = Field(min_length=1, max_length=32)
    remaining_s: int = Field(ge=0)
    session_count: int = Field(ge=0)


class PowerData(StrictModel):
    valid: bool =True
    battery_pct: int = Field(ge=0, le=100)
    charging: bool


class TelemetryIn(StrictModel):
    device_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=32)
    ts: int = Field(gt=0)
    light: LightData
    imu: ImuData
    focus: FocusData
    power: PowerData

    @field_validator("device_id")
    @classmethod
    def strip_device_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("device_id cannot be empty")
        return value


class DeviceConfigUpdate(StrictModel):
    light_min_lux: float | None = Field(default=None, ge=0)
    light_max_lux: float | None = Field(default=None, ge=0)
    low_battery_pct: int | None = Field(default=None, ge=0, le=100)
    online_timeout_s: int | None = Field(default=None, ge=5, le=3600)
    focus_session_minutes: int | None = Field(default=None, ge=1, le=180)

    @field_validator("light_max_lux")
    @classmethod
    def validate_max_lux(cls, value: float | None) -> float | None:
        return value


class TimeseriesMetric(BaseModel):
    metric: Literal["lux", "activity", "battery_pct", "remaining_s", "session_count"]
