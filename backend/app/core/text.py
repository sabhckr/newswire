"""Deterministic text normalisation shared by matching, dedup, geography and clustering.

Nothing here understands meaning; it only makes string comparison robust to case, accents,
punctuation and simple English inflection.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_QUOTES = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        " ": " ",
    }
)
_WS = re.compile(r"\s+")


def _strip_latin_marks(text: str) -> str:
    """Remove accents from Latin letters only; combining marks in other scripts carry meaning."""
    decomposed = unicodedata.normalize("NFKD", text)
    out: list[str] = []
    prev_ascii = False
    for ch in decomposed:
        if unicodedata.combining(ch):
            if prev_ascii:
                continue
            out.append(ch)
            continue
        out.append(ch)
        prev_ascii = ch.isascii()
    return unicodedata.normalize("NFC", "".join(out))


def fold(text: str | None) -> str:
    """Case-fold, unify quotes/dashes, strip Latin accents and collapse whitespace."""
    if not text:
        return ""
    text = text.translate(_QUOTES)
    text = _strip_latin_marks(text).casefold()
    return _WS.sub(" ", text).strip()


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N", "M")


def tokens(text: str | None, *, folded: bool = False) -> list[str]:
    """Split into word tokens (letters, digits, and combining marks of any script)."""
    if not text:
        return []
    s = text if folded else fold(text)
    out: list[str] = []
    buf: list[str] = []
    for ch in s:
        if _is_word_char(ch):
            buf.append(ch)
        elif ch == "'" and buf:
            # keep possessive/elision boundaries simple: "bangladesh's" -> "bangladesh", "s"
            out.append("".join(buf))
            buf = []
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def stem(token: str) -> str:
    """Very light English plural stripping for ASCII tokens; other scripts unchanged."""
    if not token.isascii() or len(token) <= 3 or token.isdigit():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("ches", "shes", "xes", "sses", "zes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def stems(toks: list[str]) -> list[str]:
    return [stem(t) for t in toks]


def contains_sequence(haystack: list[str], needle: list[str]) -> bool:
    """True if `needle` appears as a contiguous run inside `haystack`."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    first = needle[0]
    for i in range(len(haystack) - n + 1):
        if haystack[i] == first and haystack[i : i + n] == needle:
            return True
    return False


STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before
    being below between both but by can could did do does doing down during each few for from
    further had has have having he her here hers herself him himself his how i if in into is it
    its itself just me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your yours yourself yourselves
    s t says said say new news live update updates latest video watch photos report reports
    reported today yesterday tomorrow week year years day days amid via per vs one two three
    first last after before over under more less make makes made get gets got set sets
    """.split()
)


def significant_tokens(text: str | None) -> set[str]:
    """Stemmed tokens without stopwords or bare numbers; used for overlap comparisons."""
    out: set[str] = set()
    for t in tokens(text):
        if t in STOPWORDS or t.isdigit():
            continue
        if t.isascii() and len(t) < 3:
            continue
        out.add(stem(t))
    return out


def content_hash(*parts: str | None) -> str:
    joined = "\n".join(fold(p)[:2000] for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def slugify(text: str, max_len: int = 80) -> str:
    toks = tokens(text)
    slug = "-".join(t for t in toks if t.isascii()) or "article"
    return slug[:max_len].strip("-") or "article"


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
