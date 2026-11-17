"""Deterministic text normalisation shared by matching, dedup, geography and clustering.

Nothing here understands meaning; it only makes string comparison robust to case, accents,
punctuation, zero-width joiners, Bangla digits, simple English plurals and common Bangla
case/plural suffixes (see `forms` and `bn_stem`).
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
# Zero-width joiners shape Bangla conjuncts (e.g. র‍্যাব) but carry no meaning for matching.
# Zero-width (non-)joiners and spaces, BOM, soft hyphen, word joiner and the left-to-right /
# right-to-left marks some CMSs put in front of headlines.
_INVISIBLE = str.maketrans({
    "\u200c": None, "\u200d": None, "\u200b": None, "\ufeff": None, "\u00ad": None,
    "\u2060": None, "\u200e": None, "\u200f": None,
})
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_KHANDA_TA_OLD = "\u09a4\u09cd\u200d"  # ta + hasanta + ZWJ, the pre-Unicode-4.1 spelling of ৎ
_BN_RANGE = ("\u0980", "\u09ff")


def is_bangla(token: str) -> bool:
    return any(_BN_RANGE[0] <= ch <= _BN_RANGE[1] for ch in token)


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
    text = text.translate(_QUOTES).replace(_KHANDA_TA_OLD, "\u09ce").translate(_INVISIBLE).translate(_BN_DIGITS)
    text = _strip_latin_marks(text).casefold()
    return _WS.sub(" ", text).strip()


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    # ZWJ/ZWNJ sit inside Bangla words; fold() removes them afterwards.
    return cat[0] in ("L", "N", "M") or ch in "\u200c\u200d"


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


# --- Bangla inflection -------------------------------------------------------------------
# Bangla attaches case, plural, determiner and emphasis markers to the noun: ঢাকা → ঢাকায়/ঢাকার,
# সরকার → সরকারের/সরকারকে, শিক্ষার্থী → শিক্ষার্থীরা/শিক্ষার্থীদের. Two deterministic tools:
#   forms(t)   every way t may be "word + known suffixes" (used for matching: overlap of forms)
#   bn_stem(t) one canonical stem using only unambiguous suffixes (used for clustering overlap)
# Lists are folded at import so they use exactly the same normalisation as the text.
_BN_SUFFIXES = tuple(sorted({fold(x) for x in (
    "গুলোর", "গুলির", "গুলোকে", "গুলিকে", "গুলোতে", "গুলিতে", "গুলো", "গুলি", "গুলা",
    "দেরকে", "দের", "েরা", "রা", "টির", "টার", "টিকে", "টাকে", "টিতে", "টাতে", "টি", "টা",
    "খানা", "খানি", "সমূহের", "সমূহ", "য়ের", "ের", "এর", "র", "কে", "রে", "তে", "েতে", "য়",
    "ে", "ই", "ও",
)}, key=len, reverse=True))
_BN_STRONG = tuple(sorted({fold(x) for x in (
    "গুলোর", "গুলির", "গুলোকে", "গুলিকে", "গুলোতে", "গুলিতে", "গুলো", "গুলি",
    "দেরকে", "দের", "েরা", "টির", "টার", "টিকে", "টাকে", "টি", "টা", "সমূহের", "সমূহ",
    "য়ের", "ের", "কে", "তে",
)}, key=len, reverse=True))
_BN_VOWEL_SIGNS = {chr(c) for c in range(0x09BE, 0x09CD)} | {"\u09d7"}
_BN_LOCATIVE_Y = fold("য়")


def _bn_base_ok(base: str) -> bool:
    """A stripped base must keep at least three code points and two letters (কর ≠ করে)."""
    letters = sum(1 for ch in base if unicodedata.category(ch) == "Lo")
    return len(base) >= 3 and letters >= 2 and not base.endswith("\u09cd")


def _bn_strip_once(token: str, suffixes: tuple[str, ...]) -> list[str]:
    return [token[: -len(suf)] for suf in suffixes if token.endswith(suf) and _bn_base_ok(token[: -len(suf)])]


def bn_forms(token: str) -> frozenset[str]:
    """The token plus every base reachable by removing up to two known suffixes."""
    out = {token}
    frontier = [token]
    for _ in range(2):
        nxt = []
        for t in frontier:
            for base in _bn_strip_once(t, _BN_SUFFIXES):
                if base not in out:
                    out.add(base)
                    nxt.append(base)
        frontier = nxt
    return frozenset(out)


_BN_E = fold("ে")


def _bn_stem_once(token: str) -> str:
    for suf in _BN_STRONG:
        if token.endswith(suf) and _bn_base_ok(token[: -len(suf)]):
            return token[: -len(suf)]
    if token.endswith(_BN_LOCATIVE_Y):  # ঢাকায় -> ঢাকা (only after a vowel sign: সময় stays)
        base = token[: -len(_BN_LOCATIVE_Y)]
        if base and base[-1] in _BN_VOWEL_SIGNS and _bn_base_ok(base):
            return base
    if token.endswith(_BN_E) and _bn_base_ok(token[:-1]):  # চট্টগ্রামে -> চট্টগ্রাম
        return token[:-1]
    return token


def bn_stem(token: str) -> str:
    """Canonical stem for overlap comparisons: strip up to two suffixes, deterministically.

    It only has to be consistent (ছেলে, ছেলের, ছেলেকে all give ছেল), not linguistically exact."""
    once = _bn_stem_once(token)
    return _bn_stem_once(once) if once != token else once


def forms(token: str) -> frozenset[str]:
    """Comparison forms of a folded token. Two tokens match when their forms overlap."""
    if token.isascii():
        return frozenset((stem(token),))
    if is_bangla(token):
        return bn_forms(token)
    return frozenset((token,))


def forms_list(toks: list[str]) -> list[frozenset[str]]:
    return [forms(t) for t in toks]


def contains_forms(haystack: list[frozenset[str]], needle: list[frozenset[str]]) -> bool:
    """Like contains_sequence, but positions match when their form sets overlap."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    for i in range(len(haystack) - n + 1):
        if all(haystack[i + j] & needle[j] for j in range(n)):
            return True
    return False


