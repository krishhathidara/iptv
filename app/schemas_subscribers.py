"""Admin-only account and entitlement payloads."""

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.subscribers import utc_datetime


class SubscriberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mac_address: str | None = Field(default=None, max_length=17)
    months: int = Field(ge=1, le=120)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip().upper().replace("-", ":")
        if not re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", normalized):
            raise ValueError("MAC must contain six hex pairs separated by colons or dashes")
        return normalized


class SubscriberUpdate(BaseModel):
    is_active: bool | None = None
    extend_months: int | None = Field(default=None, ge=1, le=120)
    mac_address: str | None = Field(default=None, max_length=17)

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str | None) -> str | None:
        return SubscriberCreate.validate_mac(value)


class SubscriberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    mac_address: str | None
    notes: str | None
    expires_at: datetime
    is_active: bool
    created_at: datetime

    @field_validator("expires_at", "created_at", mode="before")
    @classmethod
    def represent_utc(cls, value: datetime) -> datetime:
        return utc_datetime(value)


class SubscriberCreated(SubscriberResponse):
    playlist_url: str
    portal_url: str
    mag_portal_url: str


class SubscriberRotated(BaseModel):
    playlist_url: str
    portal_url: str
    mag_portal_url: str


class PortalCheckRequest(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    mac_address: str

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str) -> str:
        mac = SubscriberCreate.validate_mac(value)
        if mac is None:
            raise ValueError("A device MAC is required")
        return mac


class PortalCheckResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    expires_at: datetime
    mac_matches: bool
    portal_origin: str