"""Post-solve feasibility walk.

The handoff document is explicit that early versions of this model returned
"optimal" solutions that were not physically flyable (a tail on two overlapping
flights, a rotation split into disconnected chains).  This module reconstructs
every tail's day from the solution and asserts it is real, so no unflyable plan
ever reaches the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd


class FeasibilityError(AssertionError):
    pass


@dataclass
class FeasibilityReport:
    ok: bool = True
    checks_run: int = 0
    violations: List[str] = field(default_factory=list)
    tail_rotations: Dict[str, List[str]] = field(default_factory=dict)

    def require(self, condition: bool, message: str) -> None:
        self.checks_run += 1
        if not condition:
            self.ok = False
            self.violations.append(message)


def check_recovery_feasible(dm, raise_on_fail: bool = True) -> FeasibilityReport:
    """Validate ``dm.flight_recovery_output`` against ``dm`` inputs."""
    rep = FeasibilityReport()
    out: pd.DataFrame = dm.flight_recovery_output.copy()
    flights = dm.flights
    tails = dm.tails
    turn = int(dm.param.turn_min)
    max_delay = int(dm.param.max_delay_min)

    # ---- per-flight sanity ------------------------------------------------
    for f, r in out.iterrows():
        if r["cancelled"]:
            rep.require(pd.isna(r["assigned_tail"]) or r["assigned_tail"] is None,
                        f"{f}: cancelled but still has assigned tail {r['assigned_tail']}")
            continue
        rep.require(r["assigned_tail"] is not None and not pd.isna(r["assigned_tail"]),
                    f"{f}: not cancelled and not assigned to any tail")
        at = r["assigned_tail"]
        rep.require(tails.at[at, "subtype"] == flights.at[f, "subtype_required"],
                    f"{f}: tail {at} subtype {tails.at[at, 'subtype']} != required "
                    f"{flights.at[f, 'subtype_required']}")
        dep = int(r["actual_dep_min"])
        sched = int(flights.at[f, "sched_dep_min"])
        rep.require(dep >= sched, f"{f}: departs {dep} before schedule {sched}")
        rep.require(dep - sched <= max_delay,
                    f"{f}: delay {dep - sched} exceeds max {max_delay}")

    # ---- frozen flights untouched --------------------------------------
    for f in dm.pre_flight_ids:
        r = out.loc[f]
        rep.require(not r["cancelled"], f"{f}: pre-disruption flight was cancelled")
        rep.require(r["assigned_tail"] == flights.at[f, "orig_tail"],
                    f"{f}: pre-disruption flight moved off {flights.at[f, 'orig_tail']}")
        rep.require(int(r["actual_dep_min"]) == int(flights.at[f, "sched_dep_min"]),
                    f"{f}: pre-disruption flight was delayed")

    # ---- blackout respected ------------------------------------------
    for tail, (from_min, until_min) in dm.disrupted_tail_windows.items():
        for f, r in out.iterrows():
            if r["cancelled"] or r["assigned_tail"] != tail or f in dm.pre_flight_ids:
                continue
            rep.require(int(r["actual_dep_min"]) >= int(until_min),
                        f"{f}: grounded tail {tail} departs {int(r['actual_dep_min'])} "
                        f"before window end {int(until_min)}")

    # ---- reconstruct each tail's chain -------------------------------
    for t in dm.tail_ids():
        legs = out[(out["assigned_tail"] == t) & (~out["cancelled"])].copy()
        legs = legs.sort_values("actual_dep_min")
        rep.tail_rotations[t] = list(legs.index)
        if legs.empty:
            continue

        home = tails.at[t, "start_location"]
        first = legs.iloc[0]
        rep.require(first["origin"] == home,
                    f"{t}: first leg {legs.index[0]} departs {first['origin']}, "
                    f"not start location {home}")

        prev_id = None
        for f, r in legs.iterrows():
            if prev_id is not None:
                p = out.loc[prev_id]
                rep.require(flights.at[prev_id, "dest"] == r["origin"],
                            f"{t}: {prev_id}->{f} broken continuity "
                            f"({flights.at[prev_id, 'dest']} != {r['origin']})")
                prev_arr = int(p["actual_dep_min"]) + int(flights.at[prev_id, "duration_min"])
                gap = int(r["actual_dep_min"]) - prev_arr
                rep.require(gap >= turn,
                            f"{t}: {prev_id}->{f} turn {gap} min < required {turn}")
            prev_id = f

    # ---- coverage ---------------------------------------------------
    n_flown = int((~out["cancelled"]).sum())
    n_cancelled = int(out["cancelled"].sum())
    rep.require(n_flown + n_cancelled == len(out),
                f"coverage: {n_flown} flown + {n_cancelled} cancelled != {len(out)} flights")

    if not rep.ok and raise_on_fail:
        raise FeasibilityError(
            "Recovered plan failed feasibility checks:\n  - "
            + "\n  - ".join(rep.violations)
        )
    return rep
