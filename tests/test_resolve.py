"""Tests for entity resolution and the review queue.

Company names here are real, because the point of these tests is that
"NVIDIA Corporation" and "NVIDIA Corp" are one company. No supply relationship
is asserted anywhere in this file.
"""

from __future__ import annotations

import csv

import pytest

from hscm.resolve import (
    Aliases,
    Resolver,
    Spine,
    apply_review_queue,
    build_review_queue,
    normalize_name,
)

SPINE_PAYLOAD = {
    "0": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 723125, "ticker": "MU", "title": "MICRON TECHNOLOGY INC"},
    "3": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "4": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
    "5": {"cik_str": 1046179, "ticker": "TSM", "title": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"},
}


@pytest.fixture
def spine() -> Spine:
    return Spine.from_json(SPINE_PAYLOAD)


@pytest.fixture
def resolver(spine) -> Resolver:
    return Resolver(spine, Aliases())


def record(supplier="NVIDIA Corporation", buyer="Microsoft Corporation", **extra) -> dict:
    base = {
        "supplier_name_raw": supplier,
        "buyer_name_raw": buyer,
        "source_sentence": "A sentence long enough to be a plausible citation from a filing.",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/000/x.htm",
        "form_type": "10-K",
        "filing_date": "2025-07-31",
        "relationship_type": "supplies",
        "extraction_confidence": "high",
        "quantified_pct": None,
        "quantified_basis": None,
        "product_or_service": None,
    }
    base.update(extra)
    return base


# --- normalisation ----------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NVIDIA Corporation", "NVIDIA"),
        ("NVIDIA Corp.", "NVIDIA"),
        ("Micron Technology, Inc.", "MICRON TECHNOLOGY"),
        ("The Kroger Co.", "KROGER"),
        ("Arm Holdings plc", "ARM"),
    ],
)
def test_corporate_suffixes_are_stripped(raw, expected):
    assert normalize_name(raw) == expected


def test_normalisation_makes_spelling_variants_equal():
    assert normalize_name("Taiwan Semiconductor Manufacturing Company Limited") == normalize_name(
        "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"
    )


# --- resolution -------------------------------------------------------------
def test_exact_match_after_normalisation(resolver):
    resolution = resolver.resolve("NVIDIA Corporation")
    assert resolution.is_resolved
    assert resolution.cik == 1045810
    assert resolution.method == "exact"


def test_bare_ticker_resolves(resolver):
    resolution = resolver.resolve("MSFT")
    assert resolution.is_resolved
    assert resolution.cik == 789019
    assert resolution.method == "ticker"


def test_share_classes_collapse_to_one_company(resolver):
    """GOOG and GOOGL are two rows in the spine and one company in the graph."""
    assert resolver.resolve("Alphabet Inc.").cik == 1652044


def test_unknown_name_goes_to_review(resolver):
    resolution = resolver.resolve("Some Private Supplier Holdings")
    assert resolution.status == "review"
    assert not resolution.is_resolved


def test_near_miss_below_threshold_goes_to_review(spine):
    """A high threshold is the point: a wrong merge is invisible, a queue row is not."""
    strict = Resolver(spine, Aliases(), threshold=0.99)
    assert strict.resolve("Micron Technologies").status == "review"


def test_alias_file_resolves_what_matching_cannot(spine):
    aliases = Aliases(aliases={"TSMC": {"cik": 1046179}})
    resolution = Resolver(spine, aliases).resolve("TSMC")
    assert resolution.is_resolved
    assert resolution.cik == 1046179
    assert resolution.method == "alias"
    assert resolution.canonical_name == "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"


def test_excluded_names_are_excluded_not_reviewed(spine):
    aliases = Aliases(excluded={"Samsung Electronics": "Korean-listed; does not file with the SEC"})
    resolution = Resolver(spine, aliases).resolve("Samsung Electronics")
    assert resolution.status == "excluded"
    assert "does not file" in resolution.reason
    assert resolution.cik is None


def test_empty_name_is_not_resolved(resolver):
    assert resolver.resolve("").status == "review"


