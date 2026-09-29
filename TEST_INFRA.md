# TEST_INFRA — Audi A6 C5 Monitoring Service

## 1. Overview & 4-Tier Testing Architecture

The test suite for the **Audi A6 C5 Multi-Source Monitoring Service** is constructed using an **opaque-box 4-tier testing architecture**. All tests evaluate public contracts, data interfaces, boundary thresholds, combinatorial state transitions, and full lifecycle end-to-end workflows. Tests never depend on internal private states or facade mocks.

```
+---------------------------------------------------------------------------------------+
|                               4-TIER TEST ARCHITECTURE                                |
+---------------------------------------------------------------------------------------+
|  Tier 1: Feature Coverage                                                             |
|  - Minimum >= 5 isolated test cases per feature across the 24-feature inventory.      |
|  - Verifies contract compliance, input parsing, validation schemas, and expected outputs|
|  - File: tests/e2e/test_e2e_tier1_features.py                                         |
+---------------------------------------------------------------------------------------+
|  Tier 2: Boundary & Corner Cases                                                      |
|  - Minimum >= 5 boundary tests per feature domain.                                    |
|  - Tests transition boundaries: years 1996, 1997, 2004, 2005, 2006.                   |
|  - Tests maximum lengths, empty payloads, corrupted characters, Unicode homoglyphs.   |
|  - File: tests/e2e/test_e2e_tier2_boundaries.py                                       |
+---------------------------------------------------------------------------------------+
|  Tier 3: Cross-Feature Combinations & Pairwise                                        |
|  - Pairwise interactions: Multi-source scrapers x Filtering Engine x 3-level Dedup.   |
|  - Tests state changes: First seen -> Deduplicated -> Price Drop -> Re-alerted.       |
|  - File: tests/e2e/test_e2e_tier3_combinations.py                                     |
+---------------------------------------------------------------------------------------+
|  Tier 4: End-to-End Real-World Scenarios                                              |
|  - Full pipeline ingestion simulation from raw platform HTML/JSON to Telegram delivery|
|  - Concurrent ingestion across all 5 platforms with error isolation.                 |
|  - Price drop detection, media group packaging, and FloodWait backoff simulation.     |
|  - File: tests/e2e/test_e2e_tier4_scenarios.py                                        |
+---------------------------------------------------------------------------------------+
```

---

## 2. Feature Inventory Coverage Matrix

The service defines 24 key features across 5 development milestones. Each feature is explicitly covered in the test suites:

| # | Feature Name | Primary Spec | Target Modules | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|--------------|--------------|----------------|:------:|:------:|:------:|:------:|
| 1 | Unified Listing Model | R1, Survey 1 | `src.models.listing` | >=5 | Yes | Yes | Yes |
| 2 | PostgreSQL DDL & Models | R3, Survey 1 | `src.database.models` | >=5 | Yes | Yes | Yes |
| 3 | 3-Level Deduplication | R3, Survey 1 | `src.services.deduplicator` | >=5 | Yes | Yes | Yes |
| 4 | State & Version Tracking | R3, Survey 1 | `src.services.deduplicator`, `src.database` | >=5 | Yes | Yes | Yes |
| 5 | Config & Settings Management | R5, Survey 3 | `src.config` | >=5 | Yes | Yes | Yes |
| 6 | Homoglyph & Text Normalization | R2, Survey 2 | `src.filtering.normalizer` | >=5 | Yes | Yes | Yes |
| 7 | Contextual Negative Filtering | R2, Survey 2 | `src.filtering.negative_rules` | >=5 | Yes | Yes | Yes |
| 8 | Generation & Model Verification | R2, Survey 2 | `src.filtering.model_rules` | >=5 | Yes | Yes | Yes |
| 9 | Target Engine Verification | R2, Survey 2 | `src.filtering.engine_rules` | >=5 | Yes | Yes | Yes |
| 10 | Decision State Machine | R2, Survey 2 | `src.filtering.engine` | >=5 | Yes | Yes | Yes |
| 11 | BaseParser Streaming ABC | R1, Survey 3 | `src.parsers.base` | >=5 | Yes | Yes | Yes |
| 12 | Parser Error Isolation | R1, Survey 3 | `src.parsers.base`, `src.runner` | >=5 | Yes | Yes | Yes |
| 13 | AUTO.RIA Parser | R1, Survey 3 | `src.parsers.auto_ria` | >=5 | Yes | Yes | Yes |
| 14 | OLX Parser | R1, Survey 3 | `src.parsers.olx` | >=5 | Yes | Yes | Yes |
| 15 | RST.ua Parser | R1, Survey 3 | `src.parsers.rst` | >=5 | Yes | Yes | Yes |
| 16 | Telegram Channel Parser | R1, Survey 3 | `src.parsers.telegram` | >=5 | Yes | Yes | Yes |
| 17 | Instagram Account Parser | R1, Survey 3 | `src.parsers.instagram` | >=5 | Yes | Yes | Yes |
| 18 | Telegram Notification Engine | R4, Survey 3 | `src.notifier.telegram` | >=5 | Yes | Yes | Yes |
| 19 | Media Group & Formatting | R4, Survey 3 | `src.notifier.templates` | >=5 | Yes | Yes | Yes |
| 20 | Rate Limiting & FloodWait Recovery | R4, Survey 3 | `src.notifier.telegram` | >=5 | Yes | Yes | Yes |
| 21 | Pipeline Orchestrator Runner | R5, Survey 3 | `src.runner` | >=5 | Yes | Yes | Yes |
| 22 | GitHub Actions Workflow | R5, Survey 3 | `.github/workflows/parser.yml` | >=5 | Yes | Yes | Yes |
| 23 | Git Remote Setup | R5, Survey 3 | Git configuration | >=5 | Yes | Yes | Yes |
| 24 | E2E Acceptance & Adversarial Hardening | R1-R5 | Full pipeline | >=5 | Yes | Yes | Yes |

