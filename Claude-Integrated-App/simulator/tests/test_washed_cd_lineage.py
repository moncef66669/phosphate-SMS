"""Focused regression tests for raw-to-washed Cd-profile lineage."""

import pytest

from mph_sim.datasets import M16_HUMID_STOCK_GRADE_IDS, end_to_end
from mph_sim.grade_ids import (
    MERCHANT_GRADE_FAMILIES,
    RAW_BT_GRADE_IDS,
    WASHED_GRADE_BY_RAW_AND_FAMILY,
    WASHED_GRADE_IDS,
    canonicalize_scenario_grade_ids,
)
from mph_sim.validation import validate_scenario


def test_washed_catalogue_preserves_family_and_raw_cd_lineage():
    scenario = end_to_end()

    assert set(WASHED_GRADE_IDS) == {
        "TBT", "TBT-HIGH-CD", "TBT-LOW-CD",
        "BT", "BT-HIGH-CD", "BT-LOW-CD",
        "MT", "MT-HIGH-CD", "MT-LOW-CD",
    }
    assert not [
        issue for issue in validate_scenario(scenario)
        if issue.severity == "ERROR"
    ]

    for family in MERCHANT_GRADE_FAMILIES:
        global_grade = scenario.grade(
            WASHED_GRADE_BY_RAW_AND_FAMILY["RAW-BT"][family])
        high_grade = scenario.grade(
            WASHED_GRADE_BY_RAW_AND_FAMILY["RAW-BT-HIGH-CD"][family])
        low_grade = scenario.grade(
            WASHED_GRADE_BY_RAW_AND_FAMILY["RAW-BT-LOW-CD"][family])

        assert global_grade.source_grade_id == "RAW-BT"
        assert high_grade.source_grade_id == "RAW-BT-HIGH-CD"
        assert low_grade.source_grade_id == "RAW-BT-LOW-CD"
        assert high_grade.evidence.status == "ASSUMED"
        assert low_grade.evidence.status == "ASSUMED"

        assert high_grade.quality["Cd"] > global_grade.quality["Cd"] \
            > low_grade.quality["Cd"]
        assert low_grade.quality["BPL"] > global_grade.quality["BPL"] \
            > high_grade.quality["BPL"]
        assert high_grade.quality["SiO2"] > global_grade.quality["SiO2"] \
            > low_grade.quality["SiO2"]

        for variant in (high_grade, low_grade):
            assert variant.beneficiation_yield == pytest.approx(
                global_grade.beneficiation_yield)
            assert variant.raw_moisture_pct == pytest.approx(
                global_grade.raw_moisture_pct)
            assert variant.product_moisture_pct == pytest.approx(
                global_grade.product_moisture_pct)

    assert {
        scenario.grade(grade_id).source_grade_id
        for grade_id in WASHED_GRADE_IDS
    } == set(RAW_BT_GRADE_IDS)


def test_active_low_cd_wash_routes_emit_low_cd_washed_grade():
    scenario = end_to_end()
    outputs = {
        transfer.id: transfer.output_grade
        for transfer in scenario.process_transfers
    }

    assert outputs["BG-L2-WASH"] == "BT-LOW-CD"
    assert outputs["BG-L2-PRODUCT-STACK"] == "BT-LOW-CD"
    assert outputs["LOU-WASH-L2-LOW-CD"] == "BT-LOW-CD"
    assert outputs["LOU-PRODUCT-STACK-L2"] == "BT-LOW-CD"
    assert outputs["BG-L1-WASH"] == "TBT"
    assert outputs["BG-L3-WASH"] == "TBT"

    assert scenario.space("BG-WASH-L2-WIP").grade == "BT-LOW-CD"
    assert scenario.space("BG-P2").grade == "BT-LOW-CD"
    assert scenario.space("LT-WASH-L2-WIP").grade == "BT-LOW-CD"
    assert scenario.space("LT-P2").grade == "BT-LOW-CD"
    assert next(
        segment for segment in scenario.segments
        if segment.space_id == "BG-P2"
    ).grade == "BT-LOW-CD"
    assert {
        component["grade_id"]
        for component in scenario.config["PILE_GRADE_DISTRIBUTIONS"]["LT-P2"]
    } == {"BT-LOW-CD", "MT-LOW-CD"}

    for profile_id in (
            "M16-WASHED-HUMID", "BEN-WASHED-FILTERED",
            "BEN-SETTLED-PRODUCT", "LOU-WASHED-PRODUCT"):
        profile = scenario.material_profile(profile_id)
        # Every washed profile declares the full raw-to-washed Cd lineage.
        # M16 additionally declares its own received-product families, whose
        # vectors carry the CO2 assay PAP's G10 window needs (25-Sep-2026);
        # those families have no upstream raw lineage and appear nowhere else.
        extra = (set(M16_HUMID_STOCK_GRADE_IDS)
                 if profile_id == "M16-WASHED-HUMID" else set())
        assert set(profile.grade_ids) == set(WASHED_GRADE_IDS) | extra


def test_washed_source_lineage_is_validated_and_legacy_blank_is_backfilled():
    scenario = end_to_end()
    grade = scenario.grade("BT-LOW-CD")

    grade.source_grade_id = "RAW-BT-HIGH-CD"
    assert any(
        issue.code == "WASHED_SOURCE_GRADE_MISMATCH"
        and issue.entity == "BT-LOW-CD"
        for issue in validate_scenario(scenario)
    )

    grade.source_grade_id = ""
    assert canonicalize_scenario_grade_ids(scenario)
    assert grade.source_grade_id == "RAW-BT-LOW-CD"
    assert not [
        issue for issue in validate_scenario(scenario)
        if issue.code == "WASHED_SOURCE_GRADE_MISMATCH"
    ]
