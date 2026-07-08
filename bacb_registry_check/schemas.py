"""Schemas for the standalone BACB registry checker."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CredentialType = Literal["RBT", "BCaBA", "BCBA", "BCBA-D"]
SearchMode = Literal["certification_number", "state_name"]
CheckStatus = Literal["completed", "not_found", "needs_human_verification", "failed"]


class BacbCheckRequest(BaseModel):
    """Request to search the public BACB Certificant Registry."""

    model_config = ConfigDict(extra="forbid")

    rbt_number: str | None = Field(
        default=None,
        description="RBT or BACB certification number, for example RBT-25-445224.",
    )
    state: str | None = Field(
        default=None,
        description="State abbreviation or state name for a name lookup.",
    )
    name: str | None = Field(
        default=None,
        description="Full name for a state/name lookup.",
    )
    credential: CredentialType = Field(
        default="RBT",
        description="Credential type to filter on for state/name lookup.",
    )

    @field_validator("rbt_number", "state", "name")
    @classmethod
    def _trim_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @field_validator("state")
    @classmethod
    def _normalize_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) == 2:
            return value.upper()
        return value

    @model_validator(mode="after")
    def _validate_lookup_mode(self) -> BacbCheckRequest:
        has_number = self.rbt_number is not None
        has_state_name = self.state is not None and self.name is not None
        if has_number == has_state_name:
            raise ValueError("submit either rbt_number or both state and name, but not both")
        if (self.state is None) != (self.name is None):
            raise ValueError("state and name must be submitted together")
        return self

    @property
    def search_mode(self) -> SearchMode:
        """Return the validated lookup mode."""

        if self.rbt_number is not None:
            return "certification_number"
        return "state_name"


class BacbRegistryRecord(BaseModel):
    """A record read from the BACB registry result table."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    location: str | None = None
    country: str | None = None
    certification: str | None = None
    status: str | None = None
    certification_level: str | None = None
    certification_number: str | None = None
    original_certification_date: str | None = None
    expiration_date: str | None = None
    supervisors: list[str] = Field(default_factory=list)
    raw_summary: str | None = None
    raw_details: str | None = None


class BacbCheckResponse(BaseModel):
    """Response returned by the BACB registry checker."""

    model_config = ConfigDict(extra="forbid")

    search_mode: SearchMode
    submitted_rbt_number: str | None = None
    submitted_state: str | None = None
    submitted_name: str | None = None
    credential: CredentialType
    status: CheckStatus
    matched: bool | None = None
    selected_record: BacbRegistryRecord | None = None
    records: list[BacbRegistryRecord] = Field(default_factory=list)
    screenshot_url: str | None = None
    screenshot_path: str | None = None
    result_summary: str
