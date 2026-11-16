"""URL normalization and canonical preference (reelline/pipeline/urls.py), and the sources
registry's validation (reelline/pipeline/sources.py). No network."""

import pytest
from reelline.pipeline.sources import SourceRegistryError, load_sources
from reelline.pipeline.urls import UrlError, normalize_url, prefer_canonical

# ---- normalize_url ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # tracking params, fragment, trailing slash
        ("https://ex.ge/a/1/?utm_source=rss&utm_medium=feed#comments", "https://ex.ge/a/1"),
        ("https://ex.ge/a?id=5&fbclid=XYZ&gclid=1&UTM_Campaign=c", "https://ex.ge/a?id=5"),
        # host and scheme case, default ports
        ("HTTPS://Ex.GE:443/Path", "https://ex.ge/Path"),
        ("http://ex.ge:80/p", "http://ex.ge/p"),
        ("http://ex.ge:8080/p/", "http://ex.ge:8080/p"),
        # remaining params sorted, blank values kept
        ("https://ex.ge/s?b=2&a=1&c=", "https://ex.ge/s?a=1&b=2&c="),
        # the site root
        ("https://ex.ge/", "https://ex.ge"),
        ("https://ex.ge", "https://ex.ge"),
        # scheme and www are kept: they may be different sites
        ("http://www.ex.ge/a", "http://www.ex.ge/a"),
    ],
)
def test_normalize(raw, expected):
    assert normalize_url(raw) == expected


def test_one_encoding_for_unicode_paths():
    raw = "https://bm.ge/category/ტექნოლოგიები"
    encoded = "https://bm.ge/category/%e1%83%a2%e1%83%94%e1%83%a5%e1%83%9c%e1%83%9d%e1%83%9a%e1%83%9d%e1%83%92%e1%83%98%e1%83%94%e1%83%91%e1%83%98"
    assert normalize_url(raw) == normalize_url(encoded)
    assert "%E1%83%A2" in normalize_url(raw)


def test_spaces_in_paths_are_encoded():
    assert normalize_url("https://tsu.ge/ka/news/some slug ") == "https://tsu.ge/ka/news/some%20slug"


def test_idempotent():
    for url in ("https://ex.ge/a/1/?utm_source=x&b=2&a=1#f", "https://bm.ge/category/ტექ", "https://ex.ge/q?x=a+b"):
        once = normalize_url(url)
        assert normalize_url(once) == once


@pytest.mark.parametrize(
    "bad", ["ftp://ex.ge/a", "/relative/path", "mailto:a@example.org", "javascript:void(0)", "https://"]
)
def test_not_http_fails(bad):
    with pytest.raises(UrlError):
        normalize_url(bad)


# ---- prefer_canonical ------------------------------------------------------------------


def test_canonical_is_preferred_and_normalized():
    assert (
        prefer_canonical("https://ex.ge/a/1?utm_source=x", "https://ex.ge/news/one/?utm_medium=y")
        == "https://ex.ge/news/one"
    )


def test_relative_canonical_is_resolved():
    assert prefer_canonical("https://ex.ge/a/1", "/news/one") == "https://ex.ge/news/one"


def test_front_page_canonical_is_ignored():
    # A CMS that points every article at the home page would make them all one URL.
    assert prefer_canonical("https://ex.ge/a/1", "https://ex.ge/") == "https://ex.ge/a/1"
    assert prefer_canonical("https://ex.ge/", "https://ex.ge/") == "https://ex.ge"


@pytest.mark.parametrize("canonical", [None, "", "   ", "ftp://ex.ge/x", "javascript:void(0)"])
def test_missing_or_unusable_canonical_falls_back(canonical):
    assert prefer_canonical("https://ex.ge/a/1/", canonical) == "https://ex.ge/a/1"


def test_cross_site_canonical_is_kept():
    # Syndicated copies point at the original; that's the de-dup key we want.
    assert prefer_canonical("https://mirror.ge/x", "https://original.ge/story") == "https://original.ge/story"


# ---- sources registry ------------------------------------------------------------------

GOOD = """
- id: one
  name: One
  topic: education
  geography: georgia
  lang: ka
  type: listing
  url: https://one.ge/news
  selector: ".news a"
  priority: 1
  enabled: true
  notes: ""
- id: two
  name: Two
  topic: tech
  geography: global
  lang: en
  type: rss
  url: https://two.com/feed
  text_from: feed
  page_dates: false
  priority: 3
  enabled: false
  notes: "Disabled: blocks bots."
"""


