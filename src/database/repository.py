"""
Repository operations for user filters and custom listing queries.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import ListingModel, UserFilterModel

logger = logging.getLogger(__name__)

DEFAULT_ENGINES = ["1.8T", "2.4", "1.9 TDI"]
DEFAULT_MODELS = ["A6 C5"]


async def get_or_create_user_filter(session: AsyncSession, chat_id: str) -> UserFilterModel:
    """
    Retrieves user filter configuration by chat_id or creates a new default one.
    """
    stmt = select(UserFilterModel).where(UserFilterModel.chat_id == str(chat_id))
    result = await session.execute(stmt)
    user_filter = result.scalar_one_or_none()

    if user_filter is None:
        user_filter = UserFilterModel(
            chat_id=str(chat_id),
            selected_models=list(DEFAULT_MODELS),
            engines=list(DEFAULT_ENGINES),
            min_price=None,
            max_price=None,
            min_year=1997,
            max_year=2005,
            max_mileage=None,
            transmission="any",
            exclude_damaged=True,
        )
        session.add(user_filter)
        await session.flush()

    return user_filter


async def update_user_filter(
    session: AsyncSession,
    chat_id: str,
    **updates: Any,
) -> UserFilterModel:
    """
    Updates specific fields in user_filters record.
    """
    user_filter = await get_or_create_user_filter(session, chat_id)
    for key, value in updates.items():
        if hasattr(user_filter, key):
            setattr(user_filter, key, value)

    user_filter.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return user_filter


async def reset_user_filter(session: AsyncSession, chat_id: str) -> UserFilterModel:
    """
    Resets user filter to default factory values.
    """
    return await update_user_filter(
        session,
        chat_id,
        selected_models=list(DEFAULT_MODELS),
        engines=list(DEFAULT_ENGINES),
        min_price=None,
        max_price=None,
        min_year=1997,
        max_year=2005,
        max_mileage=None,
        transmission="any",
        exclude_damaged=True,
    )


async def find_matching_listings(
    session: AsyncSession,
    user_filter: UserFilterModel,
    limit: int = 5,
) -> List[ListingModel]:
    """
    Finds the most recent listings matching user's custom filter criteria.
    """
    from sqlalchemy import or_, and_

    active_models = getattr(user_filter, "selected_models", None) or DEFAULT_MODELS
    model_conditions = []
    for sm in active_models:
        parts = sm.split()
        if len(parts) == 2:
            m, g = parts[0], parts[1]
            model_conditions.append(and_(ListingModel.model == m, ListingModel.generation == g))
        else:
            model_conditions.append(ListingModel.model == sm)

    stmt = select(ListingModel).where(
        ListingModel.brand == "Audi",
        or_(*model_conditions) if model_conditions else True,
    )

    # 1. Price filters
    if user_filter.min_price is not None:
        stmt = stmt.where(ListingModel.price >= user_filter.min_price)
    if user_filter.max_price is not None:
        stmt = stmt.where(ListingModel.price <= user_filter.max_price)

    # 2. Year filters
    if user_filter.min_year is not None:
        stmt = stmt.where(ListingModel.year >= user_filter.min_year)
    if user_filter.max_year is not None:
        stmt = stmt.where(ListingModel.year <= user_filter.max_year)

    # 3. Mileage filter
    if user_filter.max_mileage is not None:
        stmt = stmt.where(
            (ListingModel.mileage <= user_filter.max_mileage) | (ListingModel.mileage.is_(None))
        )

    # 4. Transmission
    if user_filter.transmission == "manual":
        stmt = stmt.where(
            ListingModel.transmission.in_(["механіка", "manual", "механика"])
        )
    elif user_filter.transmission == "automatic":
        stmt = stmt.where(
            ListingModel.transmission.in_(["автомат", "automatic", "типроник", "варіатор"])
        )

    # 4.1 Drive type
    if getattr(user_filter, "drive_type", "any") == "quattro":
        stmt = stmt.where(ListingModel.drive_type == "quattro")
    elif getattr(user_filter, "drive_type", "any") == "front":
        stmt = stmt.where(ListingModel.drive_type == "front")

    # 4.2 Body type
    if getattr(user_filter, "body_type", "any") == "avant":
        stmt = stmt.where(ListingModel.body_type == "avant")
    elif getattr(user_filter, "body_type", "any") == "sedan":
        stmt = stmt.where(ListingModel.body_type == "sedan")

    # 5. Engines
    active_engines = user_filter.engines or DEFAULT_ENGINES
    if active_engines:
        engine_conditions = []
        for eng in active_engines:
            if "1.8" in eng:
                engine_conditions.append(ListingModel.engine.ilike("%1.8%"))
                engine_conditions.append(ListingModel.engine_code == "1.8T")
            elif "2.4" in eng:
                engine_conditions.append(ListingModel.engine.ilike("%2.4%"))
                engine_conditions.append(ListingModel.engine_code == "2.4")
            elif "1.9" in eng:
                engine_conditions.append(ListingModel.engine.ilike("%1.9%"))
                engine_conditions.append(ListingModel.engine_code == "1.9TDI")

        if engine_conditions:
            from sqlalchemy import or_
            stmt = stmt.where(or_(*engine_conditions))

    stmt = stmt.order_by(ListingModel.first_seen_at.desc()).limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def calculate_market_price_stats(
    session: AsyncSession,
    model: str,
    year: Optional[int],
    generation: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes average and median price for cars of similar model/year to detect below-market deals.
    """
    from sqlalchemy import func
    stmt = select(func.avg(ListingModel.price_usd), func.count(ListingModel.id)).where(
        ListingModel.brand == "Audi",
        ListingModel.price_usd > 500,
    )
    if model:
        stmt = stmt.where(ListingModel.model == model)
    if generation:
        stmt = stmt.where(ListingModel.generation == generation)
    if year:
        # Window of +/- 1 year
        stmt = stmt.where(ListingModel.year.between(year - 1, year + 1))

    res = await session.execute(stmt)
    avg_price, total_count = res.one_or_none() or (None, 0)
    return {
        "avg_price_usd": float(avg_price) if avg_price else None,
        "sample_size": total_count or 0,
    }


async def get_seller_ad_count(
    session: AsyncSession,
    seller_phone: Optional[str] = None,
    seller_name: Optional[str] = None,
) -> int:
    """
    Counts how many vehicles this seller (phone or name) has published in the database.
    """
    from sqlalchemy import func, or_
    conditions = []
    if seller_phone:
        conditions.append(ListingModel.seller_phone == seller_phone)
    if seller_name and len(seller_name) > 3:
        conditions.append(ListingModel.seller == seller_name)

    if not conditions:
        return 0

    stmt = select(func.count(ListingModel.id)).where(or_(*conditions))
    res = await session.execute(stmt)
    count_val = res.scalar_one_or_none()
    return count_val or 0
