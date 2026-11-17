"""The learning map: quote normalisation, the curated sources, web checks."""

from __future__ import annotations

import json

import pytest

from hscm import config, learn


def test_normalize_folds_typography_but_not_wording():
    assert learn.normalize_quote_text("OpenAI’s  “compute” provider") == "OpenAI's \"compute\" provider"
    assert learn.normalize_quote_text("$30 billion") != learn.normalize_quote_text("$3 billion")
    assert learn.normalize_quote_text("Meta") != learn.normalize_quote_text("meta")


def test_quote_key_ignores_typography():
    url = "https://example.com/a"
    assert learn.quote_key(url, "it’s here") == learn.quote_key(url, "it's  here")


def test_accession_parsed_from_sec_url():
    url = "https://www.sec.gov/Archives/edgar/data/1297996/000110465926015365/dlr-20251231x10k.htm"
    assert learn.accession_of(url) == "000110465926015365"
    assert learn.accession_of("https://openai.com/index/x/") is None


graph_path = config.REPO_ROOT / "site" / "data" / "graph.json"
needs_cache = pytest.mark.skipif(
    not (config.CACHE_DIR / "manifest.json").exists() or not graph_path.exists(),
    reason="needs the cached filings and a built graph",
)


@pytest.fixture(scope="module")
def payload():
    built, _ = learn.build_map(json.loads(graph_path.read_text(encoding="utf-8")))
    return built


@needs_cache
def test_curated_sources_build_and_every_edge_is_cited(payload):
    assert payload["edges"], "the map should not be empty"
    for edge in payload["edges"]:
        assert edge["deals"]
        for deal in edge["deals"]:
            assert deal["evidence"], f"{edge['id']} has a deal with no quote"
            for cite in deal["evidence"]:
                assert cite["quote"] and cite["url"].startswith("https://")
                assert cite["type"] in learn.SOURCE_TYPES
    node_ids = {n["id"] for n in payload["nodes"]}
    for edge in payload["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    layer_keys = {layer["key"] for layer in payload["layers"]}
    assert all(n["layer"] in layer_keys for n in payload["nodes"])


@needs_cache
def test_entegris_correction_points_the_right_way(payload):
    ids = {e["id"] for e in payload["edges"]}
    entegris, tsmc = "cik-0001101302", "cik-0001046179"
    assert f"{entegris}--{tsmc}" in ids
    assert f"{tsmc}--{entegris}" not in ids


@needs_cache
def test_tours_only_walk_cited_edges(payload):
    ids = {e["id"] for e in payload["edges"]}
    for tour in payload["tours"]:
        for step in tour["steps"]:
            assert step["edge"] in ids


def test_verify_web_sorts_pages_into_found_missing_unreadable(tmp_path, monkeypatch):
    checks_path = tmp_path / "web_checks.json"
    monkeypatch.setattr(learn, "WEB_CHECKS_PATH", checks_path)
    monkeypatch.setattr(learn, "web_citations", lambda: [
        {"url": "https://a.example/", "quote": "We sell \u201cwidgets\u201d to factories.", "where": "t"},
        {"url": "https://a.example/", "quote": "We sell gadgets to factories.", "where": "t"},
        {"url": "https://shell.example/", "quote": "Microsoft signed a large agreement.", "where": "t"},
        {"url": "https://data.example/", "quote": "Microsoft signed a large agreement.", "where": "t"},
        {"url": "https://down.example/", "quote": "Anything at all here.", "where": "t"},
    ])
    pages = {
        "https://a.example/": "<html><body><p>We sell \"widgets\" to factories.</p>"
                              "<script>We sell gadgets to factories.</script></body></html>",
        "https://shell.example/": "<html><body><p>Please enable JavaScript.</p></body></html>",
        "https://data.example/": '<html><body><div id="root"></div><script type="application/ld+json">'
                                 '{"articleBody": "<p>Microsoft signed a large agreement.</p>"}</script></body></html>',
    }

    def fetch(url):
        if url not in pages:
            raise OSError("blocked")
        return pages[url]

    checks = learn.verify_web(fetch, today="2026-01-01", debug_dir=tmp_path / "debug")
    by_url_quote = {(r["url"], r["quote"]): r["status"] for r in checks.values()}
    assert by_url_quote[("https://a.example/", "We sell \u201cwidgets\u201d to factories.")] == "found"
    # Words are all there (in a script), the sentence is not visible: a real miss.
    assert by_url_quote[("https://a.example/", "We sell gadgets to factories.")] == "not_found"
    assert by_url_quote[("https://shell.example/", "Microsoft signed a large agreement.")] == "unreadable"
    assert by_url_quote[("https://data.example/", "Microsoft signed a large agreement.")] == "found"
    assert by_url_quote[("https://down.example/", "Anything at all here.")] == "unreachable"
    assert len(list((tmp_path / "debug").glob("*.html"))) == 2


def test_unreadable_page_keeps_an_earlier_found_check(tmp_path, monkeypatch):
    checks_path = tmp_path / "web_checks.json"
    monkeypatch.setattr(learn, "WEB_CHECKS_PATH", checks_path)
    cite = {"url": "https://shell.example/", "quote": "Microsoft signed a large agreement.", "where": "t"}
    monkeypatch.setattr(learn, "web_citations", lambda: [cite])
    key = learn.quote_key(cite["url"], cite["quote"])
    learn.save_web_checks({key: {"key": key, "url": cite["url"], "quote": cite["quote"],
                                 "status": "found", "method": "by eye", "checked": "2025-12-01"}})
    checks = learn.verify_web(lambda url: "<p>Checking your browser</p>", today="2026-01-01",
                              debug_dir=tmp_path / "debug")
    assert checks[key]["status"] == "found"
    assert checks[key]["checked"] == "2025-12-01"
    assert checks[key]["last_attempt"] == {"status": "unreadable", "date": "2026-01-01"}


def test_match_form_closes_gaps_left_by_html_tags():
    page = learn.page_text('<p>In addition to <org>Google</org>\'s deal with <a href="#">Kairos Power</a>.</p>')
    assert learn.match_form("Google's deal with Kairos Power.") in learn.match_form(page)
    # Wording still has to match.
    assert learn.match_form("Google's deal with Kairos.") not in learn.match_form(page)
