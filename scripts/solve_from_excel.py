"""Solve the base recovery scenario from the Excel workbook and write result.json.

    python scripts/solve_from_excel.py [--no-swap] [--excel PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default=str(ROOT / "assets" / "data_asset" / "IropInputs.xlsx"))
    ap.add_argument("--out", default=str(ROOT / "assets" / "data_asset" / "result.json"))
    ap.add_argument("--no-swap", action="store_true", help="forbid swaps (manual baseline)")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, str(ROOT))
    from irop.solve import load_inputs_from_excel, solve_scenario

    inputs = load_inputs_from_excel(args.excel)
    res = solve_scenario(inputs, forbid_swaps=True if args.no_swap else None)

    fr = res["flight_recovery"]
    payload = {
        "summary": res["summary"],
        "flights": json.loads(fr.to_json(orient="records")),
        "feasibility": {
            "ok": res["feasibility"].ok,
            "checks_run": res["feasibility"].checks_run,
            "violations": res["feasibility"].violations,
        },
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))

    s = res["summary"]
    print(f"status         : {s['solve_status']}")
    print(f"model size     : {s['num_variables']} vars / {s['num_constraints']} constraints")
    print(f"total delay    : {s['total_delay_min']} min over {s['num_delayed']} flight(s)")
    print(f"swapped        : {s['num_swapped']}   cancelled: {s['num_cancelled']}")
    print(f"recovery cost  : {s['objective_value']:.0f}")
    print(f"feasibility    : {'OK' if res['feasibility'].ok else 'FAILED'} "
          f"({res['feasibility'].checks_run} checks)")
    print(f"\nchanges:")
    changed = fr[fr["swapped"] | (fr["delay_min"] > 0) | fr["cancelled"]]
    print(changed.to_string(index=False) if not changed.empty else "  (none)")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
