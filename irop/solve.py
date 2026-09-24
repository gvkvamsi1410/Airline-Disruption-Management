"""Thin convenience wrapper around IropDataManager + IropOptimizationEngine.

Used by the dashboard and the tests so they never touch the framework
lifecycle directly.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from irop.irop_data_manager import IropDataManager
from irop.irop_optimization_engine import IropOptimizationEngine


@contextlib.contextmanager
def _quiet_stdout():
    """Silence the CPLEX version banner and the framework's mdl.report() dump.
    stderr is left alone so real errors still surface."""
    if os.environ.get("IROP_VERBOSE"):
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def load_inputs_from_excel(path: str | Path) -> Dict[str, pd.DataFrame]:
    return pd.read_excel(path, sheet_name=None)


def _set_param(params: pd.DataFrame, name: str, value) -> pd.DataFrame:
    params = params.copy()
    if (params["param"] == name).any():
        params.loc[params["param"] == name, "value"] = value
    else:
        params = pd.concat(
            [params, pd.DataFrame([{"param": name, "value": value}])], ignore_index=True
        )
    return params


def solve_scenario(
    inputs: Dict[str, pd.DataFrame],
    disruption: Optional[pd.DataFrame] = None,
    forbid_swaps: Optional[bool] = None,
    time_limit: Optional[int] = None,
) -> Dict[str, object]:
    """Solve one recovery scenario.

    Args:
        inputs: dict of the six input tables (as read from the workbook).
        disruption: optional replacement for the ``Disruption`` table
            (columns: tail, unavailable_from, unavailable_until).
        forbid_swaps: if set, overrides the ``forbidSwaps`` parameter.
        time_limit: if set, overrides ``solveTimeLimit`` (seconds).

    Returns dict with: summary, flight_recovery (DataFrame), outputs (framework
    dict), feasibility (FeasibilityReport), dm (the populated DataManager).
    """
    inputs = {k: v.copy() for k, v in inputs.items()}
    if disruption is not None:
        inputs["Disruption"] = disruption.copy()
    if forbid_swaps is not None:
        inputs["Parameter"] = _set_param(inputs["Parameter"], "forbidSwaps", str(bool(forbid_swaps)))
    if time_limit is not None:
        inputs["Parameter"] = _set_param(inputs["Parameter"], "solveTimeLimit", int(time_limit))

    dm = IropDataManager(inputs=inputs, outputs={})
    engine = IropOptimizationEngine(dm)
    with _quiet_stdout():
        outputs = engine.run()

    return {
        "summary": engine.summary,
        "flight_recovery": dm.flight_recovery_output.reset_index(),
        "outputs": outputs,
        "feasibility": getattr(dm, "feasibility_report", None),
        "dm": dm,
    }


def flight_to_tail(inputs: Dict[str, pd.DataFrame], flight_id: str) -> str:
    """The tail originally scheduled to operate ``flight_id``."""
    flights = inputs["Flight"]
    return str(flights.loc[flights["flight"] == flight_id, "orig_tail"].iloc[0])


def _disruption_row(inputs: Dict[str, pd.DataFrame], flight_id: str, duration_hours: float) -> dict:
    """One Disruption row: ground the aircraft operating ``flight_id`` from
    the moment that flight lands, for ``duration_hours``."""
    flights = inputs["Flight"]
    row = flights.loc[flights["flight"] == flight_id].iloc[0]
    legs = inputs["LegDuration"]
    dur = legs.loc[(legs["origin"] == row["origin"]) & (legs["dest"] == row["dest"]), "duration_min"]
    dur = int(dur.iloc[0])
    h, m = str(row["sched_dep"]).split(":")
    arr_min = int(h) * 60 + int(m) + dur
    end_min = arr_min + int(round(duration_hours * 60))
    return {
        "tail": row["orig_tail"],
        "unavailable_from": f"{arr_min // 60:02d}:{arr_min % 60:02d}",
        "unavailable_until": f"{end_min // 60:02d}:{end_min % 60:02d}",
    }


def disruption_from_flight(
    inputs: Dict[str, pd.DataFrame], flight_id: str, duration_hours: float
) -> pd.DataFrame:
    """Build a single-row Disruption table for one grounded flight/duration pick."""
    return pd.DataFrame([_disruption_row(inputs, flight_id, duration_hours)])


def find_duplicate_tail_picks(
    inputs: Dict[str, pd.DataFrame], picks: Sequence[Tuple[str, float]]
) -> Optional[Tuple[str, str, str]]:
    """If two picks resolve to the same tail, return
    (tail, first_flight_id, second_flight_id); else None. Check this before
    calling :func:`disruptions_from_flights` so the caller can reject the
    selection with a clear message instead of hitting the DataManager's
    lower-level guard."""
    seen: Dict[str, str] = {}
    for flight_id, _hours in picks:
        tail = flight_to_tail(inputs, flight_id)
        if tail in seen:
            return tail, seen[tail], flight_id
        seen[tail] = flight_id
    return None


def disruptions_from_flights(
    inputs: Dict[str, pd.DataFrame], picks: Sequence[Tuple[str, float]]
) -> pd.DataFrame:
    """Build a multi-row Disruption table, one row per (flight_id, duration_hours)
    pick - i.e. multiple simultaneous groundings in one scenario. Each pick
    must resolve to a distinct tail; check with :func:`find_duplicate_tail_picks`
    first if the picks come from user input."""
    if not picks:
        raise ValueError("No disruption picks given")
    rows = [_disruption_row(inputs, flight_id, hours) for flight_id, hours in picks]
    return pd.DataFrame(rows)
