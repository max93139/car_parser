"""
Adversarial Challenge Test Suite for Milestone M1 (Data Integrity & Concurrency).
Empirically stress-tests:
1. Pydantic validation resilience and boundary values (year, price, mileage, currency, phone, empty fields)
2. Database check constraints, foreign keys, and cascading deletes
3. Async concurrency: rapid consecutive insertions, parallel race conditions, and source auto-provisioning
4. Transaction rollback and session error recovery
5. Reproduces empirical bugs and edge cases discovered during challenge review.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.database.connection import (
    close_db_engine,
    get_async_engine,
    get_db_session,
    get_session_factory,
)
from src.database.ddl import drop_db, init_db
from src.database.models import ListingModel, ListingVersionModel, SourceModel
from src.models.listing import Listing, RawListingPayload, SourceType
from src.services.deduplicator import Deduplicator, DeduplicationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def challenge_engine() -> AsyncEngine:
    """Isolated in-memory SQLite engine for challenge tests."""
    engine = get_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine, seed_sources=True)
    yield engine
    await drop_db(engine)
    await close_db_engine(engine)


@pytest_asyncio.fixture
async def challenge_session(challenge_engine: AsyncEngine) -> AsyncSession:
    """Direct session fixture with commit/rollback management."""
    factory = get_session_factory(challenge_engine)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Suite 1: Pydantic Validation & Extreme Boundary Values
# ---------------------------------------------------------------------------

class TestPydanticModelResilience:
    """Adversarially tests boundary inputs and validation on Listing and RawListingPayload."""

    def test_year_extreme_boundaries(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_year_1",
            "url": "https://auto.ria.com/test_1.html",
            "title": "Audi A6 Year Boundary Test",
        }

        # Below lower bound (1990)
        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, year=1899)
        assert "year" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, year=1900)
        assert "year" in str(exc_info.value)

        # Above upper bound (2015)
        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, year=2016)
        assert "year" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, year=2100)
        assert "year" in str(exc_info.value)

        # Non-integer strings
        with pytest.raises(ValidationError):
            Listing(**base_data, year="two_thousand")

        # Exact boundaries should succeed
        l_min = Listing(**base_data, year=1990)
        assert l_min.year == 1990

        l_max = Listing(**base_data, year=2015)
        assert l_max.year == 2015

        # Null / None year should be valid
        l_none = Listing(**base_data, year=None)
        assert l_none.year is None

    def test_price_extreme_boundaries(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_price_1",
            "url": "https://auto.ria.com/test_price.html",
            "title": "Audi A6 Price Boundary Test",
        }

        # Negative price must be rejected
        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, price=-0.01)
        assert "price" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, price=-5000.0)
        assert "price" in str(exc_info.value)

        # Zero price should be permitted (e.g. ad without specified price or free)
        l_zero = Listing(**base_data, price=0.0)
        assert l_zero.price == 0.0
        assert l_zero.price_usd == 0.0

        # None price should be permitted
        l_none = Listing(**base_data, price=None)
        assert l_none.price is None
        assert l_none.price_usd is None

        # Extreme high price (within float range)
        l_huge = Listing(**base_data, price=9_999_999.99)
        assert l_huge.price == 9_999_999.99
        assert l_huge.price_usd == 9_999_999.99

    def test_mileage_extreme_boundaries(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_mileage_1",
            "url": "https://auto.ria.com/test_mileage.html",
            "title": "Audi A6 Mileage Boundary Test",
        }

        # Negative mileage rejected
        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, mileage=-1)
        assert "mileage" in str(exc_info.value)

        # Mileage over 2,000,000 km rejected
        with pytest.raises(ValidationError) as exc_info:
            Listing(**base_data, mileage=2_000_001)
        assert "mileage" in str(exc_info.value)

        # Boundary values 0, 2_000_000, and None
        l_0 = Listing(**base_data, mileage=0)
        assert l_0.mileage == 0

        l_max = Listing(**base_data, mileage=2_000_000)
        assert l_max.mileage == 2_000_000

        l_none = Listing(**base_data, mileage=None)
        assert l_none.mileage is None

    def test_currency_normalization_and_boundaries(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_curr_1",
            "url": "https://auto.ria.com/test_curr.html",
            "title": "Audi A6 Currency Test",
            "price": 1000.0,
        }

        # Common variants
        assert Listing(**base_data, currency="$").price_usd == 1000.0
        assert Listing(**base_data, currency="USD").currency == "USD"
        assert Listing(**base_data, currency="usd").currency == "USD"

        # EUR conversion (rate 1.08)
        l_eur = Listing(**base_data, currency="EUR")
        assert l_eur.currency == "EUR"
        assert l_eur.price_usd == 1080.0

        # UAH conversion (rate 0.0241)
        l_uah = Listing(**base_data, currency="ГРН")
        assert l_uah.currency == "UAH"
        assert l_uah.price_usd == 24.1

        # Unknown currency falls back to rate 1.0 without crash
        l_unk = Listing(**base_data, currency="PLN")
        assert l_unk.currency == "PLN"
        assert l_unk.price_usd == 1000.0

        # Excessively long currency string (> 8 chars) rejected by max_length
        with pytest.raises(ValidationError):
            Listing(**base_data, currency="VERY_LONG_CURRENCY_CODE")

    def test_phone_number_adversarial_inputs(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_phone_1",
            "url": "https://auto.ria.com/test_phone.html",
            "title": "Audi A6 Phone Test",
        }

        # Valid Ukrainian patterns
        assert Listing(**base_data, seller_phone="0971234567").seller_phone == "+380971234567"
        assert Listing(**base_data, seller_phone="380971234567").seller_phone == "+380971234567"
        assert Listing(**base_data, seller_phone="971234567").seller_phone == "+380971234567"
        assert Listing(**base_data, seller_phone="+38 (097) 123-45-67").seller_phone == "+380971234567"

        # Non-Ukrainian international numbers
        assert Listing(**base_data, seller_phone="+48 123 456 789").seller_phone == "+48123456789"

        # Junk text without digits
        assert Listing(**base_data, seller_phone="немає телефону").seller_phone is None
        assert Listing(**base_data, seller_phone="").seller_phone is None
        assert Listing(**base_data, seller_phone=None).seller_phone is None

        # Extremely long garbage string with > 32 digits rejected cleanly by Pydantic
        long_digits = "1" * 40
        with pytest.raises(ValidationError):
            Listing(**base_data, seller_phone=long_digits)

    def test_string_length_and_whitespace_boundaries(self):
        base_data = {
            "source": "auto_ria",
            "source_id": "test_str_1",
            "url": "https://auto.ria.com/test_str.html",
        }

        # Empty title rejected
        with pytest.raises(ValidationError):
            Listing(**base_data, title="")

        # Whitespace-only title stripped and rejected
        with pytest.raises(ValidationError):
            Listing(**base_data, title="     ")

        # 500 chars title accepted
        t_500 = "A" * 500
        l_500 = Listing(**base_data, title=t_500)
        assert len(l_500.title) == 500

        # 501 chars title rejected
        with pytest.raises(ValidationError):
            Listing(**base_data, title="A" * 501)

    def test_minimal_listing_all_optionals_none(self):
        """Verify that a listing with ONLY required fields initializes and hashes cleanly."""
        minimal = Listing(
            source="olx",
            source_id="min_001",
            url="https://www.olx.ua/d/uk/obyavlenie/audi-min-ID123.html",
            title="Audi A6 C5 Minimal Ad",
        )
        assert minimal.price is None
        assert minimal.price_usd is None
        assert minimal.year is None
        assert minimal.mileage is None
        assert minimal.description is None
        assert minimal.seller is None
        assert minimal.seller_phone is None
        assert minimal.images == []
        assert len(minimal.content_fingerprint) == 64
        assert len(minimal.fuzzy_fingerprint) == 64
        assert minimal.canonical_url.startswith("https://www.olx.ua/d/obyavlenie/audi-min-ID123.html")


# ---------------------------------------------------------------------------
# Suite 2: Database Constraints & Transactional Integrity
# ---------------------------------------------------------------------------

class TestDatabaseIntegrityConstraints:
    """Tests schema-level constraints on ListingModel and relational cascades."""

    @pytest.mark.asyncio
    async def test_db_unique_constraint_source_id_and_listing_id(self, challenge_session: AsyncSession):
        """Enforces that (source_id, source_listing_id) cannot be duplicated directly in DB."""
        l1 = ListingModel(
            source_id="auto_ria",
            source_listing_id="dup_test_100",
            url="https://auto.ria.com/100.html",
            canonical_url="https://auto.ria.com/100.html",
            title="Car 1",
            content_fingerprint="fp1",
            fuzzy_fingerprint="ffp1",
        )
        challenge_session.add(l1)
        await challenge_session.commit()

        # Attempt to insert identical source and source_listing_id
        l2 = ListingModel(
            source_id="auto_ria",
            source_listing_id="dup_test_100",
            url="https://auto.ria.com/100_alt.html",
            canonical_url="https://auto.ria.com/100_alt.html",
            title="Car 2",
            content_fingerprint="fp2",
            fuzzy_fingerprint="ffp2",
        )
        challenge_session.add(l2)
        with pytest.raises(IntegrityError):
            await challenge_session.commit()
        await challenge_session.rollback()

    @pytest.mark.asyncio
    async def test_db_check_constraint_year(self, challenge_session: AsyncSession):
        """Direct DB insertion outside 1990-2015 must violate chk_listings_year."""
        l_bad_year = ListingModel(
            source_id="auto_ria",
            source_listing_id="bad_year_1850",
            url="https://auto.ria.com/bad_year.html",
            canonical_url="https://auto.ria.com/bad_year.html",
            title="Ancient Audi",
            year=1850,
            content_fingerprint="fp_ancient",
            fuzzy_fingerprint="ffp_ancient",
        )
        challenge_session.add(l_bad_year)
        with pytest.raises(IntegrityError):
            await challenge_session.commit()
        await challenge_session.rollback()

    @pytest.mark.asyncio
    async def test_db_check_constraint_mileage(self, challenge_session: AsyncSession):
        """Direct DB insertion with negative mileage must violate chk_listings_mileage."""
        l_neg_mileage = ListingModel(
            source_id="auto_ria",
            source_listing_id="neg_mileage_1",
            url="https://auto.ria.com/neg_mileage.html",
            canonical_url="https://auto.ria.com/neg_mileage.html",
            title="Negative Mileage Audi",
            mileage=-500,
            content_fingerprint="fp_neg",
            fuzzy_fingerprint="ffp_neg",
        )
        challenge_session.add(l_neg_mileage)
        with pytest.raises(IntegrityError):
            await challenge_session.commit()
        await challenge_session.rollback()

    @pytest.mark.asyncio
    async def test_foreign_key_enforcement_on_unregistered_source(self, challenge_session: AsyncSession):
        """Inserting a listing referencing a non-existent source must fail foreign key check."""
        l_unknown_source = ListingModel(
            source_id="completely_unknown_platform",
            source_listing_id="test_fk_1",
            url="https://unknown.com/1.html",
            canonical_url="https://unknown.com/1.html",
            title="Unknown Platform Car",
            content_fingerprint="fp_fk",
            fuzzy_fingerprint="ffp_fk",
        )
        challenge_session.add(l_unknown_source)
        with pytest.raises(IntegrityError):
            await challenge_session.commit()
        await challenge_session.rollback()

    @pytest.mark.asyncio
    async def test_cascading_delete_on_listing_versions(self, challenge_session: AsyncSession):
        """Deleting a listing must cleanly cascade-delete its historical versions."""
        listing = ListingModel(
            source_id="auto_ria",
            source_listing_id="cascade_test_1",
            url="https://auto.ria.com/cascade.html",
            canonical_url="https://auto.ria.com/cascade.html",
            title="Cascade Test Car",
            price=Decimal("5000.00"),
            currency="USD",
            price_usd=Decimal("5000.00"),
            content_fingerprint="fp_casc",
            fuzzy_fingerprint="ffp_casc",
        )
        challenge_session.add(listing)
        await challenge_session.flush()

        # Add 3 versions
        for i in range(3):
            v = ListingVersionModel(
                listing_id=listing.id,
                price=Decimal(str(5000 - i * 100)),
                currency="USD",
                price_usd=Decimal(str(5000 - i * 100)),
                change_type="PRICE_DROP" if i > 0 else "INITIAL",
                change_summary={"v": i},
                detected_at=datetime.now(timezone.utc),
            )
            challenge_session.add(v)
        await challenge_session.commit()

        # Verify versions exist
        stmt = select(func.count(ListingVersionModel.id)).where(
            ListingVersionModel.listing_id == listing.id
        )
        count_before = (await challenge_session.execute(stmt)).scalar_one()
        assert count_before == 3

        # Delete listing
        await challenge_session.delete(listing)
        await challenge_session.commit()

        # Verify versions are cascaded
        count_after = (await challenge_session.execute(stmt)).scalar_one()
        assert count_after == 0


# ---------------------------------------------------------------------------
# Suite 3: Concurrency, Rapid Insertions & Race Conditions
# ---------------------------------------------------------------------------

class TestConcurrencyAndDeduplicationResilience:
    """Stress tests rapid consecutive and parallel async operations against Deduplicator."""

    @pytest.mark.asyncio
    async def test_rapid_consecutive_identical_insertions(self, challenge_session: AsyncSession):
        """50 rapid sequential evaluations of the exact same listing must not corrupt storage."""
        deduplicator = Deduplicator(price_drop_threshold_usd=1.0)
        listing = Listing(
            source="auto_ria",
            source_id="rapid_seq_001",
            url="https://auto.ria.com/uk/car/audi/a6/rapid_seq_001.html?ref=test",
            title="Audi A6 Rapid Test",
            price=4500.0,
            currency="USD",
            year=2001,
            mileage=250000,
        )

        results: list[DeduplicationResult] = []
        for i in range(50):
            res = await deduplicator.evaluate(listing, challenge_session, persist=True)
            results.append(res)

        # 1st evaluation must be CREATED
        assert results[0].is_duplicate is False
        assert results[0].action == "CREATED"
        created_id = results[0].listing_id
        assert created_id is not None

        # 2nd through 50th evaluations must be UNCHANGED (matched Level 1)
        for res in results[1:]:
            assert res.is_duplicate is True
            assert res.matched_level == 1
            assert res.existing_listing_id == created_id
            assert res.action == "UNCHANGED"
            assert res.is_price_drop is False

        await challenge_session.commit()

        # Verify exactly 1 listing row exists in DB
        stmt = select(func.count(ListingModel.id)).where(
            ListingModel.source_id == "auto_ria",
            ListingModel.source_listing_id == "rapid_seq_001",
        )
        total_rows = (await challenge_session.execute(stmt)).scalar_one()
        assert total_rows == 1

        # Verify exactly 1 version row exists
        v_stmt = select(func.count(ListingVersionModel.id)).where(
            ListingVersionModel.listing_id == created_id
        )
        total_versions = (await challenge_session.execute(v_stmt)).scalar_one()
        assert total_versions == 1

    @pytest.mark.asyncio
    async def test_concurrent_parallel_evaluations_uniqueness_protection(
        self, tmp_path
    ):
        """
        Simulates 10 concurrent async tasks evaluating and persisting the exact same brand-new
        listing simultaneously using separate sessions. Uniqueness constraint must prevent
        duplicate rows from being committed.
        """
        db_file = tmp_path / "concurrent_test.db"
        engine = get_async_engine(f"sqlite+aiosqlite:///{db_file}")
        await init_db(engine, seed_sources=True)
        factory = get_session_factory(engine)
        deduplicator = Deduplicator()

        listing = Listing(
            source="rst",
            source_id="concurrent_race_999",
            url="https://rst.ua/car/audi/a6/concurrent_999.html",
            title="Audi A6 Concurrency Race",
            price=4200.0,
            currency="USD",
            year=2002,
        )

        async def worker_insert(worker_idx: int) -> tuple[int, str, bool]:
            async with factory() as session:
                try:
                    res = await deduplicator.evaluate(listing, session, persist=True)
                    await session.commit()
                    return worker_idx, res.action, False
                except IntegrityError:
                    await session.rollback()
                    return worker_idx, "INTEGRITY_ERROR_CAUGHT", True
                except Exception as e:
                    await session.rollback()
                    return worker_idx, f"ERROR: {e}", True

        # Run 10 simultaneous workers
        tasks = [worker_insert(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # Exactly 1 worker created the listing, other 9 were rejected by uniqueness constraint
        successful_creates = [r for r in results if r[1] == "CREATED"]
        assert len(successful_creates) == 1, (
            f"Expected exactly 1 worker to create listing, got {len(successful_creates)}"
        )

        # Exactly ONE listing must exist in the database!
        async with factory() as session:
            stmt = select(func.count(ListingModel.id)).where(
                ListingModel.source_id == "rst",
                ListingModel.source_listing_id == "concurrent_race_999",
            )
            count = (await session.execute(stmt)).scalar_one()
            assert count == 1, f"Expected exactly 1 row, found {count}! Database duplicated records!"

        await close_db_engine(engine)

    @pytest.mark.asyncio
    async def test_concurrent_seeded_sources_throughput(
        self, challenge_engine: AsyncEngine
    ):
        """
        Simulates 50 concurrent tasks across all 5 pre-seeded sources.
        Confirms zero deadlock or source collision when sources are pre-seeded.
        """
        factory = get_session_factory(challenge_engine)
        deduplicator = Deduplicator()

        async def worker(idx: int):
            sources = ["auto_ria", "olx", "rst", "telegram", "instagram"]
            src = sources[idx % len(sources)]
            l = Listing(
                source=src,
                source_id=f"concurrent_seeded_{idx}",
                url=f"https://{src}.com/ad_{idx}.html",
                title=f"Audi A6 Seeded Car {idx}",
                price=3000.0 + idx * 50,
                year=2000 + (idx % 5),
            )
            async with factory() as session:
                res = await deduplicator.evaluate(l, session, persist=True)
                await session.commit()
                return res.action

        tasks = [worker(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        assert len([r for r in results if r == "CREATED"]) == 50


# ---------------------------------------------------------------------------
# Suite 4: Transaction Rollback & Error Recovery
# ---------------------------------------------------------------------------

class TestTransactionRollbackAndRecovery:
    """Verifies that failed sessions rollback cleanly without leaving zombie transactions."""

    @pytest.mark.asyncio
    async def test_get_db_session_rollback_on_exception(self, challenge_engine: AsyncEngine):
        """Context manager get_db_session must roll back changes if an exception is raised."""
        factory = get_session_factory(challenge_engine)

        with pytest.raises(RuntimeError):
            async with get_db_session(challenge_engine) as session:
                listing = ListingModel(
                    source_id="auto_ria",
                    source_listing_id="doomed_listing_99",
                    url="https://auto.ria.com/doomed.html",
                    canonical_url="https://auto.ria.com/doomed.html",
                    title="Doomed Car",
                    content_fingerprint="fp_doomed",
                    fuzzy_fingerprint="ffp_doomed",
                )
                session.add(listing)
                await session.flush()
                # Simulate mid-transaction crash
                raise RuntimeError("Simulated failure mid-transaction")

        # Verify doomed listing was NOT committed
        async with factory() as verify_session:
            stmt = select(ListingModel).where(
                ListingModel.source_listing_id == "doomed_listing_99"
            )
            result = (await verify_session.execute(stmt)).scalar_one_or_none()
            assert result is None, "Failed transaction was not rolled back!"

    @pytest.mark.asyncio
    async def test_engine_reusability_after_rollback(self, challenge_engine: AsyncEngine):
        """Engine and session factory must remain operational and healthy after an error rollback."""
        factory = get_session_factory(challenge_engine)

        # 1. Trigger an error
        try:
            async with factory() as session:
                bad = ListingModel(
                    source_id="auto_ria",
                    source_listing_id="bad_1",
                    url="https://bad.html",
                    canonical_url="https://bad.html",
                    title="Bad",
                    year=1700,  # Fails check constraint
                    content_fingerprint="fp",
                    fuzzy_fingerprint="ffp",
                )
                session.add(bad)
                await session.commit()
        except IntegrityError:
            pass

        # 2. Perform a normal successful transaction immediately after
        async with factory() as session:
            good = ListingModel(
                source_id="auto_ria",
                source_listing_id="good_after_rollback",
                url="https://good.html",
                canonical_url="https://good.html",
                title="Good Car After Rollback",
                year=2001,
                content_fingerprint="fp_good",
                fuzzy_fingerprint="ffp_good",
            )
            session.add(good)
            await session.commit()

        # 3. Verify normal transaction succeeded
        async with factory() as session:
            stmt = select(ListingModel).where(
                ListingModel.source_listing_id == "good_after_rollback"
            )
            saved = (await session.execute(stmt)).scalar_one_or_none()
            assert saved is not None
            assert saved.source_listing_id == "good_after_rollback"


# ---------------------------------------------------------------------------
# Suite 5: Empirical Bug Demonstrations (Adversarial Findings)
# ---------------------------------------------------------------------------

class TestEmpiricalBugReproductions:
    """
    Direct empirical proofs of bugs, race conditions, and data inconsistencies
    identified during adversarial challenge review.
    """

    def test_bug_1_empty_url_triggers_infinite_recursion(self):
        """
        REMEDIATION VERIFICATION:
        Passing url='' or whitespace-only url='   ' triggers a clean Pydantic ValidationError
        instead of RecursionError.
        """
        with pytest.raises(ValidationError):
            Listing(
                source="auto_ria",
                source_id="rec_001",
                url="   ",
                title="Audi A6 Whitespace URL",
            )
        with pytest.raises(ValidationError):
            Listing(
                source="auto_ria",
                source_id="rec_002",
                url="",
                title="Audi A6 Empty URL",
            )

    @pytest.mark.asyncio
    async def test_bug_2_unseeded_source_concurrent_race_condition(
        self, challenge_engine: AsyncEngine
    ):
        """
        EMPIRICAL BUG 2:
        When multiple concurrent tasks arrive with listings for an unseeded source,
        Deduplicator._ensure_source_exists exhibits a check-then-act race, attempting
        duplicate INSERT into sources and raising unhandled IntegrityError.
        """
        factory = get_session_factory(challenge_engine)
        deduplicator = Deduplicator()
        unseeded_source = "concurrent_new_source"

        caught_integrity_errors = []

        async def worker(idx: int):
            async with factory() as session:
                l = Listing(
                    source=unseeded_source,
                    source_id=f"unseeded_ad_{idx}",
                    url=f"https://unseeded.com/ad_{idx}",
                    title=f"Unseeded Ad {idx}",
                )
                try:
                    await deduplicator.evaluate(l, session, persist=True)
                    await session.commit()
                except IntegrityError as exc:
                    await session.rollback()
                    caught_integrity_errors.append(exc)

        # Launch 5 concurrent workers on the new source
        await asyncio.gather(*(worker(i) for i in range(5)))

        # Confirms that concurrency race on unseeded source causes IntegrityError
        assert len(caught_integrity_errors) > 0, (
            "Expected concurrent race condition to produce IntegrityErrors on sources.id"
        )

    @pytest.mark.asyncio
    async def test_bug_3_price_update_ignored_when_initial_price_was_none(
        self, challenge_engine: AsyncEngine
    ):
        """
        EMPIRICAL BUG 3:
        When a listing is initially scraped without a price (price=None), and later updated
        with a valid price (e.g. 4500.0), Deduplicator ignores the price update because
        'if old_price_usd is not None and new_price_usd is not None' evaluates to False.
        The price in DB remains None forever, no version is recorded, and UNCHANGED is returned.
        """
        factory = get_session_factory(challenge_engine)
        deduplicator = Deduplicator()

        # Step 1: Initial ad with price=None
        l1 = Listing(
            source="auto_ria",
            source_id="bug3_car_no_price",
            url="https://auto.ria.com/car_no_price.html",
            title="Audi A6 Initially No Price",
            price=None,
        )
        async with factory() as session:
            res1 = await deduplicator.evaluate(l1, session, persist=True)
            await session.commit()
        assert res1.action == "CREATED"

        # Step 2: Later ad scrape finds price=4500.0
        l2 = Listing(
            source="auto_ria",
            source_id="bug3_car_no_price",
            url="https://auto.ria.com/car_no_price.html",
            title="Audi A6 Initially No Price",
            price=4500.0,
        )
        async with factory() as session:
            res2 = await deduplicator.evaluate(l2, session, persist=True)
            await session.commit()

        # VERIFICATION: The update is marked PRICE_SET, and DB price is updated
        assert res2.action == "PRICE_SET", (
            f"Expected remediation action='PRICE_SET', got {res2.action}"
        )

        async with factory() as session:
            stmt = select(ListingModel).where(ListingModel.source_listing_id == "bug3_car_no_price")
            row = (await session.execute(stmt)).scalar_one()
            # The database price was updated to 4500.0!
            assert row.price == Decimal("4500.00"), (
                f"Expected row.price to be 4500.00, got {row.price}"
            )
            assert row.price_usd == Decimal("4500.00")

    def test_bug_4_stale_derived_fields_on_mutation(self):
        """
        EMPIRICAL BUG 4:
        In-memory mutation of Listing.price does not recalculate price_usd because
        compute_derived_fields only computes price_usd if self.price_usd is None.
        Likewise, mutating mileage does not recalculate content_fingerprint.
        """
        listing = Listing(
            source="auto_ria",
            source_id="mut_001",
            url="https://auto.ria.com/mut_001.html",
            title="Audi A6 Mutation Test",
            price=1000.0,
            mileage=100000,
        )
        assert listing.price == 1000.0
        assert listing.price_usd == 1000.0
        original_fp = listing.content_fingerprint

        # Mutate price and mileage
        listing.price = 2500.0
        listing.mileage = 300000

        # price_usd is STALE (still 1000.0 instead of 2500.0)
        assert listing.price_usd == 1000.0

        # content_fingerprint is STALE (not updated despite mileage change)
        assert listing.content_fingerprint == original_fp
