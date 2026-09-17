"""IropOptimizationEngine - the CPLEX (docplex) MIP for tail-swap recovery.

Subclass of ``dse_do_utils.core.core01_optimization_engine.Core01OptimizationEngine``.
Formulation follows the handoff document; the reference ``model.py`` was used as
a cross-check.  Differences from that reference, all deliberate:

  * every flight that departed before the disruption is frozen (not just two);
  * cost weights and the disruption come from the scenario tables, not code;
  * the "home base" in the rotation constraints is each tail's own
    ``start_location`` rather than a hard-coded "ORD";
  * a cancelled flight's departure is pinned to schedule so it cannot add
    phantom delay to the objective;
  * the solution is validated by an automated feasibility walk (post_processing).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

import pandas as pd

from dse_do_utils.core.core01_optimization_engine import Core01OptimizationEngine
from dse_do_utils.datamanager import Outputs

from irop.irop_data_manager import IropDataManager

logger = logging.getLogger(__name__)


class IropOptimizationEngine(Core01OptimizationEngine[IropDataManager]):
    def __init__(self, data_manager: IropDataManager, name: str = "irop_tail_swap", **kwargs) -> None:
        kwargs.setdefault("solve_kwargs", {"log_output": False})
        super().__init__(data_manager=data_manager, name=name, **kwargs)
        self.x: Dict[Tuple[str, str], object] = {}
        self.cancel: Dict[str, object] = {}
        self.dep: Dict[str, object] = {}
        self.seq: Dict[Tuple[str, str, str], object] = {}
        self.summary: Dict[str, object] = {}

    # ==================================================================
    # Decision variables
    # ==================================================================
    def create_dvars(self) -> None:
        dm = self.dm
        f_ids = dm.flight_ids()

        self.x = self.mdl.binary_var_dict(dm.eligible_pairs, name="x")
        self.cancel = self.mdl.binary_var_dict(f_ids, name="cancel")
        self.dep = self.mdl.integer_var_dict(
            f_ids, name="dep",
            lb={f: int(dm.flights.at[f, "sched_dep_min"]) for f in f_ids},
            ub={f: int(dm.flights.at[f, "sched_dep_min"]) + int(dm.param.max_delay_min) for f in f_ids},
        )

        eligible = set(dm.eligible_pairs)
        self.seq = self.mdl.binary_var_dict(
            [(t, f1, f2)
             for t in dm.tail_ids()
             for (f1, f2) in dm.connect_pairs
             if (t, f1) in eligible and (t, f2) in eligible],
            name="seq",
        )

    # ==================================================================
    # Objective
    # ==================================================================
    def create_objectives(self) -> None:
        dm = self.dm
        f_ids = dm.flight_ids()
        pre = dm.pre_flight_ids

        self.delay_expr = self.mdl.sum(
            self.dep[f] - int(dm.flights.at[f, "sched_dep_min"]) for f in f_ids
        )
        self.cancel_expr = self.mdl.sum(self.cancel[f] for f in f_ids)
        self.swap_expr = self.mdl.sum(
            self.x[t, f]
            for (t, f) in dm.eligible_pairs
            if t != dm.flights.at[f, "orig_tail"] and f not in pre
        )

        self.mdl.add_kpi(self.delay_expr, "TotalDelayMin")
        self.mdl.add_kpi(self.cancel_expr, "FlightsCancelled")
        self.mdl.add_kpi(self.swap_expr, "FlightsSwapped")

        w = dm.param
        self.cost_expr = (
            float(w.w_delay) * self.delay_expr
            + float(w.w_cancel) * self.cancel_expr
            + float(w.w_swap) * self.swap_expr
        )
        self.mdl.add_kpi(self.cost_expr, "RecoveryCost")

    def set_objective(self) -> None:
        self.mdl.minimize(self.cost_expr)

    # ==================================================================
    # Constraints
    # ==================================================================
    def create_constraints(self) -> None:
        self.create_coverage_c()
        self.create_freeze_pre_disruption_c()
        self.create_blackout_c()
        self.create_cancel_departure_c()
        self.create_turn_time_c()
        self.create_rotation_c()
        if bool(self.dm.param.forbid_swaps):
            self.create_forbid_swaps_c()

    def create_coverage_c(self) -> None:
        """Every flight is operated by exactly one eligible tail, or cancelled."""
        dm = self.dm
        eligible = set(dm.eligible_pairs)
        for f in dm.flight_ids():
            self.mdl.add_constraint(
                self.mdl.sum(self.x[t, f] for t in dm.tail_ids() if (t, f) in eligible)
                + self.cancel[f] == 1,
                ctname=f"cover_{f}",
            )

    def create_freeze_pre_disruption_c(self) -> None:
        """Flights that already departed keep their tail, time and are not cancellable."""
        dm = self.dm
        for f in dm.pre_flight_ids:
            ot = dm.flights.at[f, "orig_tail"]
            self.mdl.add_constraint(self.x[ot, f] == 1, ctname=f"fix_tail_{f}")
            self.mdl.add_constraint(self.cancel[f] == 0, ctname=f"fix_nocancel_{f}")
            self.mdl.add_constraint(
                self.dep[f] == int(dm.flights.at[f, "sched_dep_min"]), ctname=f"fix_ontime_{f}"
            )

    def create_blackout_c(self) -> None:
        """A grounded tail cannot depart a (non-frozen) flight before its window ends."""
        dm = self.dm
        eligible = set(dm.eligible_pairs)
        for tail, (_from_min, until_min) in dm.disrupted_tail_windows.items():
            for f in dm.flight_ids():
                if f in dm.pre_flight_ids or (tail, f) not in eligible:
                    continue
                self.mdl.add_constraint(
                    self.dep[f] >= int(until_min) * self.x[tail, f],
                    ctname=f"blackout_{tail}_{f}",
                )

    def create_cancel_departure_c(self) -> None:
        """If a flight is cancelled, hold its departure at schedule (no phantom delay)."""
        dm = self.dm
        max_delay = int(dm.param.max_delay_min)
        for f in dm.flight_ids():
            sched = int(dm.flights.at[f, "sched_dep_min"])
            self.mdl.add_constraint(
                self.dep[f] - sched <= max_delay * (1 - self.cancel[f]),
                ctname=f"cancel_ontime_{f}",
            )

    def create_turn_time_c(self) -> None:
        """Consecutive flights on one tail need >= TURN minutes on the ground."""
        dm = self.dm
        turn = int(dm.param.turn_min)
        big_m = int(dm.param.big_m)
        for (t, f1, f2), var in self.seq.items():
            dur1 = int(dm.flights.at[f1, "duration_min"])
            self.mdl.add_constraint(
                self.dep[f2] >= self.dep[f1] + dur1 + turn - big_m * (1 - var),
                ctname=f"turn_{t}_{f1}_{f2}",
            )
            self.mdl.add_constraint(var <= self.x[t, f1], ctname=f"seqlink1_{t}_{f1}_{f2}")
            self.mdl.add_constraint(var <= self.x[t, f2], ctname=f"seqlink2_{t}_{f1}_{f2}")

    def create_rotation_c(self) -> None:
        """Force each tail's assigned flights into one continuous, geographically
        valid chain that starts at the tail's own start_location."""
        dm = self.dm
        eligible = set(dm.eligible_pairs)
        for t in dm.tail_ids():
            home = dm.tails.at[t, "start_location"]
            flights_for_t = [f for f in dm.flight_ids() if (t, f) in eligible]
            start_indicators = []
            for f2 in flights_for_t:
                preds = [self.seq[(t, f1, f2)] for f1 in flights_for_t
                         if (t, f1, f2) in self.seq]
                at_home = dm.flights.at[f2, "origin"] == home
                if at_home:
                    if preds:
                        self.mdl.add_constraint(
                            self.mdl.sum(preds) <= self.x[t, f2], ctname=f"indeg_home_{t}_{f2}"
                        )
                        start_indicators.append(self.x[t, f2] - self.mdl.sum(preds))
                    else:
                        start_indicators.append(self.x[t, f2])
                else:
                    if preds:
                        self.mdl.add_constraint(
                            self.mdl.sum(preds) == self.x[t, f2], ctname=f"indeg_spoke_{t}_{f2}"
                        )
                    else:
                        self.mdl.add_constraint(
                            self.x[t, f2] == 0, ctname=f"noreach_{t}_{f2}"
                        )
            if start_indicators:
                self.mdl.add_constraint(
                    self.mdl.sum(start_indicators) <= 1, ctname=f"single_start_{t}"
                )
            for f1 in flights_for_t:
                succ = [self.seq[(t, f1, f2)] for f2 in flights_for_t
                        if (t, f1, f2) in self.seq]
                if succ:
                    self.mdl.add_constraint(
                        self.mdl.sum(succ) <= self.x[t, f1], ctname=f"outdeg_{t}_{f1}"
                    )

    def create_forbid_swaps_c(self) -> None:
        """What-if lever: force every still-to-operate flight onto its original
        tail.  Recovery is then limited to delay / cancel - the 'manual' baseline."""
        dm = self.dm
        eligible = set(dm.eligible_pairs)
        for f in dm.flight_ids():
            if f in dm.pre_flight_ids:
                continue
            ot = dm.flights.at[f, "orig_tail"]
            if (ot, f) in eligible:
                self.mdl.add_constraint(self.x[ot, f] == 1, ctname=f"noswap_{f}")

    # ==================================================================
    # Solution extraction
    # ==================================================================
    def extract_solution(self, drop: bool = True) -> None:
        super().extract_solution(drop=drop)  # -> dm.kpis from add_kpi
        dm = self.dm

        rows = []
        for f in dm.flight_ids():
            fr = dm.flights.loc[f]
            cancelled = self.cancel[f].solution_value > 0.5
            assigned = None
            for t in dm.tail_ids():
                if (t, f) in self.x and self.x[t, f].solution_value > 0.5:
                    assigned = t
                    break
            actual = int(round(self.dep[f].solution_value))
            sched = int(fr["sched_dep_min"])
            rows.append({
                "flight": f,
                "origin": fr["origin"],
                "dest": fr["dest"],
                "sched_dep_min": sched,
                "actual_dep_min": None if cancelled else actual,
                "delay_min": 0 if cancelled else max(0, actual - sched),
                "orig_tail": fr["orig_tail"],
                "assigned_tail": assigned,
                "swapped": bool(assigned is not None and assigned != fr["orig_tail"]),
                "cancelled": bool(cancelled),
            })

        out = pd.DataFrame(rows).sort_values("sched_dep_min").set_index("flight")
        out["actual_dep_min"] = out["actual_dep_min"].astype("Int64")
        out["delay_min"] = out["delay_min"].astype(int)
        dm.flight_recovery_output = out

        self.summary = {
            "total_delay_min": int(out["delay_min"].sum()),
            "num_delayed": int((out["delay_min"] > 0).sum()),
            "num_cancelled": int(out["cancelled"].sum()),
            "num_swapped": int(out["swapped"].sum()),
            "objective_value": float(self.mdl.objective_value),
            "solve_status": str(self.mdl.solve_details.status),
            "num_variables": int(self.mdl.number_of_variables),
            "num_constraints": int(self.mdl.number_of_constraints),
        }

    def get_outputs(self) -> Outputs:
        return self.dm.get_outputs()
