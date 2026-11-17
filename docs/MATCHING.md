# Matching, deduplication and clustering

Everything here is deterministic. The same report and the same settings always produce the
same result, and each decision records why it was made. Nothing uses a language model or any
other statistical model. The code says what it does: `backend/app/matching/engine.py`,
`backend/app/collectors/dedup.py`, `backend/app/clustering/engine.py`.

**What this means in practice.** NEWSWIRE matches words and phrases, not meaning. "Rail strike"
will not find "train drivers walk out" unless you add those words as synonyms. Good assignments
list the words reporters actually use.

## Text normalisation

Before any comparison, headlines, summaries and keywords are:

1. Unicode-normalised, lower-cased, with accents removed (`São Paulo` → `sao paulo`) and curly
   quotes and dashes unified.
2. Split into word tokens; punctuation (including the Bangla danda `।`) separates words and is
   otherwise ignored.
3. For the *inflection* level, lightly stemmed: plural and common suffix stripping
   (`railways` → `railway`, `strikes` → `strike`). This is not a full stemmer, by design.

### Bangla

Bangla (Bengali) text gets the same treatment, adapted to the script:

* **Spelling variants that look identical are made identical.** Zero-width joiners and
  non-joiners are removed, so `র‍্যাব` typed with either matches the other. Bangla digits count as
  ASCII digits (`২০২৬` = `2026`). The old form of khanda-ta (`ত্‍`) becomes `ৎ`. Vowel signs and
  conjuncts are kept exactly.
* **Case endings and plural markers are stripped for the inflection level.** A keyword matches
  its inflected forms: `ঢাকা` finds `ঢাকায়` and `ঢাকার`; `পদ্মা সেতু` finds `পদ্মা সেতুর`; `সরকার`
  finds `সরকারের` and `সরকারকে`; `শিক্ষার্থী` finds `শিক্ষার্থীদের` and `শিক্ষার্থীরা`. At most two
  endings are removed and at least two letters must remain, so short words are left alone (`কর`
  does not match `করে`). The rules are a fixed suffix list, not a dictionary. A few words that
  happen to end like a suffix can match a shorter word (`আদায়` also matches `আদা`); use an
  *excluded* phrase if that matters for an assignment.
* **Stop words.** Common Bangla function words (`এবং`, `ও`, `থেকে`, `আজ`, `করে`, …) are ignored
  by clustering and by the significant-word counts, as English ones are.
* **Bangla and English versions of a story are not linked.** Matching is lexical, so an
  assignment needs keywords in both languages (`Padma Bridge` and `পদ্মা সেতু`) to catch both,
  and clustering keeps the two languages in separate clusters.

## Relevance score

When a report arrives, it is scored against every active assignment. The score is the sum of
the components below. The report matches when the total reaches the assignment's
**minimum score** (default 25).

### Gates (reject before scoring)

| Gate | Rejects when |
|---|---|
| Date range | The report is outside the assignment's start/end dates |
| Sources | *Only selected sources* is on and the report came from elsewhere |
| Language | The assignment has a language and the feed declares a different one (reports without a language pass) |
| Excluded phrase | An excluded phrase appears as an exact or inflected match (or, with `excluded_mode=penalty`, subtract points instead) |
| Required phrase | Any required phrase is missing (exact or inflected only) |
| Keywords | Nothing matched, or only secondary keywords matched when primaries/synonyms exist |
| Geography | *Require location* is on and no matching place was found |

### Keyword levels

The best level wins for each keyword. Matching in the summary rather than the headline
multiplies the points by `body_factor` (0.75).

| Level | Rule | Multiplier |
|---|---|---|
| exact | the phrase appears word for word | 1.0 |
| inflection | it appears after light stemming | 0.9 |
| tokens | all words of a multi-word phrase appear, in any order | 0.6 |
| fuzzy | RapidFuzz partial ratio ≥ 88 for phrases of 5+ characters (catches spelling variants) | 0.5 |

### Points (defaults; change under *Settings → Matching & scoring*)

| Component | Points |
|---|---|
| Required phrase | 30 × level multiplier |
| Primary keyword | 15 × multiplier, per keyword |
| Synonym | 12 × multiplier |
| Secondary keyword | 7 × multiplier |
| Geography | 20 when a place with an *event*, *article*, *mentioned* or editor role, with confidence ≥ 0.5, falls inside one of the assignment's places (a city counts for its country and continent) |
| Category | 10 when the source's category or the feed's category matches |
| Selected publisher | 10 when the report came from one of the assignment's sources |
| Recency | up to 15, falling linearly to 0 over 72 hours |
| Language | 5 when the declared language matches |
| Excluded (penalty mode) | −50 |

Each keyword can override its weight. Publisher location is excluded from the geography
component by default: an outlet based in London is not evidence that a story is about London.

### Example

Assignment *"Bangladesh garment factory safety"*: primary `garment factory`, synonym `textile
mill`, secondary `safety`, place Bangladesh, minimum 25.

Report: headline *"Fire at Dhaka garment factories kills 12"*, published 3 hours ago.

| Component | Detail | Points |
|---|---|---|
| primary `garment factory` | headline, inflection | 15 × 0.9 = 13.5 |
| geography Dhaka | mentioned in text, confidence 0.65 (a capital city), inside Bangladesh | 20 |
| recency | 3 h ago | 15 × (1 − 3/72) = 14.4 |
| **total** | | **47.9 → matched** |

The research workspace shows this as: *Matched because: garment factory, Dhaka, published 3 h ago. Score 47.9.*

### Review loop

Matches start *unreviewed*. Mark them *verified* or *rejected*. *Headline performance* in
Analytics shows precision (verified ÷ reviewed) for each assignment. When precision is low, add
excluded phrases or raise the minimum score. Changing an assignment's rules re-scores recent
reports (`backfill_days`, default 7), so you can tune and watch the result.

## Deduplication

| Level | Rule | Result |
|---|---|---|
| 1 | Same normalised URL (scheme and host lower-cased; fragment, trailing slash and tracking parameters such as `utm_*` and `fbclid` removed; query sorted) | Same record. The extra source is linked to it (enforced by a unique index) |
| 2 | Same canonical URL (from the feed, or from the page if the source allows metadata extraction) | Same record |
| 3 | Same content hash of normalised headline + summary within 30 days | New record flagged *duplicate of* the first (a syndicated copy) |
| 4 | Headline similarity ≥ 95 (RapidFuzz) within 72 hours, for headlines of 5+ words | New record flagged *duplicate of* |
| 5 | Shared keywords and metadata | Not a duplicate: grouped as related coverage by clustering |

Flagged duplicates stay visible (the *Duplicates* filter in Live News hides or isolates them) and are counted separately in
Analytics, so you can see how widely a story was syndicated.

## Story clustering

A new report joins an existing cluster from the last 48 hours when it is related to one of the
cluster's reports by any of these rules:

* **duplicate:** it is a flagged duplicate of a cluster member;
* **headline:** headlines are near-identical after removing stop words (token-set ratio ≥ 85)
  and share at least 3 significant words covering ≥ 60 % of the shorter headline;
* **keywords:** headline + summary share at least 4 significant words with a Jaccard overlap
  ≥ 0.30, **and** either the headlines are loosely similar (≥ 50) or both reports name the same
  country.

Bangla headline words are compared by their stem (`চট্টগ্রামে` and `চট্টগ্রামের` count as the
same word). A single shared keyword never links two reports. If no cluster fits, the report starts a new one
once a second related report arrives. Editors can rename clusters, merge them, move reports out
and lock a cluster against automatic changes.
