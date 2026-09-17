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
