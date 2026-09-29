"""
Comprehensive Unit Test Suite for Audi A6 C5 Strict Filtering Engine.

Validates:
- All 35 benchmark synthetic test cases (TC-01 to TC-35) from survey_report_filter.md
- Ukrainian and Russian linguistic edge cases
- Cyrillic/Latin homoglyphs (А/A, С/C, Т/T, В/B, etc.)
- Strict negative filters (dismantlers, trade-in targets, buyer inquiries, parts titles)
- Counter-case maintenance phrasing (e.g. "новые запчасти по ходовой")
- Model and generation validation (A6 C5 / 4B vs A4, A8, C4, C6)
- Engine whitelist (1.8T, 2.4, 1.9 TDI) vs blacklist (2.5 TDI, 2.7T, 2.8, 3.0, 4.2, 2.0 ALT, 1.8 NA)
- Ambiguity detection (NEEDS_REVIEW with informative reason codes)
- Payload integration (RawListingPayload, Listing, kwargs)
"""

import pytest

from src.filtering.engine import FilterEngine
from src.filtering.engine_rules import (
    check_engine_blacklist,
    identify_target_engine,
)
from src.filtering.model_rules import (
    check_conflicting_generations,
    extract_year,
    is_avant_body,
    is_c5_explicit,
    validate_brand_and_model,
)
from src.filtering.negative_rules import evaluate_negative_rules
from src.filtering.normalizer import (
    normalize_automotive_identifiers,
    normalize_text,
    replace_homoglyphs,
    to_track_a,
)
from src.models.filter_result import FilterResult, FilterStatus
from src.models.listing import Listing, RawListingPayload


@pytest.fixture
def filter_engine() -> FilterEngine:
    return FilterEngine()


# ==============================================================================
# 1. Benchmark 35 Synthetic Test Cases (TC-01 through TC-35)
# ==============================================================================

