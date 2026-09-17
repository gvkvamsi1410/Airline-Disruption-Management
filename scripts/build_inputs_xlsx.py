"""Write the sample IROP scenario to assets/data_asset/IropInputs.xlsx.

This is the single source of truth for the demo scenario.  Schedule and
disruption are synthetic - constructed for this demo, not real airline data.
The schedule is deliberately tuned (tail A3 has a long mid-day layover at the
hub) so that a genuine tail swap is the optimal recovery.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "assets" / "data_asset" / "IropInputs.xlsx"

AIRPORTS = ["ORD", "DEN", "MSP", "STL"]

LEG_DURATIONS = [
    ("ORD", "DEN", 150), ("DEN", "ORD", 150),
    ("ORD", "MSP", 70), ("MSP", "ORD", 70),
    ("ORD", "STL", 65), ("STL", "ORD", 65),
]

TAILS = [
    ("A1", "A", "ORD"), ("A2", "A", "ORD"), ("A3", "A", "ORD"),
    ("B1", "B", "ORD"), ("B2", "B", "ORD"),
]

# flight, origin, dest, sched_dep(HH:MM), subtype_required, orig_tail
FLIGHTS = [
    ("F101", "ORD", "DEN", "06:00", "A", "A1"),
    ("F102", "DEN", "ORD", "09:15", "A", "A1"),
    ("F103", "ORD", "MSP", "12:30", "A", "A1"),
    ("F104", "MSP", "ORD", "14:25", "A", "A1"),
    ("F201", "ORD", "MSP", "06:30", "A", "A2"),
    ("F202", "MSP", "ORD", "08:25", "A", "A2"),
    ("F203", "ORD", "STL", "10:20", "A", "A2"),
    ("F204", "STL", "ORD", "12:10", "A", "A2"),
    ("F301", "ORD", "STL", "07:00", "A", "A3"),
    ("F302", "STL", "ORD", "08:50", "A", "A3"),
    ("F303", "ORD", "DEN", "15:00", "A", "A3"),   # long scheduled layover at ORD
    ("F304", "DEN", "ORD", "18:15", "A", "A3"),
    ("F401", "ORD", "DEN", "06:15", "B", "B1"),
    ("F402", "DEN", "ORD", "09:30", "B", "B1"),
    ("F403", "ORD", "STL", "12:45", "B", "B1"),
    ("F404", "STL", "ORD", "14:35", "B", "B1"),
    ("F501", "ORD", "MSP", "07:15", "B", "B2"),
    ("F502", "MSP", "ORD", "09:10", "B", "B2"),
    ("F503", "ORD", "DEN", "11:05", "B", "B2"),
    ("F504", "DEN", "ORD", "14:20", "B", "B2"),
]

DISRUPTION = [("A2", "09:35", "13:35")]

PARAMETERS = [
    ("solveTimeLimit", 60),
    ("turnMin", 45),
    ("maxDelayMin", 360),
    ("wDelay", 1),
    ("wCancel", 2000),
    ("wSwap", 150),
    ("bigM", 2000),
    ("forbidSwaps", "False"),
    # framework knobs (kept explicit so the solve log stays quiet)
    ("threads", 0),
    ("mipGap", 0),
    ("enableLPNames", "False"),
    ("handleUnscaledInfeasibilities", "False"),
    ("logSolutionQualityMetrics", "False"),
    ("enable_optimization_progress_tracking", "False"),
    ("removeZeroQuantityOutputRecords", "False"),
]


def build_tables() -> dict[str, pd.DataFrame]:
    return {
        "Airport": pd.DataFrame({"airport": AIRPORTS}),
        "LegDuration": pd.DataFrame(LEG_DURATIONS, columns=["origin", "dest", "duration_min"]),
        "Tail": pd.DataFrame(TAILS, columns=["tail", "subtype", "start_location"]),
        "Flight": pd.DataFrame(
            FLIGHTS,
            columns=["flight", "origin", "dest", "sched_dep", "subtype_required", "orig_tail"],
        ),
        "Disruption": pd.DataFrame(
            DISRUPTION, columns=["tail", "unavailable_from", "unavailable_until"]
        ),
        "Parameter": pd.DataFrame(PARAMETERS, columns=["param", "value"]),
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tables = build_tables()
    with pd.ExcelWriter(OUT, engine="openpyxl") as xw:
        for name, df in tables.items():
            df.to_excel(xw, sheet_name=name, index=False)
    print(f"wrote {OUT}  ({len(tables)} sheets)")


if __name__ == "__main__":
    main()
