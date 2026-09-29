# Project: Audi A6 C5 Multi-Source Monitoring Service

## Architecture
A modular, production-ready asynchronous Python service for continuous multi-source monitoring of Audi A6 C5 (1.8T, 2.4 petrol/LPG, 1.9 TDI) listings across 5 platforms (AUTO.RIA, OLX, RST.ua, Telegram, Instagram), storing in PostgreSQL with 3-level deduplication, alerting via Telegram with media groups, and automated via GitHub Actions.

### Data Flow
1. **Parsers** (`src/parsers/`): `BaseParser` subclasses asynchronously stream `RawListingPayload` from AUTO.RIA, OLX, RST, Telegram (Telethon), and Instagram into the pipeline. Each parser operates in total isolation; network or scraping failures on one source do not halt others.
2. **Normalizer & Filter** (`src/filtering/`): Raw payloads are normalized (homoglyphs, whitespace, case) and evaluated through the Audi A6 C5 Filtering Engine:
   - Negative filters eliminate non-sale listings (parts, dismantlers, wanted ads, "обмен на Audi").
   - Model & generation checks strictly enforce Audi A6 C5 / 4B (1997–2005) and reject other models/generations (A4, A8, C4, C6).
   - Engine validator strictly permits 1.8T, 2.4, and 1.9 TDI while rejecting all others (2.5 TDI, 2.7T, 2.8, etc.).
   - Ambiguous listings receive `NEEDS_REVIEW`.
3. **Storage & Deduplication** (`src/database/`, `src/services/deduplicator.py`): Listings passing filter are stored in PostgreSQL using SQLAlchemy 2.0 Async / asyncpg. 3-level deduplication prevents duplicate alerting:
   - Level 1: `(source_id, source_listing_id)`
   - Level 2: Normalized Canonical URL
   - Level 3: SHA-256 Content Fingerprint (year, mileage bucket, engine, city, seller)
   - Price changes are logged in `listing_versions` and flagged for price-drop alerts.