def test_a_shortened_name_gets_a_candidate_instead_of_nothing(spine):
    """Filings say "Credo"; the registry says "CREDO TECHNOLOGY GROUP HOLDING LTD".

    Similarity scoring rejects five characters against thirty before it even
    compares them, so the queue used to tell the reviewer "no candidate" for a
    name sitting in the spine.
    """
    extended = dict(SPINE_PAYLOAD)
    extended["6"] = {
        "cik_str": 1807794,
        "ticker": "CRDO",
        "title": "Credo Technology Group Holding Ltd",
    }
    resolution = Resolver(Spine.from_json(extended), Aliases()).resolve("Credo")
    assert resolution.status == "review"  # a person still decides
    assert resolution.candidates[0][2] == 1807794


def test_a_prefix_match_is_never_auto_accepted(spine):
    """"Delta" opens an airline and an apparel maker. Strong is not proof."""
    extended = dict(SPINE_PAYLOAD)
    extended["6"] = {"cik_str": 27904, "ticker": "DAL", "title": "DELTA AIR LINES INC"}
    extended["7"] = {"cik_str": 1101396, "ticker": "DLA", "title": "DELTA APPAREL INC"}
    resolution = Resolver(Spine.from_json(extended), Aliases()).resolve("Delta")
    assert resolution.status == "review"
    assert len(resolution.candidates) == 2


def test_an_acronym_the_registry_does_not_use_still_has_no_candidate(spine):
    """TSMC is not a prefix of "TAIWAN SEMICONDUCTOR...". The alias file is for this."""
    assert Resolver(spine, Aliases()).resolve("TSMC").candidates == ()


# --- alias file round trip --------------------------------------------------
def test_aliases_round_trip(tmp_path):
    path = tmp_path / "aliases.yaml"
    Aliases(aliases={"TSMC": {"cik": 1046179}}, excluded={"SK Hynix": "not an SEC filer"}).save(path)
    reloaded = Aliases.load(path)
    assert reloaded.aliases["TSMC"]["cik"] == 1046179
    assert reloaded.excluded["SK Hynix"] == "not an SEC filer"


def test_missing_alias_file_loads_empty(tmp_path):
    aliases = Aliases.load(tmp_path / "nope.yaml")
    assert aliases.aliases == {} and aliases.excluded == {}


# --- review queue -----------------------------------------------------------
def test_queue_holds_only_unresolved_names_ordered_by_frequency(tmp_path, resolver):
    records = [
        record(supplier="NVIDIA Corporation"),                    # resolves
        record(supplier="Obscure Widget Fabricators"),            # queued x3
        record(supplier="Obscure Widget Fabricators"),
        record(supplier="Obscure Widget Fabricators"),
        record(supplier="Another Unknown Vendor"),                # queued x1
    ]
    path, pending = build_review_queue(records, resolver, tmp_path / "queue.csv")

    assert [r.raw_name for r in pending] == [
        "Obscure Widget Fabricators",
        "Another Unknown Vendor",
    ]
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["occurrences"] == "3"
    assert rows[0]["example_sentence"].startswith("A sentence long enough")
    assert rows[0]["decision"] == ""  # a human fills this in


def test_queue_is_empty_when_everything_resolves(tmp_path, resolver):
    _, pending = build_review_queue([record()], resolver, tmp_path / "queue.csv")
    assert pending == []


def _write_queue(path, rows):
    from hscm.resolve import REVIEW_COLUMNS

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REVIEW_COLUMNS})


def test_apply_accepts_best_match(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "NVIDIA Corp", "best_match_cik": "1045810", "decision": "accept"}])
    aliases = Aliases()
    added, excluded, non_filers, problems = apply_review_queue(aliases, path)
    assert (added, excluded, non_filers, problems) == (1, 0, 0, [])
    assert aliases.aliases["NVIDIA Corp"]["cik"] == 1045810


def test_apply_takes_a_hand_entered_cik(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "TSMC Arizona", "cik": "1046179", "decision": "cik",
                         "reason": "subsidiary, rolled up to the parent"}])
    aliases = Aliases()
    apply_review_queue(aliases, path)
    assert aliases.aliases["TSMC Arizona"] == {
        "cik": 1046179,
        "note": "subsidiary, rolled up to the parent",
    }


def test_apply_records_exclusions_with_a_reason(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "Samsung Electronics", "decision": "exclude",
                         "reason": "Korean-listed; does not file with the SEC"}])
    aliases = Aliases()
    _, excluded, _, problems = apply_review_queue(aliases, path)
    assert excluded == 1 and problems == []
    assert "Korean-listed" in aliases.excluded["Samsung Electronics"]


