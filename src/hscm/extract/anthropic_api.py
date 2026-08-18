"""AnthropicExtractor — calls claude-sonnet-5 with structured JSON output.

Written and wired, never run: this project has no API key yet. Treat every line
as unverified until it has made a real call.

Notes on the request shape, since they are easy to get wrong:

* `output_config.format` with a json_schema is what guarantees parseable output.
  The deprecated top-level `output_format` parameter is not used.
* `temperature` / `top_p` / `top_k` are rejected by claude-sonnet-5 and are
  absent deliberately — do not add them back to make extraction "deterministic".
* Adaptive thinking is on by default on this model. `effort` is set explicitly
  rather than left at its default so the setting is visible in the diff when we
  tune it.
* `stop_reason == "refusal"` is checked before reading content: a refused
  response has no content to index.
"""

from __future__ import annotations

import json
import logging

from .base import (
    RELATIONSHIP_SCHEMA,
    SYSTEM_PROMPT,
    USER_PROMPT,
    ExtractionRequest,
    stamp_provenance,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16_000  # safe without streaming; a section yields tens of records at most
EFFORT = "medium"

# A section of a 10-K can be several hundred KB of text. Sending one is both
# slow and worse for accuracy — the verbatim-sentence requirement degrades as
# the haystack grows. Sections are split into overlapping windows.
WINDOW_CHARS = 30_000
WINDOW_OVERLAP = 2_000


class AnthropicExtractor:
    """Extracts relationships by calling the Anthropic API."""

    name = "anthropic"

    def __init__(self, model: str = MODEL, effort: str = EFFORT) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ImportError(
                "AnthropicExtractor needs the anthropic package: "
                "uv sync --extra anthropic"
            ) from exc

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self._model = model
        self._effort = effort

    # --- preflight ----------------------------------------------------------
    def check(self) -> dict:
        """One tiny call that exercises the real request shape.

        This code path has never run. The things most likely to be wrong are
        the ones a smoke test catches for a fraction of a cent: a rejected
        JSON schema, a parameter this model no longer accepts, or an auth
        problem. Run it before spending real tokens on a filing.
        """
        sample = (
            "We purchase substantially all of our graphics processing units from "
            "Example Semiconductor Corporation, and we have no second source."
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_PROMPT.format(
                company="Example Filer Corp", ticker="TEST", form_type="10-K",
                filing_date="2026-01-01", section_label="smoke test", text=sample)}],
            output_config={
                "format": {"type": "json_schema", "schema": RELATIONSHIP_SCHEMA},
                "effort": self._effort,
            },
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        parsed = None
        if response.stop_reason != "refusal":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return {
            "model": response.model,
            "stop_reason": response.stop_reason,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "schema_accepted": parsed is not None,
            "relationships": (parsed or {}).get("relationships", []),
            "raw": text[:400],
        }

    # --- windowing ----------------------------------------------------------
    @staticmethod
    def windows(text: str) -> list[str]:
        """Split a section into overlapping windows, preferring line boundaries.

        The overlap exists so a relationship described across a window boundary
        is fully visible in at least one window. Duplicate records across
        windows are expected and are the graph builder's problem, not this
        module's.
        """
        if len(text) <= WINDOW_CHARS:
            return [text]

        windows: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + WINDOW_CHARS, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + WINDOW_CHARS - WINDOW_OVERLAP, end)
                if boundary > start:
                    end = boundary
            windows.append(text[start:end])
            if end >= len(text):
                break
            start = max(start + 1, end - WINDOW_OVERLAP)
        return windows

    # --- extraction ---------------------------------------------------------
    def extract(self, request: ExtractionRequest) -> list[dict]:
        records: list[dict] = []
        for index, window in enumerate(self.windows(request.text), start=1):
            records.extend(self._extract_window(request, window, index))
        return records

    def _extract_window(self, request: ExtractionRequest, text: str, index: int) -> list[dict]:
        filing = request.filing
        prompt = USER_PROMPT.format(
            company=filing.company_name,
            ticker=filing.ticker,
            form_type=filing.form_type,
            filing_date=filing.filing_date,
            section_label=request.section_label,
            text=text,
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "format": {"type": "json_schema", "schema": RELATIONSHIP_SCHEMA},
                    "effort": self._effort,
                },
            )
        except self._anthropic.RateLimitError:
            raise  # the SDK already retried; let the caller decide to back off
        except self._anthropic.APIStatusError as exc:
            logger.error(
                "%s %s window %d: API error %s", filing.ticker, request.section_key, index, exc
            )
            raise

        if response.stop_reason == "refusal":
            logger.warning(
                "%s %s window %d: refused (%s) — no records from this window",
                filing.ticker,
                request.section_key,
                index,
                getattr(response.stop_details, "category", None),
            )
            return []

        if response.stop_reason == "max_tokens":
            # The JSON is truncated and will not parse. Losing the window is
            # correct; silently keeping a half-parsed list is not.
            logger.warning(
                "%s %s window %d: hit max_tokens, output truncated — window dropped",
                filing.ticker,
                request.section_key,
                index,
            )
            return []

        payload = next((block.text for block in response.content if block.type == "text"), "")
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.error(
                "%s %s window %d: response was not valid JSON despite json_schema format",
                filing.ticker,
                request.section_key,
                index,
            )
            return []

        return [
            stamp_provenance(record, filing)
            for record in parsed.get("relationships", [])
            if isinstance(record, dict)
        ]