4. **Notifier** (`src/notifier/`): Unsent listings are locked and dispatched to Telegram via Telegram Bot API with media group support (albums of up to 10 photos), rich Ukrainian formatting, and 429/FloodWait backoff.
5. **Orchestrator Runner & CI/CD** (`src/runner.py`, `.github/workflows/parser.yml`): Runs locally or on scheduled GitHub Actions (08:00, 14:00, 20:00 UTC), logging run metrics in `parser_runs`.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Unified Listing Model | Pydantic v2 `Listing` and `RawListingPayload` models with full field validation, datetime parsing, and JSON serialization | M1 | Survey 1, ORIGINAL_REQUEST §R1 |
| 2 | PostgreSQL DDL & Models | SQLAlchemy Async models for `sources`, `listings`, `listing_versions`, and `parser_runs` with indexes and constraints | M1 | Survey 1, ORIGINAL_REQUEST §R3 |
| 3 | 3-Level Deduplication | Hierarchical deduplication: (source_id, source_listing_id) -> canonical URL -> SHA-256 content fingerprint | M1 | Survey 1, ORIGINAL_REQUEST §R3 |
| 4 | State & Version Tracking | Tracking `first_seen_at`, `last_checked_at`, `is_sent_to_telegram`, and historical price drop snapshots | M1 | Survey 1, ORIGINAL_REQUEST §R3 |
| 5 | Config & Settings Management | `config/config.yaml` schema with Pydantic v2 Settings and environment overrides | M1 | Survey 3, ORIGINAL_REQUEST §R5 |
| 6 | Homoglyph & Text Normalization | Cyrillic/Latin token harmonization (`[cс]5`, `[aа]6`, `[tт]`, `4[bв]`), whitespace collapse, case folding | M2 | Survey 2, ORIGINAL_REQUEST §R2 |
| 7 | Contextual Negative Filtering | Regex elimination of parts, wrecker dismantlers, buyer wanted ads, and directional barter ("обмен на Audi") | M2 | Survey 2, ORIGINAL_REQUEST §R2 |
| 8 | Generation & Model Verification | Strict validation of Audi A6 C5 / 4B (1997–2005) and rejection of A4, A8, C4, C6, C7 | M2 | Survey 2, ORIGINAL_REQUEST §R2 |
| 9 | Target Engine Verification | Whitelist for 1.8T, 2.4 petrol/LPG, 1.9 TDI; strict blacklist for 2.5 TDI, 2.7T, 2.8, 3.0, 4.2, 2.0 ALT, 1.8 ADR | M2 | Survey 2, ORIGINAL_REQUEST §R2 |
| 10 | Decision State Machine | 3-state evaluation (`PASS`, `REJECT`, `NEEDS_REVIEW`) with confidence scores and descriptive reason codes | M2 | Survey 2, ORIGINAL_REQUEST §R2 |
| 11 | BaseParser Streaming ABC | Asynchronous generator streaming contract with error containment, timeout, retry, and connection pooling | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 12 | Parser Error Isolation | Isolated execution per source; errors or bans on one source never halt or degrade other sources | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 13 | AUTO.RIA Parser | Server-rendered HTML scraper for Audi A6 search cards with hi-res image URL conversion | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 14 | OLX Parser | Scraper for OLX Ukraine passenger cars category supporting embedded JSON state and HTML DOM fallback | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 15 | RST.ua Parser | Scraper for RST.ua Audi A6 listings with charset tolerance (UTF-8 / Windows-1251) and hi-res photo extraction | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 16 | Telegram Channel Parser | Telethon MTProto client for monitoring car sales channels with album grouping and StringSession auth | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 17 | Instagram Account Parser | Public web profile scraper for Instagram car dealer accounts with graceful degradation on login wall | M3 | Survey 3, ORIGINAL_REQUEST §R1 |
| 18 | Telegram Notification Engine | Async Telegram Bot API client wrapping `sendMediaGroup`, `sendPhoto`, and `sendMessage` | M4 | Survey 3, ORIGINAL_REQUEST §R4 |
| 19 | Media Group & Formatting | Multi-photo albums (2–10 photos) with <=1024 char caption, emojis, car specs, source link, and review badges | M4 | Survey 3, ORIGINAL_REQUEST §R4 |
| 20 | Rate Limiting & FloodWait Recovery | 1.2s inter-message throttle and automated exponential backoff on HTTP 429 / FloodWait | M4 | Survey 3, ORIGINAL_REQUEST §R4 |
| 21 | Pipeline Orchestrator Runner | Main CLI runner (`src.runner`) orchestrating concurrent ingestion, filtering, deduplication, alerting, and telemetry | M4 | Survey 3, ORIGINAL_REQUEST §R5 |
| 22 | GitHub Actions Workflow | `.github/workflows/parser.yml` with scheduled cron (`0 8,14,20 * * *`), manual dispatch, and secrets injection | M5 | Survey 3, ORIGINAL_REQUEST §R5 |
| 23 | Git Remote Setup | Configuration and verification of remote `git@github.com:max93139/car_parser.git` | M5 | Survey 3, ORIGINAL_REQUEST §R5 |
| 24 | E2E Acceptance & Adversarial Hardening | 100% test pass on full E2E suite (Tiers 1–4) and adversarial coverage hardening (Tier 5) | M5 | Survey 1–3, ORIGINAL_REQUEST Acceptance Criteria |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Data Layer, Core Models, Config & Deduplication | Pydantic v2 models, SQLAlchemy 2.0 Async schema, 3-level deduplicator service, config parser, SQLite/PostgreSQL support | none | DONE |
| M2 | Audi A6 C5 Strict Filtering Engine | Homoglyphs, text normalization, negative filters, model/gen validation (C5 1997-2005), engine whitelist (1.8T, 2.4, 1.9 TDI), `NEEDS_REVIEW` | none | IN_PROGRESS |
| M3 | Parsers Architecture & Multi-Source Scrapers | `BaseParser` ABC, error isolation, AUTO.RIA, OLX, RST, Telegram (Telethon), Instagram parsers | M1 | PLANNED |
| M4 | Telegram Notification Engine & Pipeline Runner | Telegram Bot API client, media groups, format templates, FloodWait recovery, `src.runner` pipeline | M1, M2, M3 | PLANNED |
| M5 | DevOps, GitHub Actions & Final E2E Pass | `.github/workflows/parser.yml`, git remote setup, pass 100% of E2E test suite (Tiers 1–5) | M1, M2, M3, M4, TEST_READY.md | PLANNED |

