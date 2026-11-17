import pytest

from app.core.text import content_hash, contains_sequence, fold, significant_tokens, slugify, stem, tokens
from app.core.urls import normalize_url


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://www.Example.com/news/story/?utm_source=x&b=2&a=1#frag", "https://example.com/news/story?a=1&b=2"),
        ("http://example.com:80/a//b/", "https://example.com/a/b"),
        ("https://example.com/path/index.html", "https://example.com/path"),
        ("https://EXAMPLE.com/caf%C3%A9", "https://example.com/caf%C3%A9"),
        ("https://example.com/a?fbclid=123&id=7", "https://example.com/a?id=7"),
    ],
)
def test_normalize_url_equivalences(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_keeps_meaningful_params_and_ports():
    assert normalize_url("https://example.com/a?id=1") != normalize_url("https://example.com/a?id=2")
    assert normalize_url("https://example.com:8443/a") == "https://example.com:8443/a"


@pytest.mark.parametrize("bad", ["ftp://example.com/x", "javascript:alert(1)", "", "https:///nohost"])
def test_normalize_url_rejects_bad(bad):
    with pytest.raises(ValueError):
        normalize_url(bad)


def test_fold_and_tokens():
    assert fold("  Café “Dhaka”—Rail  ") == 'cafe "dhaka"-rail'
    assert tokens("Bangladesh's new rail-link, 2026!") == ["bangladesh", "s", "new", "rail", "link", "2026"]
    # Non-Latin scripts keep their combining marks.
    assert tokens("বাংলাদেশ রেলওয়ে") == ["বাংলাদেশ", "রেলওয়ে"]


def test_stem_and_sequence():
    assert stem("railways") == "railway" and stem("cities") == "city" and stem("bus") == "bus"
    assert contains_sequence(["a", "b", "c", "d"], ["b", "c"])
    assert not contains_sequence(["a", "b", "c"], ["c", "b"])


def test_hash_is_stable_across_cosmetic_changes():
    assert content_hash("Rail Project Approved", "Text") == content_hash("rail  project approved", "TEXT")
    assert content_hash("Rail project approved", "x") != content_hash("Rail project rejected", "x")


def test_significant_tokens_drop_stopwords_and_numbers():
    assert significant_tokens("The new railway projects in 2026 says minister") == {"railway", "project", "minister"}


def test_slugify():
    assert slugify("Bangladesh approves Padma rail link!") == "bangladesh-approves-padma-rail-link"
