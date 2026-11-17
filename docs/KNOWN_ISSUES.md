# Known issues and limitations

Honest list of what is untested, incomplete or limited by design in 0.10.0. Each entry says what
to do about it.

## Untested

| Area | Status | Workaround / next step |
|---|---|---|
| Docker images and `docker-compose.yml` | Written but **never built**: the development machine has no Docker. The bare-metal path (PostgreSQL 17, Python 3.13, Node) is what has been run and tested. | Run `docker compose up --build` once on a machine with Docker and fix anything that fails before relying on it. |
| Caddy, Nginx and systemd configs | Not exercised. | Treat as templates. |
| Pages outside the main browser flows | The Playwright suite (`frontend/e2e/`, Chromium only, SQLite) covers setup, sources, headlines, research, the article editor, publishing to the built-in API and unpublishing, plus a 375 px overflow check of every sidebar page and theme switching. The Image Library, WordPress and webhook destinations, users and roles, source import, scheduled publishing and the worker's own polling are covered by API tests only. | Extend `frontend/e2e/` when changing those pages. |
| WordPress connector | Tested against a mocked REST API, not a live WordPress site. | Use *Test connection* and *Publish as draft* first. |
| Facebook and YouTube connectors | Written to the documented Graph API v23.0 and YouTube Data API v3 and tested against in-process fakes of those APIs, **not against real Facebook or Google accounts**. The sign-in redirect to Facebook was checked in a browser. | Connect a test Page or channel and publish one article with *Post*, one *Reel* and one *Unlisted* YouTube upload before relying on them. |
| Openverse / Wikimedia Commons | Parsers tested on recorded response shapes; live calls depend on those services. | Search returns a clear provider error when they are unavailable. |

## Limitations by design

* **Facebook:** Pages only (Facebook does not let apps post to personal profiles or, since 2024,
  to groups). No drafts; *Unpublish* deletes the post. Reels must be vertical and 3–90 seconds.
* **YouTube:** unaudited Google projects upload as private, and the free quota allows about six
  uploads a day. A Google project left in *Testing* disconnects after 7 days.
* **Videos are not converted.** NEWSWIRE checks the MP4/MOV container (size, length,
  orientation) but does not transcode; a codec a platform rejects is reported by that platform.