def write(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_registry_loads(tmp_path):
    one, two = load_sources(write(tmp_path, GOOD))
    assert (one.id, one.type, one.selector, one.text_from, one.page_dates) == (
        "one",
        "listing",
        ".news a",
        "article",
        True,
    )
    assert (two.enabled, two.text_from, two.page_dates, two.priority) == (False, "feed", False, 3)


@pytest.mark.parametrize(
    "change, message",
    [
        (("priority: 1", "priority: 4"), "priority"),
        (("topic: education", "topic: sports"), "topic must be one of"),
        (('  selector: ".news a"\n', ""), "needs a selector"),
        (('selector: ".news a"', 'selector: "a[href"'), "doesn't compile"),
        (("id: two", "id: one"), "duplicate id"),
        (("id: two", "id: Two-Bad"), "snake_case"),
        (("url: https://one.ge/news", "url: one.ge/news"), "http"),
        (("enabled: true", "enabled: yes please"), "enabled"),
        (('notes: "Disabled: blocks bots."', 'notes: ""'), "needs notes"),
        (('notes: "Disabled: blocks bots."', 'notes: "Disabled \u2014 blocks bots."'), "em/en dash"),
        (("  text_from: feed\n", "  text_from: feed\n  colour: red\n"), "unknown keys"),
        (("  type: rss\n", "  type: rss\n  selector: a\n"), "listing sources only"),
        (("  type: rss\n", "  type: rss\n  date_selector: p\n"), "listing sources only"),
        (('  selector: ".news a"\n', '  selector: ".news a"\n  date_selector: "p["\n'), "doesn't compile"),
        (('  selector: ".news a"\n', '  selector: ".news a"\n  min_words: 0\n'), "min_words"),
        (('  selector: ".news a"\n', '  selector: ".news a"\n  min_words: "40"\n'), "min_words"),
        (("  text_from: feed\n", "  text_from: feed\n  page_date_selector: time\n"), "needs article pages"),
    ],
)
def test_registry_errors(tmp_path, change, message):
    old, new = change
    assert old in GOOD
    with pytest.raises(SourceRegistryError, match=message):
        load_sources(write(tmp_path, GOOD.replace(old, new, 1)))


def test_date_keys_and_min_words_load(tmp_path):
    text = GOOD.replace(
        '  selector: ".news a"\n',
        '  selector: ".news a"\n  date_selector: ".news_date"\n  page_date_selector: "time"\n  min_words: 40\n',
    )
    one = load_sources(write(tmp_path, text))[0]
    assert (one.date_selector, one.page_date_selector, one.min_words) == (".news_date", "time", 40)


JSON_ENTRY = """
- id: api
  name: Api
  topic: education
  geography: georgia
  lang: ka
  type: json
  url: https://api.example.ge/news
  headers: {Accept-Language: ka}
  json: {items: data, url_template: "https://example.ge/a/{id}", title: title, date: created_at, text: content}
  priority: 2
  enabled: true
  notes: ""
"""


def test_json_source_loads(tmp_path):
    (api,) = load_sources(write(tmp_path, JSON_ENTRY))
    assert api.json["url_template"] == "https://example.ge/a/{id}" and api.headers == {"Accept-Language": "ka"}
    assert api.item_text


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("  json: {items: data, ", "  jsn: {items: data, ", "unknown keys"),
        ('url_template: "https://example.ge/a/{id}"', "colour: red", "unknown json keys"),
        ('url_template: "https://example.ge/a/{id}"', "url_template: x, url: y", "exactly one"),
        ("items: data, ", "", "json needs items"),
        ("{Accept-Language: ka}", "{Cookie: session=1}", "headers may only set"),
        ("  type: json\n", "  type: rss\n", "json is for json sources only"),
        ("  priority: 2\n", "  priority: 2\n  text_from: article\n", "not text_from"),
    ],
)
def test_json_source_errors(tmp_path, old, new, message):
    assert old in JSON_ENTRY
    with pytest.raises(SourceRegistryError, match=message):
        load_sources(write(tmp_path, JSON_ENTRY.replace(old, new, 1)))


def test_feed_text_needs_rss(tmp_path):
    text = GOOD.replace('  selector: ".news a"\n', '  selector: ".news a"\n  text_from: feed\n')
    with pytest.raises(SourceRegistryError, match="needs type: rss"):
        load_sources(write(tmp_path, text))


def test_the_real_registry_is_valid():
    sources = load_sources()
    assert any(s.enabled for s in sources)
    for s in sources:
        assert s.enabled or s.notes, s.id
