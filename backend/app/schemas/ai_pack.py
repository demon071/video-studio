"""Request schemas for the on-demand AI pack (the heavy engine add-on)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AiPackInstallRequest(BaseModel):
    """Install from a URL or from a zip the user already downloaded."""

    url: str | None = Field(
        default=None,
        description="HTTP(S) address of the pack; streamed by the engine before extraction",
    )
    path: str | None = Field(
        default=None,
        description="Absolute path of a local .zip (takes over when `url` is empty)",
    )


__all__ = ["AiPackInstallRequest"]
