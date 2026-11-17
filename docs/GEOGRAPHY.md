# Geography

NEWSWIRE records **where** a report is from and **what places** it is about. It works these out
from text and metadata with fixed rules and keeps the different meanings apart. It never guesses
beyond what the report or its feed states. Unknown stays unknown.

## Roles

| Role | Source | Example |
|---|---|---|
| `publisher` | the source's configured country/region/city | an outlet based in the UK |
| `event` | a wire-style dateline at the start of the summary or content | `DHAKA, Nov 17 (Reuters) -` |
| `article` | location fields, geo tags or place categories the feed supplies | `<category>Bangladesh</category>` |
| `mentioned` | a place named in the headline or summary | "…talks in Geneva…" |
| `editor` | set by hand on a news item | |

Each row stores the place, the role, the method (`source_metadata`, `dateline`, `feed_metadata`,
`feed_category`, `text_match`), a confidence between 0 and 1, and the original text it came from.
The news detail panel shows all of them.

**Publisher location is never treated as the event location.** It is excluded from assignment
geography matching and from Analytics' *Coverage by country* unless you switch
`geo_include_publisher` on.

## Confidence

| Evidence | Confidence |
|---|---|
| publisher location from source settings | 1.0 |
| feed location field naming exactly one place | 0.9 (0.5 if it could be several) |
| country named in the headline / summary | 0.9 / 0.75 |
| country only as a demonym ("Kenyan") in headline / summary | 0.65 / 0.55 |
| city in a country the report also names | 0.85 |
| capital city on its own | 0.65 |
| other city on its own | 0.55 |
| dateline naming one place | 0.8 |
| name shared by several places (e.g. "Georgia") without a disambiguating country | 0.3 |
| place name that is also a common word ("Nice", "Reading", "Split") | at most 0.45, and only when capitalised |

Assignment matching ignores places below `geo_min_confidence` (default 0.5). A place matches an
assignment's target when it is that place or lies inside it: a report about Chattogram counts for
an assignment targeting Bangladesh or Asia.

## The gazetteer

The built-in gazetteer (`backend/app/geography/data/countries.psv`) holds 7 continents, 202
countries and territories with their ISO codes, common alternative names and demonyms, and 464
capitals and major cities with well-known alternative spellings (Mumbai/Bombay, Kyiv/Kiev). It is
compiled from public factual information and loaded into the database on first start.

Short codes are case-sensitive (`US`, `UK` match only in capitals). ISO-3 codes are not used as
aliases, because all-capital headlines would collide with ordinary words.

### Adding GeoNames (optional, offline)

For regions and smaller towns, import the free [GeoNames](https://www.geonames.org/) dumps.
Download them yourself; NEWSWIRE never downloads them automatically:

* `admin1CodesASCII.txt` (first-level regions)
* one of `cities15000.zip`, `cities5000.zip` or `cities1000.zip` (unzip it)

```bash
python -m app.cli import-geonames admin1CodesASCII.txt cities15000.txt --min-population 15000
```

The import is idempotent. It links existing built-in cities to their GeoNames IDs instead of
duplicating them, and adds regions and cities under the right country. A larger gazetteer finds
more places but also more ambiguous names, so start with `cities15000`.

GeoNames data is licensed **CC BY 4.0**. If you import it and show locations publicly, credit
"GeoNames (geonames.org)".

### Custom areas

A custom area is a named group of places that the gazetteer does not have as one unit, such as
"Sahel" (Mali, Niger, Burkina Faso, Chad). Assignments
can target it like any other place: a report matches when it names any member, or anything
inside a member. Areas are created through `POST /api/v1/geography/areas` (`assignments:write`)
and removed with `DELETE /api/v1/geography/areas/{id}`. Built-in and GeoNames places cannot be
deleted. Changes reach the matcher immediately in the process that made them, and within five
minutes in the worker.
