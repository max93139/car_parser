"""
Master Pipeline Runner for Audi A6 C5 Monitoring Service.

Orchestrates:
1. Application configuration loading (config/config.yaml with env overrides).
2. Database connection & table verification (PostgreSQL / SQLite).
3. Concurrent, isolated parser execution (AUTO.RIA, OLX, RST, Telegram, Instagram).
4. Deterministic domain filtering (FilterEngine).
5. 3-level deduplication and historical price drop tracking (Deduplicator).
6. Telegram notification broadcasting with media group albums (TelegramNotifier).
7. Execution telemetry persistence in parser_runs table.
8. CLI interface with --once, --source, --dry-run flags.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
import sys
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.config import Settings, get_settings
from src.database.connection import close_db_engine, get_async_engine, get_session_factory
from src.database.ddl import init_db
from src.database.models import ListingModel, ParserRunModel
from src.filtering.engine import FilterEngine
from src.models.listing import Listing, RawListingPayload
from src.notifier.telegram import TelegramNotifier
from src.parsers.auto_ria import AutoRiaParser
from src.parsers.base import BaseParser
from src.parsers.instagram import InstagramParser
from src.parsers.olx import OlxParser
from src.parsers.rst import RstParser
from src.parsers.telegram import TelegramChannelParser
from src.database.repository import (
    calculate_market_price_stats,
    get_or_create_user_filter,
    get_seller_ad_count,
)
from src.filtering.user_filter import matches_user_filter
from src.services.deduplicator import Deduplicator

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("src.runner")


class PipelineRunner:
    """
    Main orchestrator for the car monitoring pipeline.
    Coordinates scrapers, filtering, deduplication, notifications, and telemetry.
    """

    def __init__(
        self,
        config: Optional[Union[str, Settings]] = None,
        dry_run: Optional[bool] = None,
        source_override: Optional[str] = None,
        concurrency_limit: Optional[int] = None,
        engine: Optional[AsyncEngine] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        filter_engine: Optional[FilterEngine] = None,
        deduplicator: Optional[Deduplicator] = None,
        notifier: Optional[TelegramNotifier] = None,
    ) -> None:
        if isinstance(config, Settings):
            self.settings = config
        elif isinstance(config, str):
            self.settings = get_settings(config, reload=True)
        else:
            self.settings = get_settings()

        # Command-line / programmatic overrides
        self.dry_run = dry_run if dry_run is not None else self.settings.runner.dry_run
        self.source_override = source_override
        self.concurrency_limit = concurrency_limit or self.settings.runner.concurrency_limit or 3

        # Injected or lazily initialized dependencies
        self._engine = engine
        self._session_factory = session_factory
        self._filter_engine = filter_engine
        self._deduplicator = deduplicator
        self._notifier = notifier

        self._semaphore = asyncio.Semaphore(self.concurrency_limit)

    async def get_engine(self) -> AsyncEngine:
        """Returns the active async database engine."""
        if self._engine is None:
            self._engine = get_async_engine(self.settings.database.url)
        return self._engine

    async def get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Returns the active session factory."""
        if self._session_factory is None:
            engine = await self.get_engine()
            self._session_factory = get_session_factory(engine)
        return self._session_factory

    def get_filter_engine(self) -> FilterEngine:
        """Returns the filtering engine configured for Audi A6 C5."""
        if self._filter_engine is None:
            self._filter_engine = FilterEngine(config=self.settings.search.model_dump())
        return self._filter_engine

    def get_deduplicator(self) -> Deduplicator:
        """Returns the 3-level deduplicator."""
        if self._deduplicator is None:
            self._deduplicator = Deduplicator()
        return self._deduplicator

    def get_notifier(self) -> TelegramNotifier:
        """Returns the TelegramNotifier client."""
        if self._notifier is None:
            self._notifier = TelegramNotifier(
                bot_token=self.settings.telegram_bot.bot_token,
                chat_id=self.settings.telegram_bot.chat_id,
                max_photos=self.settings.telegram_bot.max_photos_per_album,
                rate_limit_delay=self.settings.telegram_bot.rate_limit_delay,
            )
        return self._notifier

    def initialize_parsers(self) -> List[BaseParser]:
        """
        Instantiates enabled scrapers according to configuration and source overrides.
        """
        parsers: List[BaseParser] = []
        p_cfg = self.settings.parsers

        # 1. AUTO.RIA
        if p_cfg.auto_ria.enabled and (not self.source_override or self.source_override == "auto_ria"):
            parsers.append(
                AutoRiaParser(
                    request_delay=p_cfg.auto_ria.request_delay,
                    timeout=p_cfg.auto_ria.timeout,
                    max_pages=p_cfg.auto_ria.max_pages,
                    target_models=[("A6", "49"), ("A4", "47")],
                )
            )

        # 2. OLX
        if p_cfg.olx.enabled and (not self.source_override or self.source_override == "olx"):
            parsers.append(
                OlxParser(
                    request_delay=p_cfg.olx.request_delay,
                    timeout=p_cfg.olx.timeout,
                    max_pages=p_cfg.olx.max_pages,
                )
            )

        # 3. RST.ua
        if p_cfg.rst.enabled and (not self.source_override or self.source_override == "rst"):
            parsers.append(
                RstParser(
                    request_delay=p_cfg.rst.request_delay,
                    timeout=p_cfg.rst.timeout,
                    max_pages=p_cfg.rst.max_pages,
                )
            )

        # 4. Telegram Channels
        if p_cfg.telegram.enabled and (not self.source_override or self.source_override == "telegram"):
            parsers.append(
                TelegramChannelParser(
                    api_id=p_cfg.telegram.api_id or 0,
                    api_hash=p_cfg.telegram.api_hash or "",
                    session_string=p_cfg.telegram.session_string or "",
                    channels=p_cfg.telegram.channels,
                    max_messages_per_channel=p_cfg.telegram.max_messages_per_channel,
                )
            )

        # 5. Instagram Accounts
        if p_cfg.instagram.enabled and (not self.source_override or self.source_override == "instagram"):
            parsers.append(
                InstagramParser(
                    accounts=p_cfg.instagram.accounts,
                    session_cookie=p_cfg.instagram.session_cookie,
                    max_posts_per_account=p_cfg.instagram.max_posts_per_account,
                )
            )

        return parsers

    async def _process_listing_payload(
        self,
        raw_payload: RawListingPayload,
        session: AsyncSession,
        filter_engine: FilterEngine,
        deduplicator: Deduplicator,
        notifier: TelegramNotifier,
    ) -> Dict[str, Any]:
        """
        Executes single listing pipeline: Filter -> Deduplicate -> Persist -> Alert.
        Returns execution outcome dictionary.
        """
        # Step 1: Filter
        raw_m = (getattr(raw_payload, "model", None) or "").strip().upper()
        raw_g = (getattr(raw_payload, "generation", None) or "").strip().upper()
        is_c5_target = (not raw_m or raw_m == "A6") and (not raw_g or raw_g == "C5")

        if is_c5_target:
            filter_res = filter_engine.filter_listing(raw_payload)
            status_val = filter_res.status.value if hasattr(filter_res.status, "value") else str(filter_res.status)
            if filter_res.is_rejected:
                return {
                    "outcome": "REJECTED",
                    "filter_status": status_val,
                    "is_duplicate": False,
                    "is_price_drop": False,
                    "notified": False,
                }
        else:
            # Multi-model path (e.g. A4, or A6 C4/C6/C7): apply negative context filtering (Track A)
            clean_title = (getattr(raw_payload, "title", None) or "").lower()
            clean_desc = (getattr(raw_payload, "description", None) or getattr(raw_payload, "raw_text", None) or "").lower()
            from src.filtering.negative_rules import evaluate_negative_rules
            neg_reason = evaluate_negative_rules(clean_title, clean_desc)
            if neg_reason:
                return {
                    "outcome": "REJECTED",
                    "filter_status": "REJECT",
                    "is_duplicate": False,
                    "is_price_drop": False,
                    "notified": False,
                }
            from src.models.filter_result import FilterResult, FilterStatus
            filter_res = FilterResult(status=FilterStatus.PASS, confidence=1.0)
            status_val = "PASS"

        # Step 2: Convert to unified Listing model
        payload_data = raw_payload.model_dump()
        payload_data["status"] = status_val
        if filter_res.normalized_engine:
            payload_data["engine_code"] = filter_res.normalized_engine
        if filter_res.normalized_generation:
            payload_data["generation"] = filter_res.normalized_generation

        listing = Listing(**payload_data)

        # Step 3: Deduplicate & Persist
        dedup_res = await deduplicator.evaluate(listing, session, persist=True)

        notified = False
        action = dedup_res.action

        # Step 4: Dispatch Telegram Alerts
        if not self.dry_run:
            should_send = True
            # Check review settings
            if listing.status == "NEEDS_REVIEW" and not self.settings.telegram_bot.send_needs_review:
                should_send = False

            if should_send and self.settings.telegram_bot.chat_id:
                try:
                    user_filter = await get_or_create_user_filter(session, self.settings.telegram_bot.chat_id)
                    if not matches_user_filter(listing, user_filter):
                        should_send = False
                except Exception as uf_err:
                    logger.debug("[runner] Failed to check custom user filter: %s", uf_err)

            if should_send:
                # Calculate market price statistics & dealer history
                stats = await calculate_market_price_stats(
                    session, model=listing.model, year=listing.year, generation=listing.generation
                )
                seller_count = await get_seller_ad_count(
                    session, seller_phone=listing.seller_phone, seller_name=listing.seller
                )

                meta_info: Dict[str, Any] = {
                    "avg_price_usd": stats.get("avg_price_usd"),
                    "sample_size": stats.get("sample_size"),
                    "seller_ad_count": seller_count,
                }

                if action == "CREATED":
                    sent = await notifier.send_listing_alert(listing, price_drop_info=meta_info, session=session)
                    if sent:
                        notified = True
                        if dedup_res.listing_id:
                            stmt = select(ListingModel).where(ListingModel.id == dedup_res.listing_id)
                            db_item = (await session.execute(stmt)).scalar_one_or_none()
                            if db_item:
                                db_item.is_sent_to_telegram = True
                                db_item.telegram_sent_at = datetime.now(timezone.utc)
                                db_item.status = "SENT"
                                await session.flush()

                elif dedup_res.is_price_drop:
                    old_price_usd = (listing.price_usd or 0.0) - (dedup_res.price_diff_usd or 0.0)
                    meta_info.update({
                        "is_price_drop": True,
                        "old_price_usd": old_price_usd,
                        "new_price_usd": listing.price_usd or 0.0,
                        "diff_usd": dedup_res.price_diff_usd,
                    })
                    sent = await notifier.send_listing_alert(
                        listing, price_drop_info=meta_info, session=session
                    )
                    if sent:
                        notified = True

        return {
            "outcome": action,
            "filter_status": status_val,
            "is_duplicate": dedup_res.is_duplicate,
            "is_price_drop": dedup_res.is_price_drop,
            "notified": notified,
        }

    async def run_source_parser(
        self,
        parser: BaseParser,
    ) -> Dict[str, Any]:
        """
        Executes a single source scraper in complete isolation.
        Errors during ingestion are caught, isolated, and persisted to parser_runs table.
        """
        async with self._semaphore:
            source_name = parser.name
            started_at = datetime.now(timezone.utc)
            start_mono = time.monotonic()
            logger.info("Starting scraper for source: %s", source_name)

            factory = await self.get_session_factory()
            filter_engine = self.get_filter_engine()
            deduplicator = self.get_deduplicator()
            notifier = self.get_notifier()

            stats: Dict[str, Any] = {
                "source": source_name,
                "status": "RUNNING",
                "scanned": 0,
                "matched_filter": 0,
                "new": 0,
                "updated": 0,
                "duplicates": 0,
                "notified": 0,
                "errors": 0,
                "duration_seconds": 0.0,
                "error_message": None,
            }

            error_msg: Optional[str] = None

            async with factory() as session:
                try:
                    # Stream raw listing payloads
                    async for raw_payload in parser.fetch_new_listings():
                        stats["scanned"] += 1
                        try:
                            result = await self._process_listing_payload(
                                raw_payload=raw_payload,
                                session=session,
                                filter_engine=filter_engine,
                                deduplicator=deduplicator,
                                notifier=notifier,
                            )
                            if result.get("outcome") != "REJECTED":
                                stats["matched_filter"] += 1

                                if result.get("outcome") == "CREATED":
                                    stats["new"] += 1
                                elif result.get("is_price_drop"):
                                    stats["updated"] += 1
                                elif result.get("is_duplicate"):
                                    stats["duplicates"] += 1

                                if result.get("notified"):
                                    stats["notified"] += 1

                        except Exception as item_err:
                            logger.error("[%s] Error processing listing: %s", source_name, item_err, exc_info=True)
                            stats["errors"] += 1

                    # Inherit parser final status if set (e.g. DEGRADED)
                    stats["status"] = getattr(parser.stats, "status", "SUCCESS")
                    if stats["status"] == "PENDING" or stats["status"] == "RUNNING":
                        if stats["errors"] > 0 and stats["scanned"] > 0:
                            stats["status"] = "PARTIAL"
                        elif stats["errors"] > 0 and stats["scanned"] == 0:
                            stats["status"] = "FAILED"
                        else:
                            stats["status"] = "SUCCESS"

                except Exception as exc:
                    logger.error("[%s] Unhandled exception in scraper: %s", source_name, exc, exc_info=True)
                    stats["status"] = "FAILED"
                    stats["errors"] += 1
                    error_msg = str(exc)
                    stats["error_message"] = error_msg

                finally:
                    duration = round(time.monotonic() - start_mono, 2)
                    stats["duration_seconds"] = duration
                    finished_at = datetime.now(timezone.utc)

                    # Persist run telemetry to parser_runs table
                    try:
                        parser_run = ParserRunModel(
                            run_id=uuid.uuid4(),
                            source_id=source_name,
                            started_at=started_at,
                            finished_at=finished_at,
                            status=stats["status"],
                            items_scanned=stats["scanned"],
                            items_matched_filter=stats["matched_filter"],
                            items_new=stats["new"],
                            items_updated=stats["updated"],
                            items_duplicates=stats["duplicates"],
                            items_errors=stats["errors"],
                            error_message=error_msg,
                            execution_metadata={
                                "dry_run": self.dry_run,
                                "duration_seconds": duration,
                                "notified": stats["notified"],
                            },
                        )
                        session.add(parser_run)
                        await session.commit()
                    except Exception as telemetry_err:
                        logger.error("[%s] Failed to record parser_runs telemetry: %s", source_name, telemetry_err)
                        await session.rollback()

                    # Release scraper resources
                    await parser.close()

            logger.info(
                "Finished scraper %s in %.1fs: scanned=%d, new=%d, updated=%d, notified=%d, errors=%d, status=%s",
                source_name,
                stats["duration_seconds"],
                stats["scanned"],
                stats["new"],
                stats["updated"],
                stats["notified"],
                stats["errors"],
                stats["status"],
            )
            return stats

    async def run(self) -> Dict[str, Any]:
        """
        Executes complete pipeline across all enabled platforms.
        Ensures DB readiness, concurrent execution, summary logging, and resource cleanup.
        """
        start_time = time.monotonic()
        logger.info(
            "=== Starting Audi A6 C5 Monitoring Pipeline (dry_run=%s, source_override=%s) ===",
            self.dry_run,
            self.source_override,
        )

        # 1. Initialize Database Schema & Seed Sources
        engine = await self.get_engine()
        await init_db(engine, seed_sources=True)

        # 2. Instantiate Parsers
        parsers = self.initialize_parsers()
        if not parsers:
            logger.warning("No scrapers are enabled or matched the source filter. Exiting.")
            return {
                "total_scanned": 0,
                "matched_filter": 0,
                "new": 0,
                "updated": 0,
                "duplicates": 0,
                "notified": 0,
                "errors": 0,
                "duration_seconds": 0.0,
                "sources": {},
            }

        # 3. Concurrent isolated execution
        tasks = [self.run_source_parser(p) for p in parsers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Aggregate telemetry
        aggregated: Dict[str, Any] = {
            "total_scanned": 0,
            "matched_filter": 0,
            "new": 0,
            "updated": 0,
            "duplicates": 0,
            "notified": 0,
            "errors": 0,
            "duration_seconds": 0.0,
            "sources": {},
        }

        source_stats_list: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, dict):
                src = res["source"]
                aggregated["sources"][src] = res
                aggregated["total_scanned"] += res["scanned"]
                aggregated["matched_filter"] += res["matched_filter"]
                aggregated["new"] += res["new"]
                aggregated["updated"] += res["updated"]
                aggregated["duplicates"] += res["duplicates"]
                aggregated["notified"] += res["notified"]
                aggregated["errors"] += res["errors"]
                source_stats_list.append(res)
            elif isinstance(res, Exception):
                logger.error("Parser task encountered uncaught exception: %s", res)
                aggregated["errors"] += 1

        total_elapsed = round(time.monotonic() - start_time, 2)
        aggregated["duration_seconds"] = total_elapsed

        # 5. Output Summary Table
        self.print_summary_table(source_stats_list, aggregated)

        # 6. Release notifier resources
        if self._notifier is not None:
            await self._notifier.close()

        return aggregated

    @staticmethod
    def print_summary_table(
        source_stats: List[Dict[str, Any]],
        aggregated: Dict[str, Any],
    ) -> None:
        """Prints formatted execution telemetry table to stdout."""
        border = "+" + "=" * 94 + "+"
        header = f"| {'CAR PARSER EXECUTION SUMMARY':^92} |"
        sub_border = "+============+==========+=========+=========+==========+=========+===========+==========+"
        col_hdr = "| Source     | Status   | Fetched | Filtered| New Ads  | Notified| Errors    | Time (s) |"

        print("\n" + border)
        print(header)
        print(sub_border)
        print(col_hdr)
        print(sub_border)

        for s in source_stats:
            src = s["source"][:10]
            status = s["status"][:8]
            scanned = s["scanned"]
            filtered = s["matched_filter"]
            new_ads = s["new"]
            notified = s["notified"]
            errors = s["errors"]
            duration = f"{s['duration_seconds']:.1f}s"
            print(
                f"| {src:<10} | {status:<8} | {scanned:>7} | {filtered:>7} | "
                f"{new_ads:>8} | {notified:>7} | {errors:>9} | {duration:>8} |"
            )

        print(sub_border)
        total_line = (
            f"| {'TOTAL':<10} | {'':<8} | {aggregated['total_scanned']:>7} | "
            f"{aggregated['matched_filter']:>7} | {aggregated['new']:>8} | "
            f"{aggregated['notified']:>7} | {aggregated['errors']:>9} | "
            f"{aggregated['duration_seconds']:.1f}s |"
        )
        print(total_line)
        print(border + "\n")


def parse_cli_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parses command-line arguments for runner invocation."""
    parser = argparse.ArgumentParser(
        description="Audi A6 C5 Multi-Source Monitoring Pipeline Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run monitoring pipeline once and exit",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Execute only a specific source (auto_ria, olx, rst, telegram, instagram)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Dry run mode: ingest and filter without sending live Telegram alerts",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Concurrency limit for scrapers",
    )
    return parser.parse_args(argv)


async def async_main(argv: Optional[List[str]] = None) -> int:
    """Async CLI entry point."""
    args = parse_cli_args(argv)
    runner = PipelineRunner(
        config=args.config,
        dry_run=args.dry_run,
        source_override=args.source,
        concurrency_limit=args.concurrency,
    )
    try:
        summary = await runner.run()
        if summary.get("errors", 0) > 0 and summary.get("total_scanned", 0) == 0:
            return 1
        return 0
    finally:
        await close_db_engine()


def main() -> None:
    """Synchronous entry point for module execution."""
    exit_code = asyncio.run(async_main(sys.argv[1:]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