---

## Interface Contracts

### M1 Models ↔ M2 Filtering Engine
- `RawListingPayload` contains raw fields: `title`, `description`, `year`, `engine`, `fuel_type`, `transmission`, `mileage`, `source`, `url`.
- `filter_listing(payload: RawListingPayload) -> FilterResult`:
  - `status: FilterStatus` (`PASS`, `REJECT`, `NEEDS_REVIEW`)
  - `confidence: float` (0.0 to 1.0)
  - `reasons: list[str]` (e.g. `["NEGATIVE_KEYWORD_DISMANTLER"]`, `["ENGINE_BLACKLIST_2.5_TDI"]`)
  - `normalized_engine: Optional[str]` (`"1.8T"`, `"2.4"`, `"1.9_TDI"`)
  - `normalized_generation: Optional[str]` (`"C5"`)

### M1 Deduplicator ↔ Storage
- `deduplicator.evaluate(listing: Listing, db_session) -> DeduplicationResult`:
  - `is_duplicate: bool`
  - `matched_level: Optional[int]` (1, 2, or 3)
  - `existing_listing_id: Optional[int]`
  - `is_price_drop: bool`
  - `price_diff_usd: Optional[float]`

### M3 Parsers ↔ Pipeline Runner
- `BaseParser.fetch_new_listings() -> AsyncGenerator[RawListingPayload, None]`
- Scraper execution wraps each source in `try/except` yielding `ParserRunStats(source, status, scanned, new, errors)`.

### M4 Notifier ↔ Pipeline Runner
- `TelegramNotifier.send_listing_alert(listing: Listing) -> bool`:
  - Dispatches media group (photos) or single photo/message with rate limiter and FloodWait backoff.
  - Returns `True` on success, updates `listing.is_sent_to_telegram = True`.

---

## Code Layout
```
/Users/mac/Desktop/car/
├── config/
│   └── config.yaml               # Application configuration
├── src/
│   ├── __init__.py
│   ├── config.py                 # Pydantic v2 Settings
│   ├── models/
│   │   ├── __init__.py
│   │   ├── listing.py            # Pydantic Listing & RawListingPayload
│   │   └── filter_result.py      # FilterResult, FilterStatus
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py         # SQLAlchemy async engine & sessionmaker
│   │   ├── models.py             # DDL: Source, Listing, ListingVersion, ParserRun
│   │   └── ddl.py                # Raw DDL creation utilities
│   ├── filtering/
│   │   ├── __init__.py
│   │   ├── normalizer.py         # Text & homoglyph normalization
│   │   ├── negative_rules.py     # Parts, wrecker, trade exclusion rules
│   │   ├── model_rules.py        # Generation C5 / 4B validation
│   │   ├── engine_rules.py       # 1.8T, 2.4, 1.9 TDI whitelist / blacklists
│   │   └── engine.py             # Orchestrating FilterEngine
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── base.py               # BaseParser ABC & ParserRunStats
│   │   ├── auto_ria.py           # AUTO.RIA scraper
│   │   ├── olx.py                # OLX scraper
│   │   ├── rst.py                # RST.ua scraper
│   │   ├── telegram.py           # Telethon MTProto scraper
│   │   └── instagram.py          # Instagram public scraper
│   ├── services/
│   │   ├── __init__.py
│   │   └── deduplicator.py       # 3-level deduplication service
│   ├── notifier/
│   │   ├── __init__.py
│   │   ├── telegram.py           # Telegram Bot API client
│   │   └── templates.py          # Rich formatting & captions
│   └── runner.py                 # Master pipeline orchestrator CLI
├── tests/
│   ├── conftest.py               # Test database fixtures, mock HTTP clients
│   ├── e2e/                      # Opaque-box E2E test suite (Tiers 1–4)
│   ├── unit/                     # Unit tests for models, filter, deduplicator, parsers
│   └── fixtures/                 # HTML/JSON mock fixtures for all 5 platforms
├── .github/
│   └── workflows/
│       └── parser.yml            # GitHub Actions cron & dispatch workflow
├── pyproject.toml                # Project metadata & dependencies
└── requirements.txt              # Production & testing dependencies
```
