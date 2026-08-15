"""Extractor selection.

Which implementation runs is a config decision made here and nowhere else. No
other module imports FixtureExtractor or AnthropicExtractor directly, so
flipping HSCM_EXTRACTOR changes the pipeline without touching a line of
pipeline code.
"""

from __future__ import annotations

from .. import config
from .base import (
    PROVENANCE_FIELDS,
    RELATIONSHIP_SCHEMA,
    SYSTEM_PROMPT,
    USER_PROMPT,
    ExtractionRequest,
    Extractor,
    stamp_provenance,
)

__all__ = [
    "PROVENANCE_FIELDS",
    "RELATIONSHIP_SCHEMA",
    "SYSTEM_PROMPT",
    "USER_PROMPT",
    "ExtractionRequest",
    "Extractor",
    "get_extractor",
    "stamp_provenance",
]


def get_extractor(name: str | None = None) -> Extractor:
    """Build the configured extractor. `name` overrides HSCM_EXTRACTOR."""
    choice = (name or config.EXTRACTOR).lower()

    if choice == "fixture":
        from .fixture import FixtureExtractor

        return FixtureExtractor(config.FIXTURE_PATH)

    if choice == "anthropic":
        from .anthropic_api import AnthropicExtractor

        return AnthropicExtractor()

    raise ValueError(
        f"Unknown extractor {choice!r}. Set HSCM_EXTRACTOR to 'fixture' or 'anthropic'."
    )