def test_exclusion_without_a_reason_is_refused(tmp_path):
    """An undocumented exclusion is exactly what the limitations page cannot show."""
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "Mystery Corp", "decision": "exclude"}])
    aliases = Aliases()
    _, excluded, _, problems = apply_review_queue(aliases, path)
    assert excluded == 0
    assert problems and "reason" in problems[0]


def test_undecided_and_skipped_rows_are_left_alone(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [
        {"raw_name": "Undecided Corp", "decision": ""},
        {"raw_name": "Later Corp", "decision": "skip"},
    ])
    aliases = Aliases()
    added, excluded, non_filers, problems = apply_review_queue(aliases, path)
    assert (added, excluded, non_filers, problems) == (0, 0, 0, [])


def test_decision_without_a_usable_cik_is_reported(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "Typo Corp", "decision": "cik", "cik": "not-a-number"}])
    added, _, _, problems = apply_review_queue(Aliases(), path)
    assert added == 0 and problems


# --- non-filers -------------------------------------------------------------
def test_non_filer_resolves_to_a_node_without_a_cik(spine):
    """OpenAI is named in Microsoft's 10-K and files nothing itself."""
    aliases = Aliases(non_filers={"OpenAI": "Private company; named in filings, files none"})
    resolution = Resolver(spine, aliases).resolve("OpenAI")
    assert resolution.is_resolved
    assert resolution.cik is None
    assert resolution.method == "non_filer"
    assert resolution.node_key == "name-openai"


def test_non_filer_key_is_stable_across_spellings(spine):
    aliases = Aliases(non_filers={"OpenAI": "x", "OpenAI, Inc.": "x"})
    resolver = Resolver(spine, aliases)
    assert resolver.resolve("OpenAI").node_key == resolver.resolve("OpenAI, Inc.").node_key


def test_filers_still_key_on_cik(resolver):
    assert resolver.resolve("NVIDIA Corporation").node_key == "cik-0001045810"


def test_two_spellings_of_one_non_filer_are_one_node(spine):
    """NVIDIA's 10-K says "Samsung Electronics Co., Ltd." once and "Samsung" later.

    There is no CIK spine to collapse them onto, so without a declared canonical
    name the graph grows two Samsungs and splits its evidence between them.
    """
    aliases = Aliases(
        non_filers={
            "Samsung Electronics Co., Ltd.": "Korean-listed; files nothing with the SEC",
            "Samsung": {
                "canonical": "Samsung Electronics Co., Ltd.",
                "note": "Short form used later in the same filing",
            },
        }
    )
    resolver = Resolver(spine, aliases)
    assert resolver.resolve("Samsung").node_key == resolver.resolve(
        "Samsung Electronics Co., Ltd."
    ).node_key


def test_a_canonical_name_survives_the_file(tmp_path):
    path = tmp_path / "aliases.yaml"
    Aliases(non_filers={"Samsung": {"canonical": "Samsung Electronics", "note": "short form"}}).save(path)
    reloaded = Aliases.load(path)
    assert reloaded.non_filers["Samsung"]["canonical"] == "Samsung Electronics"


def test_apply_records_a_non_filer(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "OpenAI", "decision": "non-filer",
                         "reason": "Private; named in filings, files none"}])
    aliases = Aliases()
    added, excluded, non_filers, problems = apply_review_queue(aliases, path)
    assert (added, excluded, non_filers, problems) == (0, 0, 1, [])
    assert "Private" in aliases.non_filers["OpenAI"]["note"]


def test_non_filer_without_a_reason_gets_a_default(tmp_path):
    path = tmp_path / "queue.csv"
    _write_queue(path, [{"raw_name": "xAI", "decision": "non-filer"}])
    aliases = Aliases()
    apply_review_queue(aliases, path)
    assert "does not file" in aliases.non_filers["xAI"]["note"]


def test_non_filers_round_trip_through_the_file(tmp_path):
    path = tmp_path / "aliases.yaml"
    Aliases(non_filers={"OpenAI": "Private company"}).save(path)
    assert Aliases.load(path).non_filers["OpenAI"]["note"] == "Private company"
