"""
Empirical Adversarial Test Suite for Milestone M2 (Audi A6 C5 Strict Filtering Engine).

Created by Challenger (teamwork_preview_challenger_m2_1_gen2).
Adversarially stress-tests the filtering engine across 5 key dimensions:
1. Confusing barter listings (trade-in targets vs outbound trade offers)
2. Parts and scrap listings (standalone parts titles, dismantlers vs car maintenance)
3. Homoglyph evasion (mixed Cyrillic/Latin/Ukrainian scripts, compact engine formats)
4. Disallowed engines (2.5 TDI, 2.7 biturbo, 1.8 NA / без турбо, 2.8, 3.0, 4.2, etc.)
5. Generation boundaries & transition years (1997 C4 vs C5, 2004 C5 vs C6, 2005 C5 vs C6)
"""

import pytest

from src.filtering.engine import FilterEngine
from src.models.filter_result import FilterStatus


@pytest.fixture
def engine() -> FilterEngine:
    return FilterEngine()


# ==============================================================================
# Category 1: Confusing Barter Listings
# ==============================================================================

class TestChallengerBarterListings:
    """Stress-tests distinction between selling an Audi A6 C5 with barter vs trading another vehicle for Audi."""

    def test_pass_outbound_trade_offer_audi_seller(self, engine: FilterEngine):
        """Owner selling Audi A6 C5 1.8T willing to barter for a minibus -> MUST PASS."""
        res = engine.filter_listing(
            title="Продам Ауди А6 С5 1.8Т, обмен на бус",
            year=2000,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_reject_vaz_trade_to_audi(self, engine: FilterEngine):
        """Seller trading VAZ for Audi A6 -> MUST REJECT."""
        res = engine.filter_listing(
            title="Обменяю ВАЗ на Ауди А6",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_reject_vaz_2109_trade_to_audi_c5_18t(self, engine: FilterEngine):
        """Trade offer of VAZ 2109 targeting an Audi A6 C5 1.8T -> MUST REJECT."""
        res = engine.filter_listing(
            title="Обменяю ВАЗ 2109 на Audi A6 C5 1.8T",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_reject_passat_trade_to_audi_c5_tdi(self, engine: FilterEngine):
        """Ukrainian inquiry trading Passat for Audi A6 C5 TDI -> MUST REJECT."""
        res = engine.filter_listing(
            title="Цікавить обмін мого Пасата на Ауді А6 С5 1.9 тді",
            year=2002,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_pass_audi_c5_24_trade_to_suv(self, engine: FilterEngine):
        """Owner selling Audi A6 C5 2.4 open to SUV barter -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.4 газ/бензин 2001, возможен обмен на внедорожник",
            year=2001,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"

    def test_pass_audi_c5_19tdi_trade_money_only(self, engine: FilterEngine):
        """Audi A6 C5 1.9 TDI wagon seller stating 'обмен только на деньги' -> MUST PASS."""
        res = engine.filter_listing(
            title="Продам Audi A6 C5 1.9 TDI 2003 универсал, обмен только на деньги",
            year=2003,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.9_TDI"

    def test_reject_buyer_directional_trade_inquiry(self, engine: FilterEngine):
        """Buyer seeking to trade for Audi A6 C5 diesel -> MUST REJECT."""
        res = engine.filter_listing(
            title="Розгляну обмін на Audi A6 C5 дизель",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_reject_vaz_2110_trade_with_surcharge(self, engine: FilterEngine):
        """Seller offering VAZ 2110 with surcharge for Audi A6 -> MUST REJECT."""
        res = engine.filter_listing(
            title="Обмен с моей доплатой ВАЗ 2110 на Audi A6",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons


# ==============================================================================
# Category 2: Parts, Dismantlers and Scrap Listings
# ==============================================================================

class TestChallengerPartsAndScrap:
    """Stress-tests filtering of spare parts and scrap vs legitimate cars with maintenance history."""

    def test_reject_headlights_parts_listing_with_prodam(self, engine: FilterEngine):
        """Parts ad 'Продам фары на ауди а6 с5' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Продам фары на ауди а6 с5",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("PARTS" in r or "DISMANTLING" in r for r in res.reasons)

    def test_pass_car_with_replaced_headlights(self, engine: FilterEngine):
        """Whole car listing with replaced headlights mentioned -> MUST PASS."""
        res = engine.filter_listing(
            title="Ауди А6 С5 1.8Т, заменены передние фары",
            year=2000,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"

    def test_reject_engine_part_sale_18t(self, engine: FilterEngine):
        """Spare engine listing 'Продам двигатель 1.8Т на ауди а6 с5' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Продам двигатель 1.8Т на ауди а6 с5",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("PARTS" in r or "DISMANTLING" in r for r in res.reasons)

    def test_reject_turbine_part_sale_18t(self, engine: FilterEngine):
        """Spare turbo listing 'Продам турбину на Ауди А6 С5 1.8Т' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Продам турбину на Ауди А6 С5 1.8Т",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("PARTS" in r or "DISMANTLING" in r for r in res.reasons)

    def test_reject_gearbox_part_sale_24(self, engine: FilterEngine):
        """Transmission listing 'Продам коробку передач на Ауди А6 С5 2.4' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Продам коробку передач на Ауди А6 С5 2.4",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("PARTS" in r or "DISMANTLING" in r for r in res.reasons)

    def test_reject_general_parts_listing(self, engine: FilterEngine):
        """General parts ad 'Продам запчасти на ауди а6 с5' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Продам запчасти на ауди а6 с5",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("PARTS" in r or "DISMANTLING" in r for r in res.reasons)

    def test_pass_car_with_new_body_parts(self, engine: FilterEngine):
        """Car ad with new hood and bumper installed -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8T 2000, установлен новый оригинальный капот и бампер",
            year=2000,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"

    def test_reject_dismantler_scrap_listing(self, engine: FilterEngine):
        """Auto dismantler scrap ad -> MUST REJECT."""
        res = engine.filter_listing(
            title="Авторазборка Ауди А6 С5 1.8Т 1999",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_DISMANTLING" in res.reasons

    def test_reject_donor_parts_car(self, engine: FilterEngine):
        """Whole vehicle for parts scrap -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.9 TDI 2002 на запчасти целиком",
            year=2002,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "NEGATIVE_DISMANTLING" in res.reasons

    def test_reject_undocumented_donor(self, engine: FilterEngine):
        """Undocumented donor car -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.4 2001 донор из Европы без растаможки",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("DISMANTLING" in r or "COMMERCIAL" in r for r in res.reasons)

    def test_pass_car_with_new_turbine_maintenance(self, engine: FilterEngine):
        """Car ad mentioning new turbine installed during maintenance -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8T 2001, новая турбина, заменено масло и фильтра",
            year=2001,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"


# ==============================================================================
# Category 3: Homoglyph and Dual-Script Evasion
# ==============================================================================

class TestChallengerHomoglyphEvasion:
    """Stress-tests Cyrillic/Latin dual-script and typographic normalization."""

    def test_pass_mixed_cyrillic_audi_latin_a_cyrillic_c5(self, engine: FilterEngine):
        """Mixed Cyrillic/Latin tokens: Cyrillic 'уди' with Latin 'A', Cyrillic 'С', Latin 'T'."""
        res = engine.filter_listing(
            title="Aуди A6 С5 1.8T 2001 седан",
            year=2001,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_pass_comma_decimal_ukrainian_tdi(self, engine: FilterEngine):
        """Decimal comma in 1,9 and Ukrainian 'тді'."""
        res = engine.filter_listing(
            title="A6 C5 1,9 тді 2002 универсал",
            year=2002,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.9_TDI"

    def test_pass_chassis_4b_cyrillic_v(self, engine: FilterEngine):
        """Chassis identifier '4В' with Cyrillic 'В' (\u0412)."""
        res = engine.filter_listing(
            title="Audi A6 4В кузов 2.4 2000",
            year=2000,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"

    def test_pass_ukrainian_i_and_cyrillic_homoglyphs(self, engine: FilterEngine):
        """Ukrainian 'і' (\u0456), Cyrillic 'А' (\u0410), Cyrillic 'С' (\u0421), Cyrillic 'Т' (\u0422)."""
        res = engine.filter_listing(
            title="Аudі А6 С5 1.8Т 1999",
            year=1999,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"

    def test_pass_space_before_cyrillic_t(self, engine: FilterEngine):
        """Space separating volume and engine letter '1.8 Т' with Cyrillic 'Т'."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 Т 2000 седан",
            year=2000,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"

    def test_pass_comma_decimal_compact_turbo(self, engine: FilterEngine):
        """Compact decimal comma with Cyrillic 'турбо': '1,8турбо'."""
        res = engine.filter_listing(
            title="Audi A6 C5 1,8турбо 1999 седан",
            year=1999,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"

    def test_pass_all_cyrillic_lowercase_compact_tdi(self, engine: FilterEngine):
        """All-Cyrillic lowercase compact string '4в 1,9тді'."""
        res = engine.filter_listing(
            title="Ауді А6 4в 1,9тді 2003",
            year=2003,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.9_TDI"

    def test_pass_comma_decimal_24_lpg(self, engine: FilterEngine):
        """Decimal comma in 2,4 petrol/LPG."""
        res = engine.filter_listing(
            title="Audi A6 C5 2,4 газ/бензин 2002",
            year=2002,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "2.4"

    def test_pass_compact_19tdi_no_space(self, engine: FilterEngine):
        """Compact Latin '1.9tdi' without spaces."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.9tdi 2001 седан",
            year=2001,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.9_TDI"


# ==============================================================================
# Category 4: Disallowed Engines
# ==============================================================================

class TestChallengerDisallowedEngines:
    """Stress-tests engine blacklists and non-turbo disqualification."""

    def test_reject_disallowed_25_tdi_v6(self, engine: FilterEngine):
        """2.5 TDI V6 -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.5 TDI v6 2001",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_2.5_TDI" in res.reasons

    def test_reject_disallowed_27_biturbo(self, engine: FilterEngine):
        """2.7 biturbo -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.7 biturbo 2002",
            year=2002,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_2.7T" in res.reasons

    def test_reject_disallowed_18_bez_turbo(self, engine: FilterEngine):
        """1.8 non-turbo ('1.8 без турбо') -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 без турбо 1999",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_reject_disallowed_18_adr(self, engine: FilterEngine):
        """1.8 ADR naturally aspirated 125hp -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 ADR 1998",
            year=1998,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_reject_disallowed_28_petrol(self, engine: FilterEngine):
        """2.8 petrol -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.8 бензин 2000",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_2.8" in res.reasons

    def test_reject_disallowed_30_quattro(self, engine: FilterEngine):
        """3.0 quattro -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 3.0 quattro 2003",
            year=2003,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_3.0" in res.reasons

    def test_reject_disallowed_42_v8_quattro(self, engine: FilterEngine):
        """4.2 V8 quattro -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 4.2 quattro 2002",
            year=2002,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_4.2" in res.reasons

    def test_reject_disallowed_20_alt(self, engine: FilterEngine):
        """2.0 ALT 20V petrol -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.0 ALT 2002",
            year=2002,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_2.0" in res.reasons

    def test_reject_disallowed_18_bez_turbiny_ukrainian(self, engine: FilterEngine):
        """Ukrainian non-turbo phrasing '1.8 без турбіни' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 без турбіни 2000",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_reject_disallowed_18_ne_turbo(self, engine: FilterEngine):
        """Non-turbo phrasing '1.8 не турбо' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 не турбо 1999",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_reject_disallowed_18_atmosfernik(self, engine: FilterEngine):
        """Non-turbo atmospheric phrasing '1.8 атмосферник' -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 1.8 атмосферник 1999",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_reject_disallowed_26_v6(self, engine: FilterEngine):
        """Older 2.6 V6 engine -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C5 2.6 V6 1997",
            year=1997,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "REJECTED_ENGINE_OTHER" in res.reasons


# ==============================================================================
# Category 5: Generation Boundaries and Transition Years
# ==============================================================================

class TestChallengerGenerationBoundaries:
    """Stress-tests generation boundaries (1997 C4 vs C5, 2004/2005 C5 vs C6)."""

    def test_reject_1997_c4_26(self, engine: FilterEngine):
        """1997 C4 generation with 2.6 engine -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 1997 C4 2.6 бензин",
            year=1997,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("GENERATION_C4" in r or "REJECTED_ENGINE" in r for r in res.reasons)

    def test_reject_2004_c6_32(self, engine: FilterEngine):
        """2004 C6 generation with 3.2 engine -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 2004 C6 3.2 FSI седан",
            year=2004,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("GENERATION_C6" in r or "REJECTED_ENGINE" in r for r in res.reasons)

    def test_pass_2005_c5_19tdi(self, engine: FilterEngine):
        """2005 C5 end-of-run Avant 1.9 TDI -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 2005 C5 1.9 TDI универсал",
            year=2005,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"

    def test_reject_1996_c4_out_of_range(self, engine: FilterEngine):
        """1996 C4 out of year range (< 1997) -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C4 1996 2.8 quattro",
            year=1996,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("YEAR_TOO_EARLY" in r or "GENERATION_C4" in r for r in res.reasons)

    def test_reject_2006_c6_out_of_range(self, engine: FilterEngine):
        """2006 C6 out of year range (> 2005) -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 C6 2006 3.0 TDI quattro",
            year=2006,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("YEAR_TOO_LATE" in r or "GENERATION_C6" in r for r in res.reasons)

    def test_pass_1997_transition_18t_c5(self, engine: FilterEngine):
        """1997 transition year with 1.8T (exclusive to C5) -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 1997 1.8T седан",
            year=1997,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "1.8T"

    def test_pass_1997_transition_24_c5(self, engine: FilterEngine):
        """1997 transition year with 2.4 (exclusive to C5) -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 1997 2.4 V6 седан",
            year=1997,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"
        assert res.normalized_engine == "2.4"

    def test_needs_review_1997_transition_19tdi(self, engine: FilterEngine):
        """1997 transition year with 1.9 TDI without C4/C5 badge -> MUST BE NEEDS_REVIEW."""
        res = engine.filter_listing(
            title="Audi A6 1997 1.9 TDI седан",
            year=1997,
        )
        assert res.status == FilterStatus.NEEDS_REVIEW, f"Expected NEEDS_REVIEW but got {res.status} ({res.reasons})"

    def test_needs_review_2004_transition_24_sedan(self, engine: FilterEngine):
        """2004 transition year 2.4 sedan without C5 badge -> MUST BE NEEDS_REVIEW."""
        res = engine.filter_listing(
            title="Audi A6 2004 2.4 седан",
            year=2004,
        )
        assert res.status == FilterStatus.NEEDS_REVIEW, f"Expected NEEDS_REVIEW but got {res.status} ({res.reasons})"

    def test_pass_2004_transition_24_avant(self, engine: FilterEngine):
        """2004 transition year 2.4 Avant (C6 Avant did not exist in 2004) -> MUST PASS."""
        res = engine.filter_listing(
            title="Audi A6 2004 2.4 универсал",
            year=2004,
        )
        assert res.status == FilterStatus.PASS, f"Expected PASS but got {res.status} ({res.reasons})"

    def test_reject_2005_transition_sedan_without_c5(self, engine: FilterEngine):
        """2005 sedan without C5 badge (overwhelmingly C6) -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A6 2005 2.4 седан",
            year=2005,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"


# ==============================================================================
# Category 6: Wrong Audi Models and Other Brands
# ==============================================================================

class TestChallengerWrongModelsAndMakes:
    """Stress-tests rejection of non-A6 models and competitor makes."""

    def test_reject_wrong_model_a4_b5(self, engine: FilterEngine):
        """Audi A4 B5 1.8T -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A4 B5 1.8T 2000",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "WRONG_MODEL_A4" in res.reasons

    def test_reject_wrong_model_a8_d2(self, engine: FilterEngine):
        """Audi A8 D2 2.8 1999 -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi A8 D2 2.8 1999",
            year=1999,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert "WRONG_MODEL_A8" in res.reasons

    def test_reject_wrong_make_passat(self, engine: FilterEngine):
        """Volkswagen Passat B5 1.8T -> MUST REJECT."""
        res = engine.filter_listing(
            title="Volkswagen Passat B5 1.8T 2000",
            year=2000,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("NOT_AUDI_A6" in r or "WRONG_BRAND" in r for r in res.reasons)

    def test_reject_wrong_model_s6(self, engine: FilterEngine):
        """Audi S6 4.2 V8 2001 -> MUST REJECT."""
        res = engine.filter_listing(
            title="Audi S6 4.2 V8 2001",
            year=2001,
        )
        assert res.status == FilterStatus.REJECT, f"Expected REJECT but got {res.status} ({res.reasons})"
        assert any("NOT_AUDI_A6" in r or "REJECTED_ENGINE_4.2" in r for r in res.reasons)
