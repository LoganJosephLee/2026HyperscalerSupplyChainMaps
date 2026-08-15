"""FixtureExtractor — replays saved extraction output instead of calling the API.

Its purpose is to let everything downstream of extraction be built and tested
while the API key is unavailable. It is a correctness reference for the
verification logic and nothing more: records it replays were not produced by
the model we will actually ship, so a clean M3 run against a fixture says
something about verify.py and nothing about extraction accuracy.

The fixture file it reads does not exist yet, and cannot be written honestly
until a filing has been fetched — inventing filing sentences to fill it would
put fabricated evidence in the repository.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config
from .base import ExtractionRequest, stamp_provenance

DEFAULT_FIXTURE_PATH = config.DATA_DIR / "fixtures" / "extractions.json"


class FixtureNotFoundError(FileNotFoundError):
    """Raised with instructions rather than a bare path, because this is expected."""


class FixtureExtractor:
    """Replays records from a JSON fixture, keyed by accession and section."""

    name = "fixture"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_FIXTURE_PATH
        self._records: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._records is None:
            if not self.path.exists():
                raise FixtureNotFoundError(
                    f"No extraction fixture at {self.path}. Produce one by running the "
                    f"real extractor once (HSCM_EXTRACTOR=anthropic), or point "
                    f"HSCM_FIXTURE at an existing file. The fixture is deliberately "
                    f"not checked in with invented content."
                )
            payload = json.loads(self.path.read_text())
            self._records = payload["relationships"] if isinstance(payload, dict) else payload
        return self._records

    def extract(self, request: ExtractionRequest) -> list[dict]:
        """Return the fixture's records for this filing and section.

        Records are matched on accession number and section key when the fixture
        records them, so a fixture covering several filings replays the right
        subset for each request.
        """
        accession = request.filing.accession
        out: list[dict] = []
        for record in self._load():
            if record.get("_accession") not in (None, accession):
                continue
            if record.get("_section_key") not in (None, request.section_key):
                continue
            stripped = {k: v for k, v in record.items() if not k.startswith("_")}
            # Fixtures may already carry provenance; stamping is idempotent and
            # keeps a hand-edited fixture from citing the wrong filing.
            out.append(stamp_provenance(stripped, request.filing))
        return out
