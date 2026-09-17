"""Run the demo scenario set and print a comparison table.

    python scripts/run_scenarios.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from irop.solve import disruption_from_flight, load_inputs_from_excel, solve_scenario

EXCEL = ROOT / "assets" / "data_asset" / "IropInputs.xlsx"


def main() -> None:
    inputs = load_inputs_from_excel(EXCEL)

    scenarios = {
        "base (A2 grounded 4h)":        dict(),
        "no-swap / manual baseline":    dict(forbid_swaps=True),
        "A2 grounded 6h":               dict(disruption=disruption_from_flight(inputs, "F202", 6)),
        "A1 grounded 6h (via F102)":    dict(disruption=disruption_from_flight(inputs, "F102", 6)),
        "A3 grounded 5h (via F302)":    dict(disruption=disruption_from_flight(inputs, "F302", 5)),
    }

    hdr = f"{'scenario':<30} {'delay':>7} {'swap':>5} {'canc':>5} {'cost':>8}  feas"
    print(hdr)
    print("-" * len(hdr))
    for name, kw in scenarios.items():
        r = solve_scenario(inputs, **kw)
        s = r["summary"]
        print(f"{name:<30} {s['total_delay_min']:>6}m {s['num_swapped']:>5} "
              f"{s['num_cancelled']:>5} {s['objective_value']:>8.0f}  "
              f"{'OK' if r['feasibility'].ok else 'FAIL'}")


if __name__ == "__main__":
    main()