def all_forms_present(haystack: list[frozenset[str]], needle: list[frozenset[str]]) -> bool:
    """Every needle token overlaps some haystack token, in any order."""
    pool: set[str] = set().union(*haystack) if haystack else set()
    return all(pool & f for f in needle)


def stem(token: str) -> str:
    """Light English plural stripping for ASCII tokens, canonical Bangla stem, others unchanged."""
    if not token.isascii():
        return bn_stem(token) if is_bangla(token) else token
    if len(token) <= 3 or token.isdigit():
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
) | frozenset(
    # Bangla function words and newsroom boilerplate, folded like the text they are compared to.
    fold(w)
    for w in """
    এবং ও বা কিন্তু তবে যে যা যিনি যারা এ এই এটি এটা ওই সেই সে তা তার তাঁর তাদের তাঁদের তিনি তারা
    তাঁরা আমি আমরা আমাদের আপনি তুমি না নয় নেই আছে আছেন ছিল ছিলেন ছিলো হয় হয়েছে হয়েছিল হবে হচ্ছে
    হলো হল হলে হয়ে করে করা করেন করেছে করেছেন করবে করার করতে থেকে জন্য জন্যে সঙ্গে সাথে সহ পর পরে আগে
    মধ্যে মাঝে নিয়ে দিয়ে দিয়েছে দেন দেওয়া নেওয়া বলে বলেন বলেছেন জানান জানিয়েছেন জানা গেছে গেল যায় যাবে
    পারে পারেন প্রতি পর্যন্ত বিরুদ্ধে উপর ওপর মতো মত অনুযায়ী ধরে কোনো কোন সব সকল আরও আর একটি একজন
    এক দুই তিন কি কী কেন কোথায় কখন যখন তখন সেখানে এখানে আজ গতকাল আগামীকাল নতুন খবর সংবাদ ভিডিও ছবি
    লাইভ আপডেট সর্বশেষ বিস্তারিত প্রতিবেদক নিজস্ব
    """.split()
)


def bangla_share(text: str | None) -> float:
    """Share of letters in the text that are Bangla script (0.0 when there are no letters)."""
    letters = [ch for ch in (text or "") if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "\u0980" <= ch <= "\u09ff") / len(letters)


def significant_tokens(text: str | None) -> set[str]:
    """Stemmed tokens without stopwords or bare numbers; used for overlap comparisons."""
    out: set[str] = set()
    for t in tokens(text):
        if t in STOPWORDS or t.isdigit():
            continue
        if t.isascii() and len(t) < 3:
            continue
        st = stem(t)
        if st in STOPWORDS:
            continue
        out.add(st)
    return out


def content_hash(*parts: str | None) -> str:
    joined = "\n".join(fold(p)[:2000] for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def slugify(text: str, max_len: int = 80) -> str:
    """URL slug. Latin and Bangla words are kept (Bangla slugs are valid UTF-8 URLs)."""
    toks = [t for t in tokens(text) if t.isascii() or is_bangla(t)]
    slug = "-".join(toks)[:max_len].strip("-")
    return unicodedata.normalize("NFC", slug) or "article"


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