* **Lexical matching only.** NEWSWIRE matches words and phrases, not meaning. Synonyms must be
  entered by hand. Stemming is light: English plurals and Bangla case endings and plural markers
  ([MATCHING.md](MATCHING.md#bangla)). Stop words exist for English and Bangla. Other languages
  match on exact phrases but cluster less well. A Bangla report and an English report of the same
  story are never linked automatically.
* **Bangla suffix rules are a fixed list, not a dictionary.** A few words that end like a case
  ending match a shorter word (`আদায়` also matches `আদা`). Add an *excluded* phrase if one
  causes false matches.
* **Full-text search** uses PostgreSQL's `simple` configuration. Bangla words are matched by
  prefix on the word and its stem, so `ঢাকা` finds `ঢাকায়`; a prefix can also find a longer,
  unrelated word that starts the same way. English words are not stemmed (`strike` does not
  find `strikes`). PostgreSQL does not index Bangla digits (search `2026`, not `২০২৬`), and it
  splits a Bangla word at a zero-width joiner (`র‍্যাব`), so such words are found only in part.
  Phrases in quotes and `OR` work for English queries, not for queries containing Bangla.
  Assignment matching does not have these limits. On SQLite, search falls back to substring
  matching.
* **Built-in gazetteer** covers countries, about 460 major cities and, for Bangladesh, all
  divisions and districts plus 28 towns. Upazilas, unions and neighbourhoods (Mirpur, Gulshan)
  are not included; elsewhere, regions and small towns need the optional GeoNames import
  ([GEOGRAPHY.md](GEOGRAPHY.md)). Place names shared by several places get low confidence unless
  the report also names the country.
* **Bangla place names that are also ordinary words** are handled only for `ঢাকা` ("covered").
  Others, such as `ভোলা` (also "to forget"), can occasionally be read as a place.
* **Rate limiting** is per API process, in memory. With several API replicas, each counts on
  its own. Put a reverse-proxy limit in front for multi-replica deployments.
* **Single worker on SQLite.** SQLite has no `SKIP LOCKED`. Use PostgreSQL if you run more than
  one worker.
* **WordPress mapping** sends title, content, excerpt, slug and featured image (with caption and
  credit). NEWSWIRE categories and tags are not mapped to WordPress taxonomies.
* **Webhook unpublish** sends `article.unpublished`; removing the article is up to the
  receiving site.
* **Analytics** reports count days in the viewer's time zone. On SQLite (development only) the
  zone's current UTC offset is used for the whole period, so a daylight-saving change inside the
  period shifts buckets by an hour. Bangladesh has no daylight saving.
* **Plain sitemaps** (without Google News tags) carry no headlines. Their entries are only
  collected when page metadata lookup is on: at most 10 new pages per fetch, only entries from
  the last two days (by the sitemap's `lastmod`), only pages that look like stories (og:type
  `article`, a publication time, or a story number in the address). A busy site can publish
  more than 10 stories between two fetches; the rest are picked up on later fetches while they
  are under two days old. A story page with none of those signs is skipped.
* **The web UI's API address is fixed at build time** (`NEWSWIRE_BACKEND_URL`, Next.js
  rewrites). Rebuild the web image if the API moves.
* **Restore** is CLI-only and expects you to stop the API and worker first.

## Bangladeshi sources

* **Some major papers cannot be monitored.** When the pack was built (17 November 2026), Kaler
  Kantho, Bangladesh Pratidin, Kalbela, Jagonews24, RisingBD, Daily Sun, Jaijaidin, Manobkantha
  and Somoy News put a bot check in front of their feeds or robots.txt, and Protidiner Sangbad,
  RTV, Bangladesh Journal and Dhaka Times refused feed requests (403). NEWSWIRE does not get
  around such checks. Naya Diganta's sitemaps stopped on 8 November 2026. The Financial Express,
  Bonik Barta, Alokito Bangladesh and DBC News list only section pages in their sitemaps; UNB,
  Bangladesh Post, The Asian Age, Ajkaler Khobor, The Daily Messenger and Dhaka Courier offered
  no feed or sitemap at all. Daily Sangram, BTV, Deepto TV, The Independent and some regional
  papers could not be reached. Try *Find feeds* again later; publishers change their sites.
  Rechecked on 17 November 2026 with the fixed robots.txt handling: Daily Sangram's robots.txt
  is readable (its first answer is slow; the retry now gets it), BTV serves a self-signed
  certificate, The Independent timed out, and Bangla Insider's DNS points at 127.0.0.1.
* **Full article text** depends on the page: video and photo stories, live pages and text that
  only appears after JavaScript runs have none to read. Ads or "read more" lines between
  paragraphs occasionally come through; edit them out in the draft.
* **Feeds change.** The pack was verified on one day. A feed that moves or stops shows up as a
  failing source on the Sources page and in System → failing sources.
* **Feed timestamps.** Several Bangladeshi feeds publish Dhaka time labelled as UTC, or no time
  zone at all, which puts a new story up to six hours in the future. For sources whose country
  is Bangladesh (or another country with a fixed offset ahead of UTC) such times are moved back
  by the offset (the time as given is kept as `published_at_as_given`). Other future times are
  set aside (`rejected_published_at`) and the report is ordered by when it was collected. A
  local-time label on a story more than six hours old looks like a valid past time and cannot be
  detected.
* **News-sitemap sources have headlines only**, no summaries, so keyword matching sees less
  text for them. 20 of the 68 pack feeds are news sitemaps because the publisher has no working
  RSS. The six plain-sitemap sources (Manab Zamin, Amader Shomoy, New Age, The Daily Campus,
  Dainik Shiksha, Daily Karatoa) get headline and description from each story page, one extra
  request per new story.

## Not built

* Email delivery of notifications (in-app only).
* Single sign-on (SAML/OIDC) and two-factor authentication.
* Paywalled or login-protected sources. By policy, these will not be added.