class TestBenchmarkSyntheticCases:
    """Covers all 35 synthetic test cases defined in survey_report_filter.md §7."""

    def test_tc01_baseline_positive_18t(self, filter_engine: FilterEngine):
        # TC-01: Audi A6 C5 1.8T 1999 седан механика в отличном состоянии
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1.8T 1999 седан механика в отличном состоянии",
            year=1999,
        )
        assert res.is_passed is True
        assert res.status == FilterStatus.PASS
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"
        assert "AUDI_A6_C5_1.8T" in res.reasons

    def test_tc02_cyrillic_homoglyphs_18t_lpg(self, filter_engine: FilterEngine):
        # TC-02: Cyrillic homoglyphs А6 С5 1.8Т + LPG
        res = filter_engine.filter_listing(
            title="Продам Ауди \u04106 \u04215 1.8\u0422 2000 года газ/бензин, климат",
            year=2000,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_tc03_chassis_code_4b_24_v6(self, filter_engine: FilterEngine):
        # TC-03: Chassis code 4B + 2.4 V6
        res = filter_engine.filter_listing(
            title="Audi A6 4B 2.4 V6 2002 автомат, кожа, люк, европеец",
            year=2002,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"
        assert "AUDI_A6_C5_2.4" in res.reasons

    def test_tc04_ukrainian_a6_19tdi_wagon(self, filter_engine: FilterEngine):
        # TC-04: Ukrainian Ауді, тді, універсал
        res = filter_engine.filter_listing(
            title="Ауді А6 1.9 тді 2003 універсал в гарному стані, економна",
            year=2003,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"
        assert "AUDI_A6_C5_1.9_TDI" in res.reasons

    def test_tc05_pre_facelift_19tdi_afn(self, filter_engine: FilterEngine):
        # TC-05: Pre-facelift 110hp AFN engine
        res = filter_engine.filter_listing(
            title="Audi A6 1998 1.9 TDI 81kW AFN седан",
            year=1998,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"

    def test_tc06_2004_c5_avant_awx(self, filter_engine: FilterEngine):
        # TC-06: 2004 C5 Avant with AWX engine
        res = filter_engine.filter_listing(
            title="Audi A6 Avant 2004 1.9 TDI 96kW AWX 6-МКПП",
            year=2004,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"

    def test_tc07_vernacular_slang_gorbataya_24(self, filter_engine: FilterEngine):
        # TC-07: Vernacular slang горбатая
        res = filter_engine.filter_listing(
            title="Ауди А6 горбатая 2001 2.4 газ бензин на ходу",
            year=2001,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"

    def test_tc08_2005_c5_avant_end_of_run(self, filter_engine: FilterEngine):
        # TC-08: 2005 C5 Avant final version
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2005 Avant 1.9 TDI фінальна версія",
            year=2005,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"

    def test_tc09_high_output_18t_ajl_180hp(self, filter_engine: FilterEngine):
        # TC-09: High-output AJL 180hp variant
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1.8 Turbo 180hp AJL quattro рестайл",
            year=2001,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_tc10_1997_transition_18t_guarantees_c5(self, filter_engine: FilterEngine):
        # TC-10: 1997 transition: 1.8T guarantees C5
        res = filter_engine.filter_listing(
            title="Audi A6 1997 1.8T седан",
            year=1997,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_tc11_reject_25_tdi(self, filter_engine: FilterEngine):
        # TC-11: REJECTED_ENGINE_2.5_TDI
        res = filter_engine.filter_listing(
            title="Audi A6 2001 2.5 TDI v6 полный привод",
            year=2001,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_2.5_TDI" in res.reasons

    def test_tc12_reject_27t_biturbo(self, filter_engine: FilterEngine):
        # TC-12: REJECTED_ENGINE_2.7T
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2.7 biturbo quattro мех 250 л.с.",
            year=2000,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_2.7T" in res.reasons

    def test_tc13_reject_28_petrol(self, filter_engine: FilterEngine):
        # TC-13: REJECTED_ENGINE_2.8
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1999 2.8 газ/бензин автомат",
            year=1999,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_2.8" in res.reasons

    def test_tc14_reject_30_petrol(self, filter_engine: FilterEngine):
        # TC-14: REJECTED_ENGINE_3.0
        res = filter_engine.filter_listing(
            title="Audi A6 2002 3.0 бензин седан рестайлинг",
            year=2002,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_3.0" in res.reasons

    def test_tc15_reject_42_v8_s6(self, filter_engine: FilterEngine):
        # TC-15: REJECTED_ENGINE_4.2
        res = filter_engine.filter_listing(
            title="Audi S6 C5 2000 4.2 V8 340 лс",
            year=2000,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_4.2" in res.reasons

    def test_tc16_reject_20_alt_petrol(self, filter_engine: FilterEngine):
        # TC-16: REJECTED_ENGINE_2.0
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2002 2.0 ALT бензин масло не бере",
            year=2002,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_2.0" in res.reasons

    def test_tc17_reject_18_adr_non_turbo(self, filter_engine: FilterEngine):
        # TC-17: REJECTED_ENGINE_1.8_NON_TURBO
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1998 1.8 бензин 125 л.с. простой мотор ADR",
            year=1998,
        )
        assert res.is_rejected is True
        assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_tc18_reject_1995_c4(self, filter_engine: FilterEngine):
        # TC-18: YEAR_TOO_EARLY_1995 / GENERATION_C4
        res = filter_engine.filter_listing(
            title="Audi A6 1995 2.6 газ бензин климат C4",
            year=1995,
        )
        assert res.is_rejected is True
        assert any("YEAR" in r or "GENERATION" in r for r in res.reasons)

    def test_tc19_reject_2006_c6(self, filter_engine: FilterEngine):
        # TC-19: GENERATION_C6 / YEAR_TOO_LATE_2006
        res = filter_engine.filter_listing(
            title="Audi A6 C6 2006 2.4 бензин седан",
            year=2006,
        )
        assert res.is_rejected is True
        assert any("GENERATION" in r or "YEAR" in r for r in res.reasons)

    def test_tc20_reject_wrong_model_a4(self, filter_engine: FilterEngine):
        # TC-20: WRONG_MODEL_A4
        res = filter_engine.filter_listing(
            title="Audi A4 B6 1.8T 2002 универсал из Германии",
            year=2002,
        )
        assert res.is_rejected is True
        assert "WRONG_MODEL_A4" in res.reasons

    def test_tc21_reject_wrong_model_a8(self, filter_engine: FilterEngine):
        # TC-21: WRONG_MODEL_A8
        res = filter_engine.filter_listing(
            title="Audi A8 D2 1999 2.8 quattro",
            year=1999,
        )
        assert res.is_rejected is True
        assert "WRONG_MODEL_A8" in res.reasons

    def test_tc22_reject_dismantling_parts_car(self, filter_engine: FilterEngine):
        # TC-22: NEGATIVE_DISMANTLING ("по запчастям есть всё шрот")
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2.4 по запчастям есть всё шрот",
            year=2001,
        )
        assert res.is_rejected is True
        assert "NEGATIVE_DISMANTLING" in res.reasons

    def test_tc23_reject_ukrainian_rozbirka_shrot(self, filter_engine: FilterEngine):
        # TC-23: NEGATIVE_DISMANTLING ("Розбірка Audi A6 C5 1.9 TDI шрот")
        res = filter_engine.filter_listing(
            title="Розбірка Audi A6 C5 1.9 TDI шрот запчастини з Польщі",
            year=2002,
        )
        assert res.is_rejected is True
        assert "NEGATIVE_DISMANTLING" in res.reasons

    def test_tc24_reject_parts_title_turbina(self, filter_engine: FilterEngine):
        # TC-24: NEGATIVE_PARTS_TITLE ("Турбина 1.8T Audi A6 C5")
        res = filter_engine.filter_listing(
            title="Турбина 1.8T Audi A6 C5 Passat B5 оригинал б/у",
        )
        assert res.is_rejected is True
        assert "NEGATIVE_PARTS_TITLE" in res.reasons

    def test_tc25_reject_parts_title_fary(self, filter_engine: FilterEngine):
        # TC-25: NEGATIVE_PARTS_TITLE ("Фары Audi A6 C5")
        res = filter_engine.filter_listing(
            title="Фары Audi A6 C5 рестайлинг ксенон пара",
        )
        assert res.is_rejected is True
        assert "NEGATIVE_PARTS_TITLE" in res.reasons

    def test_tc26_reject_buyer_inquiry_kuplyu(self, filter_engine: FilterEngine):
        # TC-26: NEGATIVE_BUYER_INQUIRY ("Куплю Audi A6 C5 1.9 TDI")
        res = filter_engine.filter_listing(
            title="Куплю Audi A6 C5 1.9 TDI до 4000$ для себя",
            year=2002,
        )
        assert res.is_rejected is True
        assert "NEGATIVE_BUYER_INQUIRY" in res.reasons

    def test_tc27_reject_trade_target_bmw_to_audi(self, filter_engine: FilterEngine):
        # TC-27: NEGATIVE_TRADE_IN_TARGET ("BMW E39 2.5d 2001 обмен на Audi A6 C5")
        res = filter_engine.filter_listing(
            title="BMW E39 2.5d 2001 обмен на Audi A6 C5 1.9 TDI",
            year=2001,
        )
        assert res.is_rejected is True
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_tc28_reject_trade_target_passat_to_audi(self, filter_engine: FilterEngine):
        # TC-28: NEGATIVE_TRADE_IN_TARGET ("Продам Passat B5, можливий обмін на Ауді А6 С5")
        res = filter_engine.filter_listing(
            title="Продам Passat B5, можливий обмін на Ауді А6 С5 з доплатою",
            year=2000,
        )
        assert res.is_rejected is True
        assert "NEGATIVE_TRADE_IN_TARGET" in res.reasons

    def test_tc29_needs_review_engine_not_identified(self, filter_engine: FilterEngine):
        # TC-29: ENGINE_NOT_IDENTIFIED ("Audi A6 C5 2001 года отличное авто")
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2001 года отличное авто срочно торг",
            year=2001,
        )
        assert res.is_review_needed is True
        assert res.status == FilterStatus.NEEDS_REVIEW
        assert "ENGINE_NOT_IDENTIFIED" in res.reasons
        assert res.normalized_generation == "C5"
        assert res.normalized_engine is None

    def test_tc30_needs_review_ambiguous_18(self, filter_engine: FilterEngine):
        # TC-30: AMBIGUOUS_1.8_CHECK_TURBO ("Audi A6 C5 1.8 1999 бензин седан")
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1.8 1999 бензин седан",
            year=1999,
        )
        assert res.is_review_needed is True
        assert res.status == FilterStatus.NEEDS_REVIEW
        assert "AMBIGUOUS_1.8_CHECK_TURBO" in res.reasons

    def test_tc31_needs_review_1997_19tdi(self, filter_engine: FilterEngine):
        # TC-31: YEAR_1997_1.9TDI_CHECK_C4_OR_C5 ("Audi A6 1997 1.9 TDI седан")
        res = filter_engine.filter_listing(
            title="Audi A6 1997 1.9 TDI седан",
            year=1997,
        )
        assert res.is_review_needed is True
        assert res.status == FilterStatus.NEEDS_REVIEW
        assert "YEAR_1997_1.9TDI_CHECK_C4_OR_C5" in res.reasons
        assert res.normalized_engine == "1.9_TDI"

    def test_tc32_needs_review_2004_24_sedan(self, filter_engine: FilterEngine):
        # TC-32: YEAR_2004_2.4_CHECK_C5_OR_C6 ("Audi A6 2004 2.4 седан автомат")
        res = filter_engine.filter_listing(
            title="Audi A6 2004 2.4 седан автомат",
            year=2004,
            body_type="sedan",
        )
        assert res.is_review_needed is True
        assert res.status == FilterStatus.NEEDS_REVIEW
        assert "YEAR_2004_2.4_CHECK_C5_OR_C6" in res.reasons
        assert res.normalized_engine == "2.4"

    def test_tc33_reject_2005_sedan_without_c5(self, filter_engine: FilterEngine):
        # TC-33: YEAR_2005_CHECK_C5_OR_C6 ("Audi A6 2005 2.4 бензин седан")
        res = filter_engine.filter_listing(
            title="Audi A6 2005 2.4 бензин седан",
            year=2005,
            body_type="sedan",
        )
        assert res.is_rejected is True
        assert "YEAR_2005_CHECK_C5_OR_C6" in res.reasons

    def test_tc34_pass_outbound_trade_offer_from_c5_owner(self, filter_engine: FilterEngine):
        # TC-34: Audi owner proposing trade for bus/crossover
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1.8T, возможен обмен на бус или кроссовер",
            year=2000,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_tc35_pass_maintenance_phrasing_new_parts(self, filter_engine: FilterEngine):
        # TC-35: Legitimate maintenance ("новые запчасти по ходовой")
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2.4 2001 идеал, новые запчасти по ходовой",
            year=2001,
        )
        assert res.is_passed is True
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"


# ==============================================================================
# 2. Text Normalization, Dual-Script, & Homoglyphs Tests
# ==============================================================================

class TestNormalizerAndHomoglyphs:
    """Detailed unit tests for normalizer.py functions."""

    def test_unicode_nfkc_and_zero_width_removal(self):
        dirty = "Audi\u200B A6\uFEFF \u00A0C5\t\n  1.8T"
        clean = normalize_text(dirty)
        assert clean == "Audi A6 C5 1.8T"

    def test_decimal_comma_conversion(self):
        text = "Audi A6 1,8T, 2,4 газ/бензин, 1,9 TDI"
        clean = normalize_text(text)
        assert "1.8T" in clean
        assert "2.4" in clean
        assert "1.9 TDI" in clean

    def test_homoglyph_replacement_isolated_tokens(self):
        # Cyrillic А6, С5, 1.8Т, 4В
        cyrillic_token = "\u04106 \u04215 1.8\u0422 4\u0412"
        latin_token = replace_homoglyphs(cyrillic_token)
        assert latin_token == "A6 C5 1.8T 4B"

    def test_automotive_identifiers_preserves_russian_words(self):
        # Should transform Cyrillic А6 and С5 to Latin A6 and C5,
        # but preserve words like 'Продам' and 'машина'
        mixed = "Продам \u04106 \u04215 хорошая машина"
        res = normalize_automotive_identifiers(mixed)
        assert "A6" in res
        assert "C5" in res
        assert "Продам" in res
        assert "машина" in res

    def test_track_a_lowercasing(self):
        raw = "Продам АУДИ А6 С5 1,8Т"
        track_a = to_track_a(raw)
        assert "продам" in track_a
        assert "1.8" in track_a


# ==============================================================================
# 3. Contextual Negative Filter Edge Cases (RU & UA)
# ==============================================================================

class TestNegativeFilterEdgeCases:
    """Validates negative rules across Russian and Ukrainian linguistic patterns."""

    def test_trade_in_target_variations(self):
        samples = [
            "BMW 525 2002 возможен обмен на Audi A6 C5",
            "Опель Вектра, цікавить обмін на Ауді А6",
            "Mercedes E220 розгляну обмін на Audi A6",
            "Фольксваген Пассат поменяю на Audi",
            "Шкода Октавія поміняю на Ауді А6 С5",
            "ВАЗ 2110 рассмотрю обмен на авто Audi",
        ]
        for s in samples:
            reason = evaluate_negative_rules(s, "")
            assert reason == "NEGATIVE_TRADE_IN_TARGET", f"Failed on: {s}"

    def test_seller_trade_allowed(self):
        allowed_samples = [
            "Audi A6 C5 1.8T 2000 обмен на универсал дизель",
            "Ауді А6 С5 2.4 обмін на дешевше авто з доплатою",
            "Audi A6 1.9 TDI 2002 без обміну",
            "Audi A6 C5 обмен не интересует",
        ]
        for s in allowed_samples:
            reason = evaluate_negative_rules(s, "")
            assert reason is None, f"Incorrectly triggered negative on: {s}"

    def test_buyer_inquiries(self):
        buyers = [
            "Куплю Ауди А6 С5 в Киеве",
            "Шукаю Audi A6 1.9 TDI у доброму стані",
            "Придбаю Audi A6 C5 для себе",
            "Ищу Audi A6 C5 с мотором 1.8T",
            "Купим Audi A6 в любом состоянии",
        ]
        for b in buyers:
            reason = evaluate_negative_rules(b, "")
            assert reason == "NEGATIVE_BUYER_INQUIRY", f"Failed on: {b}"

    def test_dismantling_and_scrap(self):
        scraps = [
            "Авторозбірка Audi A6 C5 Львів шрот",
            "Авторазборка Ауди А6 С5 запчасти б/у",
            "Audi A6 C5 2.5 TDI в разбор",
            "Audi A6 1999 на розборку",
            "Audi A6 C5 донор из Польши без документов",
            "Ауди А6 С5 без двигателя и кпп",
            "Голый кузов с документами Audi A6 C5",
            "Разбираю Audi A6 C5 по запчастям",
            "Розбираємо Ауді А6 С5 універсал",
        ]
        for s in scraps:
            reason = evaluate_negative_rules(s, "")
            assert reason == "NEGATIVE_DISMANTLING", f"Failed on: {s}"

    def test_legitimate_car_maintenance_not_rejected(self):
        maintenance = [
            "Audi A6 C5 1.8T 2000, новые запчасти по ходовой, масло не берет",
            "Ауді А6 С5 1.9 TDI замінено запчастини по підвісці",
            "Audi A6 2.4 2002 замінені ремені та помпа, авто на повному ходу",
        ]
        for m in maintenance:
            reason = evaluate_negative_rules(m, "")
            assert reason is None, f"Incorrectly flagged maintenance as scrap: {m}"

    def test_spare_parts_titles(self):
        parts = [
            "Капот Audi A6 C5 рестайлинг черный",
            "Крыло левое Ауди А6 С5 оригинал",
            "Двигатель 1.8T AEB Audi A6 C5 Passat B5",
            "АКПП 5HP19 Audi A6 C5 2.4",
            "Турбіна 1.9 TDI AFN Audi A6",
            "Салон кожа Recaro Audi A6 C5 комплект",
        ]
        for p in parts:
            reason = evaluate_negative_rules(p, "")
            assert reason == "NEGATIVE_PARTS_TITLE", f"Failed on: {p}"

    def test_commercial_services(self):
        services = [
            "Пригон авто из Европы под ключ",
            "Автоподбор Киев проверка Audi перед покупкой",
            "Підбір авто Луцьк та пригон",
            "Помощь в растаможке авто из США и ЕС",
            "Аренда авто под выкуп без залога",
        ]
        for s in services:
            reason = evaluate_negative_rules(s, "")
            assert reason == "NEGATIVE_COMMERCIAL_SERVICE", f"Failed on: {s}"


# ==============================================================================
# 4. Model and Generation Rules Edge Cases
# ==============================================================================

class TestModelAndGenerationRules:
    """Validates brand, model, and generation demarcation."""

    def test_brand_validation_structured(self):
        valid, reason = validate_brand_and_model(
            structured_brand="Audi",
            structured_model="A6",
            title="Audi A6 C5 1.8T",
            full_text="audi a6 c5 1.8t",
        )
        assert valid is True

        valid_bmw, reason_bmw = validate_brand_and_model(
            structured_brand="BMW",
            structured_model="520",
            title="BMW 520 2001",
            full_text="bmw 520 2001",
        )
        assert valid_bmw is False
        assert reason_bmw == "WRONG_BRAND"

    def test_reject_a4_a8_structured(self):
        valid_a4, reason_a4 = validate_brand_and_model(
            structured_brand="Audi",
            structured_model="A4",
            title="Audi A4 1.8T",
            full_text="audi a4 1.8t",
        )
        assert valid_a4 is False
        assert reason_a4 == "WRONG_MODEL_A4"

        valid_a8, reason_a8 = validate_brand_and_model(
            structured_brand="Audi",
            structured_model="A8",
            title="Audi A8 2.8",
            full_text="audi a8 2.8",
        )
        assert valid_a8 is False
        assert reason_a8 == "WRONG_MODEL_A8"

    def test_vernacular_c5_detection(self):
        assert is_c5_explicit("ауди а6 горбатая 2001") is True
        assert is_c5_explicit("ауді а6 горбатка 1999") is True
        assert is_c5_explicit("audi a6 капля 2002") is True
        assert is_c5_explicit("audi a6 черепаха 2000") is True
        assert is_c5_explicit("audi a6 4b 2001") is True
        assert is_c5_explicit("audi a6 4в 2001") is True

    def test_conflicting_generations(self):
        assert check_conflicting_generations("audi a6 c4 1996") == "GENERATION_C4"
        assert check_conflicting_generations("audi a6 4a 1995") == "GENERATION_C4"
        assert check_conflicting_generations("audi a6 c6 2007") == "GENERATION_C6"
        assert check_conflicting_generations("audi a6 4f 2008") == "GENERATION_C6"
        assert check_conflicting_generations("audi a6 c7 2012") == "GENERATION_C7"
        assert check_conflicting_generations("audi a6 c8 2019") == "GENERATION_C8"

    def test_year_extractor(self):
        assert extract_year(None, "Audi A6 C5 1999 1.8T") == 1999
        assert extract_year(2001, "Audi A6 C5 1999") == 2001
        assert extract_year(None, "Audi A6 C5 без года") is None


# ==============================================================================
# 5. Engine Rules Edge Cases
# ==============================================================================

class TestEngineRules:
    """Validates whitelist and blacklist engine evaluation."""

    def test_identify_18t_patterns(self):
        samples = [
            "Audi A6 1.8T 1999",
            "Audi A6 1.8\u0422 2000",
            "Audi A6 1.8 Turbo 2001",
            "Audi A6 1.8 турбо 1998",
            "Audi A6 1.8 150 л.с.",
            "Audi A6 1.8 180hp AJL",
        ]
        for s in samples:
            engine, ambiguity = identify_target_engine(s)
            assert engine == "1.8T", f"Failed on: {s}"
            assert ambiguity is None

    def test_identify_24_patterns(self):
        samples = [
            "Audi A6 2.4 2001",
            "Audi A6 2.4 V6 2002",
            "Audi A6 2.4 газ/бензин",
            "Audi A6 2.4 ГБО 2000",
            "Audi A6 2.4 бензин BDV",
        ]
        for s in samples:
            engine, ambiguity = identify_target_engine(s)
            assert engine == "2.4", f"Failed on: {s}"
            assert ambiguity is None

    def test_identify_19tdi_patterns(self):
        samples = [
            "Audi A6 1.9 TDI 2003",
            "Audi A6 1.9 тді універсал",
            "Audi A6 1.9 тди 1999",
            "Audi A6 1.9 дизель 2002",
            "Audi A6 1.9 81kW AFN",
            "Audi A6 1.9 130 л.с. AWX",
            "Audi A6 1.9 насос-форсунка",
        ]
        for s in samples:
            engine, ambiguity = identify_target_engine(s)
            assert engine == "1.9_TDI", f"Failed on: {s}"
            assert ambiguity is None

    def test_blacklisted_engines(self):
        assert check_engine_blacklist("Audi A6 2.5 TDI v6") == "REJECTED_ENGINE_2.5_TDI"
        assert check_engine_blacklist("Audi A6 2.7 biturbo") == "REJECTED_ENGINE_2.7T"
        assert check_engine_blacklist("Audi A6 2.8 quattro") == "REJECTED_ENGINE_2.8"
        assert check_engine_blacklist("Audi A6 3.0 бензин") == "REJECTED_ENGINE_3.0"
        assert check_engine_blacklist("Audi S6 4.2 V8") == "REJECTED_ENGINE_4.2"
        assert check_engine_blacklist("Audi A6 2.0 ALT") == "REJECTED_ENGINE_2.0"

    def test_ambiguous_18_without_turbo(self):
        engine, ambiguity = identify_target_engine("Audi A6 C5 1.8 1999 бензин")
        assert engine is None
        assert ambiguity == "AMBIGUOUS_1.8_CHECK_TURBO"

    def test_rejected_18_na_adr(self):
        engine, ambiguity = identify_target_engine("Audi A6 C5 1.8 125 л.с. простой мотор ADR")
        assert engine is None
        assert ambiguity == "REJECTED_ENGINE_1.8_NON_TURBO"


# ==============================================================================
# 6. Pydantic Model Integration (RawListingPayload & Listing)
# ==============================================================================

class TestPayloadIntegration:
    """Validates FilterEngine integration with Pydantic listing models."""

    def test_filter_raw_listing_payload(self, filter_engine: FilterEngine):
        payload = RawListingPayload(
            source="auto_ria",
            source_id="12345",
            url="https://auto.ria.com/auto_audi_a6_12345.html",
            title="Audi A6 C5 1.8T 1999 седан",
            year=1999,
            engine="1.8 Turbo",
            fuel_type="petrol",
        )
        res = filter_engine.filter_listing(payload)
        assert res.is_passed is True
        assert res.normalized_engine == "1.8T"
        assert res.normalized_generation == "C5"

    def test_filter_unified_listing(self, filter_engine: FilterEngine):
        listing = Listing(
            source="olx",
            source_id="67890",
            url="https://www.olx.ua/d/uk/obyavlenie/audi-a6-ID67890.html",
            title="Audi A6 4B 2.4 V6 2002",
            year=2002,
            engine="2.4",
            brand="Audi",
            model="A6",
        )
        res = filter_engine.filter_listing(listing)
        assert res.is_passed is True
        assert res.normalized_engine == "2.4"
        assert res.normalized_generation == "C5"

    def test_filter_dict_input(self, filter_engine: FilterEngine):
        listing_dict = {
            "title": "Ауді А6 1.9 тді 2003 універсал",
            "year": 2003,
            "engine": "1.9 TDI",
        }
        res = filter_engine.filter_listing(listing_dict)
        assert res.is_passed is True
        assert res.normalized_engine == "1.9_TDI"
        assert res.normalized_generation == "C5"


class TestRemediationM2EdgeCases:
    """Verifies edge cases remediated in M2 remediation pass."""

    def test_ambiguous_multiple_engines_unshadowed(self, filter_engine: FilterEngine):
        """Listing mentioning multiple valid engines must return AMBIGUOUS_MULTIPLE_ENGINES with confidence 0.5."""
        res = filter_engine.filter_listing(
            title="Audi A6 C5 2000 1.8T или 2.4",
            year=2000,
        )
        assert res.status == FilterStatus.NEEDS_REVIEW
        assert res.confidence == 0.5
        assert "AMBIGUOUS_MULTIPLE_ENGINES" in res.reasons
        assert res.normalized_engine is None

    def test_slang_inflections_c5_detection(self, filter_engine: FilterEngine):
        """Inflected vernacular slang forms (черепаху, горбатку, каплю) confirm C5 generation."""
        for title, eng in [
            ("Продам Ауді А6 черепаху 1.8Т 1999", "1.8T"),
            ("Ауди А6 горбатку 2.4 2001", "2.4"),
            ("Продам каплю Ауди А6 1.9 тди 2002", "1.9_TDI"),
        ]:
            res = filter_engine.filter_listing(title=title)
            assert res.status == FilterStatus.PASS, f"Failed for {title}: {res.reasons}"
            assert res.normalized_engine == eng
            assert res.normalized_generation == "C5"

    def test_ukrainian_turbovanyi_positive(self, filter_engine: FilterEngine):
        """Ukrainian 'турбований' matches 1.8T whitelist."""
        res = filter_engine.filter_listing(
            title="Audi A6 C5 1.8 турбований двигун 2001",
            year=2001,
        )
        assert res.status == FilterStatus.PASS
        assert res.normalized_engine == "1.8T"

    def test_non_turbo_18_phrasings_rejected(self, filter_engine: FilterEngine):
        """Phrasings explicitly negating turbo on 1.8 must be rejected as REJECTED_ENGINE_1.8_NON_TURBO."""
        for title in [
            "Audi A6 C5 1.8 без турбо 1999",
            "Audi A6 C5 1.8 не турбо 1999",
            "Audi A6 C5 1.8 без турбіни 2000",
            "Audi A6 C5 1.8 без турбины 2000",
            "Audi A6 C5 1.8 без turbo 1999",
            "Audi A6 C5 1.8 не turbo 1999",
        ]:
            res = filter_engine.filter_listing(title=title, year=2000)
            assert res.status == FilterStatus.REJECT, f"Failed for {title}: got {res.status}"
            assert "REJECTED_ENGINE_1.8_NON_TURBO" in res.reasons

    def test_parts_titles_with_prefixes_rejected(self, filter_engine: FilterEngine):
        """Parts listings with sale prefixes must be rejected as NEGATIVE_PARTS_TITLE."""
        for title in [
            "Продам фары на ауди а6 с5",
            "Продаю капот на audi a6 c5",
            "Продаж запчастин audi a6 c5",
            "Продається двигун 1.8Т ауди а6 с5",
            "Продам коробку передач на Ауди А6 С5 2.4",
            "Продам турбину на Ауди А6 С5 1.8Т",
            "Продам запчасти на ауди а6 с5",
        ]:
            res = filter_engine.filter_listing(title=title, year=2000)
            assert res.status == FilterStatus.REJECT, f"Failed for {title}: got {res.status} ({res.reasons})"
            assert "NEGATIVE_PARTS_TITLE" in res.reasons

