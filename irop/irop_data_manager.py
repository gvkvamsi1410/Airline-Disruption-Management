"""IropDataManager - ingest, validate and derive the IROP scenario data.

Subclass of ``dse_do_utils.core.core01_data_manager.Core01DataManager``.
Holds no docplex code; everything here is pure pandas / python so it can be
re-used by the dashboard and the tests without a solver.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from dse_do_utils.core.core01_data_manager import Core01DataManager
from dse_do_utils.datamanager import Inputs, Outputs

from irop.irop_dataframe_schema import INPUT_SCHEMAS

logger = logging.getLogger(__name__)


def parse_hhmm(value) -> int:
    """'06:00' -> 360 (minutes from midnight). Ints/floats pass through."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("Missing time value")
    if isinstance(value, (int,)) or (isinstance(value, float) and not pd.isna(value)):
        return int(value)
    s = str(value).strip()
    if ":" in s:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    return int(s)


def fmt_hhmm(minutes: Optional[int]) -> str:
    if minutes is None or pd.isna(minutes):
        return "--:--"
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class IropDataManager(Core01DataManager):
    # ------------------------------------------------------------------
    def __init__(self, inputs: Optional[Inputs] = None, outputs: Optional[Outputs] = None,
                 log_level: Optional[str] = None) -> None:
        if outputs is None:
            outputs = {}
        super().__init__(inputs=inputs, outputs=outputs, log_level=log_level)

        # input tables
        self.airports: Optional[pd.DataFrame] = None
        self.leg_durations: Optional[pd.DataFrame] = None
        self.tails: Optional[pd.DataFrame] = None
        self.flights: Optional[pd.DataFrame] = None
        self.disruptions: Optional[pd.DataFrame] = None

        # lookups / derived
        self.duration: Dict[Tuple[str, str], int] = {}
        self.disruption_start_min: Optional[int] = None
        self.planning_now_min: Optional[int] = None
        self.pre_flight_ids: Set[str] = set()
        self.eligible_pairs: List[Tuple[str, str]] = []
        self.connect_pairs: List[Tuple[str, str]] = []
        self.disrupted_tail_windows: Dict[str, Tuple[int, int]] = {}

        # output tables
        self.flight_recovery_output: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def set_parameters(self) -> None:
        super().set_parameters()  # Core01: time_limit, threads, mip_gap, ...
        gp = self.get_parameter_value
        self.param.turn_min = gp(self.params, "turnMin", param_type="int", default_value=45)
        self.param.max_delay_min = gp(self.params, "maxDelayMin", param_type="int", default_value=360)
        self.param.w_delay = gp(self.params, "wDelay", param_type="float", default_value=1.0)
        self.param.w_cancel = gp(self.params, "wCancel", param_type="float", default_value=2000.0)
        self.param.w_swap = gp(self.params, "wSwap", param_type="float", default_value=150.0)
        self.param.big_m = gp(self.params, "bigM", param_type="int", default_value=2000)
        # what-if lever: fix every flight to its original tail (no swaps allowed)
        self.param.forbid_swaps = gp(self.params, "forbidSwaps", param_type="bool", default_value=False)

    # ------------------------------------------------------------------
    # Input preparation
    # ------------------------------------------------------------------
    def prepare_input_data_frames(self) -> None:
        super().prepare_input_data_frames()  # -> set_parameters()
        self.validate_inputs()

        self.airports = self.prepare_input_df("Airport", index_columns=["airport"])

        legs = self.prepare_input_df(
            "LegDuration", index_columns=None,
            value_columns=["origin", "dest", "duration_min"],
            dtypes={"duration_min": int},
        )
        self.leg_durations = legs.set_index(["origin", "dest"])
        if not self.leg_durations.index.is_unique:
            dupes = self.leg_durations.index[self.leg_durations.index.duplicated()].tolist()
            raise ValueError(f"Duplicate (origin, dest) rows in LegDuration: {dupes}")
        self.duration = {idx: int(row.duration_min) for idx, row in self.leg_durations.iterrows()}

        self.tails = self.prepare_input_df(
            "Tail", index_columns=["tail"],
            value_columns=["subtype", "start_location"],
        )

        flights = self.prepare_input_df(
            "Flight", index_columns=["flight"],
            value_columns=["origin", "dest", "sched_dep", "subtype_required", "orig_tail"],
        )
        flights["sched_dep_min"] = flights["sched_dep"].map(parse_hhmm).astype(int)
        flights["duration_min"] = [
            self._leg_duration(o, d) for o, d in zip(flights["origin"], flights["dest"])
        ]
        flights["sched_arr_min"] = flights["sched_dep_min"] + flights["duration_min"]
        self.flights = flights.sort_values("sched_dep_min")

        disr = self.prepare_input_df(
            "Disruption", index_columns=None,
            value_columns=["tail", "unavailable_from", "unavailable_until"],
        )
        disr["from_min"] = disr["unavailable_from"].map(parse_hhmm).astype(int)
        disr["until_min"] = disr["unavailable_until"].map(parse_hhmm).astype(int)
        self.disruptions = disr

    def _leg_duration(self, origin: str, dest: str) -> int:
        if (origin, dest) in self.duration:
            return self.duration[(origin, dest)]
        if (dest, origin) in self.duration:  # tolerate a single direction in the table
            return self.duration[(dest, origin)]
        raise KeyError(f"No LegDuration row for {origin}-{dest} (or its reverse)")

    def validate_inputs(self) -> None:
        assert self.inputs is not None, "No inputs provided to IropDataManager"
        missing = [t for t in INPUT_SCHEMAS if t not in self.inputs]
        if missing:
            raise KeyError(f"Scenario is missing input tables: {missing}")
        for name, schema in INPUT_SCHEMAS.items():
            try:
                schema.validate(self.inputs[name], lazy=True)
            except Exception as exc:  # pandera SchemaErrors
                raise ValueError(f"Input table '{name}' failed validation:\n{exc}") from exc

    # ------------------------------------------------------------------
    # Derived data
    # ------------------------------------------------------------------
    def pre_processing(self) -> None:
        super().pre_processing()

        # disruption window(s) - one entry per grounded tail. A tail named in
        # more than one Disruption row is rejected rather than silently
        # overwritten: two simultaneous groundings of the SAME aircraft is
        # not a representable scenario (merge them into one row/window first).
        self.disrupted_tail_windows = {}
        for r in self.disruptions.itertuples():
            tail = str(r.tail)
            window = (int(r.from_min), int(r.until_min))
            if tail in self.disrupted_tail_windows:
                raise ValueError(
                    f"Tail {tail} appears in more than one Disruption row "
                    f"({self.disrupted_tail_windows[tail]} and {window}) - "
                    "a tail can only be grounded once per scenario; merge "
                    "the two rows into a single window before solving."
                )
            self.disrupted_tail_windows[tail] = window

        self.disruption_start_min = (
            min(w[0] for w in self.disrupted_tail_windows.values())
            if self.disrupted_tail_windows else None
        )
        # "now": the latest disruption start in this batch - the earliest
        # moment we could be jointly re-planning around ALL of them, since
        # a disruption discovered later can't have been acted on any sooner.
        self.planning_now_min = (
            max(w[0] for w in self.disrupted_tail_windows.values())
            if self.disrupted_tail_windows else None
        )

        # frozen ("already operated") flights: everything that departed
        # before recovery planning could have touched it. A tail with its
        # own disruption freezes relative to THAT disruption's start (not
        # some other tail's, earlier or later); every other tail freezes
        # relative to "now". This is the key correction over the reference
        # model.py, which froze only two flights by hand off one hardcoded
        # time - and, with more than one disruption, a single shared cutover
        # for every tail would let the optimizer "retroactively" rewrite an
        # uninvolved tail's flights that had already operated normally
        # before its own (later, unrelated) disruption was even known.
        self.pre_flight_ids = set()
        if self.disrupted_tail_windows:
            for f, row in self.flights.iterrows():
                tail = row["orig_tail"]
                cutover = self.disrupted_tail_windows.get(tail, (self.planning_now_min,))[0]
                if row["sched_dep_min"] < cutover:
                    self.pre_flight_ids.add(str(f))

        # subtype-eligible (tail, flight) pairs
        self.eligible_pairs = [
            (t, f)
            for t in self.tails.index
            for f in self.flights.index
            if self.tails.at[t, "subtype"] == self.flights.at[f, "subtype_required"]
        ]

        # geographically-connecting, time-feasible ordered flight pairs
        turn = int(self.param.turn_min)
        max_delay = int(self.param.max_delay_min)
        pairs: List[Tuple[str, str]] = []
        for f1 in self.flights.index:
            arr1 = int(self.flights.at[f1, "sched_arr_min"])          # no delay on f1
            dest1 = self.flights.at[f1, "dest"]
            for f2 in self.flights.index:
                if f1 == f2 or self.flights.at[f2, "origin"] != dest1:
                    continue
                latest_dep2 = int(self.flights.at[f2, "sched_dep_min"]) + max_delay
                if latest_dep2 >= arr1 + turn:          # any chance of connecting?
                    pairs.append((f1, f2))
        self.connect_pairs = pairs

    # ------------------------------------------------------------------
    # Output preparation
    # ------------------------------------------------------------------
    def prepare_output_data_frames(self) -> None:
        super().prepare_output_data_frames()  # kpis, business_kpis, progress
        self.flight_recovery_output = self.prepare_output_df(
            "FlightRecoveryOutput",
            index_columns=["flight"],
            value_columns=[
                "origin", "dest", "sched_dep_min", "actual_dep_min", "delay_min",
                "orig_tail", "assigned_tail", "swapped", "cancelled",
            ],
        )

    def post_processing(self) -> None:
        """Runs after the engine has extracted the solution.  Validates the
        recovered plan is physically flyable before anyone trusts it."""
        from irop.irop_feasibility import check_recovery_feasible

        if self.flight_recovery_output is not None and len(self.flight_recovery_output) > 0:
            self.feasibility_report = check_recovery_feasible(self)

    def get_outputs(self) -> Outputs:
        outputs = super().get_outputs()  # kpis, BusinessKpi, OptimizationProgress
        if self.flight_recovery_output is not None:
            outputs["FlightRecoveryOutput"] = self.flight_recovery_output.reset_index()
        return outputs

    # ------------------------------------------------------------------
    # Convenience accessors used by the dashboard / plotly manager
    # ------------------------------------------------------------------
    def tail_ids(self) -> List[str]:
        return list(self.tails.index)

    def flight_ids(self) -> List[str]:
        return list(self.flights.index)

    def leg_minutes(self, flight_id: str) -> int:
        return int(self.flights.at[flight_id, "duration_min"])