---

## 3. Test Fixtures Architecture (`tests/fixtures/`)

To guarantee reproducible opaque-box testing without dependency on live network connectivity or rate limits, realistic fixtures are stored in `tests/fixtures/`:

```
tests/fixtures/
├── auto_ria/
│   ├── sample_search_page.html       # Server-rendered AUTO.RIA search HTML with tickets
│   └── sample_card_detail.html       # Full card HTML with hi-res image URLs
├── olx/
│   ├── sample_prerendered_state.json # OLX window.__PRERENDERED_STATE__ payload
│   └── sample_listing_card.html      # OLX DOM fallback card HTML
├── rst/
│   ├── sample_rst_search.html        # RST search page with .rst-ocb-i car elements
│   └── sample_rst_win1251.html       # RST page with Windows-1251 encoding markers
├── telegram/
│   ├── sample_channel_posts.json     # Telethon message structures with albums (grouped_id)
│   └── sample_single_post.json       # Standalone Telethon message
├── instagram/
│   ├── sample_profile_response.json  # Public web profile info JSON response
│   └── sample_login_wall.html        # Instagram /accounts/login/ challenge response
└── config/
    └── test_config.yaml              # Complete mock configuration for test runs
```

---

## 4. Test Execution Guide

### Prerequisites
- Python 3.12+ (or 3.14+)
- Active virtual environment `.venv` with installed requirements.

### Quick Commands
```bash
# Run all E2E test suites with verbose output
.venv/bin/pytest tests/e2e/ -v

# Run individual test tiers
.venv/bin/pytest tests/e2e/test_e2e_tier1_features.py -v
.venv/bin/pytest tests/e2e/test_e2e_tier2_boundaries.py -v
.venv/bin/pytest tests/e2e/test_e2e_tier3_combinations.py -v
.venv/bin/pytest tests/e2e/test_e2e_tier4_scenarios.py -v

# Run tests with duration profiling
.venv/bin/pytest tests/e2e/ --durations=10

# Run specific feature tests by keyword expression
.venv/bin/pytest tests/e2e/ -k "test_filter" -v
.venv/bin/pytest tests/e2e/ -k "test_dedup" -v
.venv/bin/pytest tests/e2e/ -k "test_parser" -v
```

---

## 5. Expected Output Derivation & Oracle Rules

Expected outputs for test assertions are derived through authoritative domain oracles:
1. **Filtering Engine Rules**:
   - `PASS`: Model is Audi A6 C5 / 4B, Year in 1997-2005, Engine is one of `1.8T`, `2.4`, `1.9_TDI`, zero negative flags.
   - `REJECT`: Presence of dismantler (`розбірка`, `шрот`, `на запчасти`), trade-in target (`обмін на Audi`), buyer inquiry (`куплю`), non-C5 generation (`C4`, `C6`, `C7`), non-target engine (`2.5 TDI`, `2.7T`, `2.8`, `3.0`, `4.2`, `2.0 ALT`, `1.8 ADR`).
   - `NEEDS_REVIEW`: High probability of C5, but missing engine or transition year ambiguity (1997 without C5 badge, 2004 2.4 sedan without C5/C6 badge).
2. **Deduplication Engine Rules**:
   - Level 1 Match: Identical `(source_id, source_listing_id)`.
   - Level 2 Match: Identical canonical URL (stripped UTM, standard domain, normalized path).
   - Level 3 Match: Identical SHA-256 fingerprint over `AUDI_A6_C5 | year | body | engine | fuel | trans | drive | mileage_bucket | location | seller`.
3. **Price Drop Rules**:
   - `old_price_usd > new_price_usd` triggers `PRICE_DROP` version and alert flag.
   - `old_price_usd <= new_price_usd` updates price without alert flag.
4. **Notification Engine Rules**:
   - Valid photo list of length $N \in [2, 10]$ formats as Telegram `InputMediaPhoto` media group.
   - Single photo formats as `sendPhoto`.
   - Caption length strictly $\le 1024$ characters.
   - Ukrainian currency conversion and localized emojis (`🚗`, `💰`, `📍`, `⚙️`, `📉`).
