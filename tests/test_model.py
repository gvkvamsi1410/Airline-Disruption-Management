"""Model correctness tests.

Run:  PYTHONPATH=. pytest -q
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from irop.solve import (  # noqa: E402
    disruption_from_flight,
    disruptions_from_flights,
    find_duplicate_tail_picks,
    load_inputs_from_excel,
    solve_scenario,
)

EXCEL = ROOT / "assets" / "data_asset" / "IropInputs.xlsx"


@pytest.fixture(scope="module")
def inputs():
    if not EXCEL.exists():
        import scripts.build_inputs_xlsx as b
        b.main()
    return load_inputs_from_excel(EXCEL)


@pytest.fixture(scope="module")
def base(inputs):
    return solve_scenario(inputs)


# --------------------------------------------------------------------------
def test_base_reproduces_reference(base):
    s = base["summary"]
    assert s["solve_status"] == "integer optimal solution"
    assert s["total_delay_min"] == 40
    assert s["num_swapped"] == 2
    assert s["num_cancelled"] == 0
    assert s["objective_value"] == pytest.approx(340.0)


def test_base_swaps_f203_f204_to_a3(base):
    fr = base["flight_recovery"].set_index("flight")
    for f in ("F203", "F204"):
        assert fr.at[f, "assigned_tail"] == "A3"
        assert fr.at[f, "swapped"]
        assert fr.at[f, "delay_min"] == 20


def test_base_feasible(base):
    rep = base["feasibility"]
    assert rep.ok, rep.violations
    assert rep.checks_run > 50


def test_all_pre_disruption_flights_frozen(base):
    dm = base["dm"]
    assert dm.disruption_start_min == 575
    assert dm.pre_flight_ids == {
        "F101", "F102", "F201", "F202", "F301", "F302", "F401", "F402", "F501", "F502",
    }
    fr = base["flight_recovery"].set_index("flight")
    for f in dm.pre_flight_ids:
        assert fr.at[f, "assigned_tail"] == fr.at[f, "orig_tail"]
        assert fr.at[f, "delay_min"] == 0
        assert not fr.at[f, "cancelled"]


def test_model_within_community_edition_cap(base):
    s = base["summary"]
    assert s["num_variables"] < 1000
    assert s["num_constraints"] < 1000


def test_no_swap_baseline_is_worse(inputs, base):
    nb = solve_scenario(inputs, forbid_swaps=True)
    assert nb["feasibility"].ok
    assert nb["summary"]["num_swapped"] == 0
    assert nb["summary"]["total_delay_min"] == 390
    assert nb["summary"]["total_delay_min"] > base["summary"]["total_delay_min"]


def test_every_flight_covered_or_cancelled(base):
    fr = base["flight_recovery"]
    for _, r in fr.iterrows():
        assert bool(r["cancelled"]) ^ (r["assigned_tail"] is not None and not pd.isna(r["assigned_tail"]))


def test_turn_times_respected_in_every_tail_chain(base):
    dm = base["dm"]
    fr = base["flight_recovery"].set_index("flight")
    turn = int(dm.param.turn_min)
    for t in dm.tail_ids():
        legs = fr[(fr["assigned_tail"] == t) & (~fr["cancelled"])].sort_values("actual_dep_min")
        prev = None
        for f, r in legs.iterrows():
            if prev is not None:
                assert dm.flights.at[prev, "dest"] == r["origin"]
                gap = int(r["actual_dep_min"]) - (
                    int(fr.at[prev, "actual_dep_min"]) + int(dm.flights.at[prev, "duration_min"])
                )
                assert gap >= turn, f"{t}: {prev}->{f} turn {gap}"
            prev = f


def test_grounded_tail_respects_blackout(base):
    dm = base["dm"]
    fr = base["flight_recovery"].set_index("flight")
    for tail, (_frm, until) in dm.disrupted_tail_windows.items():
        for f, r in fr.iterrows():
            if r["assigned_tail"] == tail and not r["cancelled"] and f not in dm.pre_flight_ids:
                assert int(r["actual_dep_min"]) >= int(until)


@pytest.mark.parametrize("flight_id,hours", [("F202", 4), ("F302", 5), ("F102", 6), ("F201", 3)])
def test_arbitrary_disruptions_stay_feasible(inputs, flight_id, hours):
    d = disruption_from_flight(inputs, flight_id, hours)
    res = solve_scenario(inputs, disruption=d)
    assert res["summary"]["solve_status"] == "integer optimal solution"
    assert res["feasibility"].ok, res["feasibility"].violations


def test_weights_are_read_from_parameter_table(inputs):
    hi = {k: v.copy() for k, v in inputs.items()}
    p = hi["Parameter"]
    p.loc[p["param"] == "wSwap", "value"] = 5000  # make swapping very expensive
    res = solve_scenario(hi)
    # with swap cost 5000 >> 390 min delay, optimizer should delay in place instead
    assert res["summary"]["num_swapped"] == 0
    assert res["feasibility"].ok


# ==========================================================================
# Multiple simultaneous disruptions (Phase 1)
# ==========================================================================
def test_two_simultaneous_disruptions_feasible_and_matches_expected(inputs):
    """A2 and B1 grounded at (nearly) the same time - regression-locks the
    exact solved numbers, same way the single-disruption base case is locked
    above."""
    picks = [("F202", 4), ("F402", 4)]
    d = disruptions_from_flights(inputs, picks)
    res = solve_scenario(inputs, disruption=d)
    s = res["summary"]
    assert s["solve_status"] == "integer optimal solution"
    assert res["feasibility"].ok, res["feasibility"].violations
    assert s["total_delay_min"] == 430
    assert s["num_swapped"] == 2
    assert s["num_cancelled"] == 0
    assert s["objective_value"] == pytest.approx(730.0)
    assert s["num_variables"] < 1000 and s["num_constraints"] < 1000

    fr = res["flight_recovery"].set_index("flight")
    # A2's stranded flights still swap to A3, exactly as in the single-disruption case
    for f in ("F203", "F204"):
        assert fr.at[f, "assigned_tail"] == "A3" and fr.at[f, "delay_min"] == 20
    # B1 has no free B-tail to swap to (B2 is busy elsewhere), so it delays in place
    for f in ("F403", "F404"):
        assert fr.at[f, "assigned_tail"] == "B1" and fr.at[f, "delay_min"] == 195


def test_staggered_disruptions_freeze_correctly_per_tail(inputs):
    """Two disruptions at DIFFERENT times: freezing must be per-tail (each
    tail frozen relative to ITS OWN disruption, or to the later of the two if
    it has none), not one shared global cutover - otherwise an uninvolved
    tail's already-completed flights between the two times would wrongly
    become re-plannable."""
    picks = [("F202", 4), ("F403", 4)]  # A2 @ 09:35, B1 (via F403) @ 13:50 - later
    d = disruptions_from_flights(inputs, picks)
    res = solve_scenario(inputs, disruption=d)
    dm = res["dm"]

    assert dm.disruption_start_min == 575          # earliest disruption (A2)
    assert dm.planning_now_min == 830               # latest disruption (B1 via F403, 13:50)

    # flights for OTHER (undisrupted) tails between the two disruption times
    # must now be frozen too - they already flew before "now" (830), even
    # though they departed after A2's own disruption started (575).
    assert "F103" in dm.pre_flight_ids   # A1, dep 750 - between 575 and 830
    assert "F503" in dm.pre_flight_ids   # B2, dep 665 - between 575 and 830
    # B1's own trigger flight (F403 itself) must be frozen - it's the flight
    # whose landing causes B1's blackout, so it has to have actually flown.
    assert "F403" in dm.pre_flight_ids

    assert res["feasibility"].ok, res["feasibility"].violations
    assert res["summary"]["solve_status"] == "integer optimal solution"


def test_duplicate_tail_picks_are_detected_before_solving(inputs):
    """F203 and F204 were both originally scheduled on A2 - picking both
    should be caught by the helper the dashboard uses, before ever building
    a Disruption table."""
    dup = find_duplicate_tail_picks(inputs, [("F203", 4), ("F204", 4)])
    assert dup == ("A2", "F203", "F204")
    assert find_duplicate_tail_picks(inputs, [("F202", 4), ("F402", 4)]) is None


def test_duplicate_tail_disruption_rows_raise_not_silently_drop(inputs):
    """If a duplicate-tail Disruption table reaches the solver anyway (the
    dashboard's guard is bypassed, or a script builds one by hand), it must
    fail loudly, not silently keep only the last row for that tail."""
    d = disruptions_from_flights(inputs, [("F203", 4), ("F204", 4)])
    with pytest.raises(ValueError, match="more than one Disruption row"):
        solve_scenario(inputs, disruption=d)


# one representative flight per tail, on that tail's own original schedule
_TAIL_PROBE_FLIGHT = {"A1": "F102", "A2": "F202", "A3": "F302", "B1": "F402", "B2": "F502"}
_TAIL_PAIRS = [
    (t1, t2) for i, t1 in enumerate(_TAIL_PROBE_FLIGHT) for t2 in list(_TAIL_PROBE_FLIGHT)[i + 1:]
]


@pytest.mark.parametrize("tail1,tail2", _TAIL_PAIRS)
def test_every_pair_of_tails_disrupted_together_stays_feasible(inputs, tail1, tail2):
    """Sweep every pair of tails grounded simultaneously (10 combinations) -
    all must solve to proven optimality and pass the feasibility walk."""
    picks = [(_TAIL_PROBE_FLIGHT[tail1], 3), (_TAIL_PROBE_FLIGHT[tail2], 3)]
    res = solve_scenario(inputs, disruption=disruptions_from_flights(inputs, picks))
    assert res["summary"]["solve_status"] == "integer optimal solution"
    assert res["feasibility"].ok, (tail1, tail2, res["feasibility"].violations)
    assert res["summary"]["num_variables"] < 1000
    assert res["summary"]["num_constraints"] < 1000
