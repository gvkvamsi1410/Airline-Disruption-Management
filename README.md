# IROP Tail-Swap Recovery Demo

Irregular-operations (IROP) recovery: when an aircraft breaks mid-day, a CPLEX
MIP re-plans the rest of the day's tail-to-flight assignments — swapping tails,
delaying flights, or (last resort) cancelling — to minimise downstream
disruption, subject to fleet type, minimum turn time and airport continuity.

Built on the **DSE DO Enterprise framework** (`dse_do_utils` Core01 classes).
All schedule/disruption data is synthetic, constructed for this demo.

## Layout

```
irop/
  irop_dataframe_schema.py     Pandera schemas for the six input tables
  irop_data_manager.py         IropDataManager  - load, validate, derive
  irop_optimization_engine.py  IropOptimizationEngine - the CPLEX MIP
  irop_feasibility.py          post-solve feasibility walk
  irop_scenario_generator.py   IropScenarioGenerator - what-if variants
  irop_plotly_manager.py       IropPlotlyManager - rotation Gantt + KPIs
  solve.py                     solve_scenario() convenience wrapper
assets/data_asset/
  IropInputs.xlsx              the sample scenario (one sheet per table)
scripts/
  build_inputs_xlsx.py         regenerate IropInputs.xlsx from source data
  solve_from_excel.py          solve base scenario -> result.json + console report
  run_scenarios.py             solve the demo scenario set, print a comparison
dashboard/app.py               interactive disruption-picker dashboard (Dash)
tests/test_model.py            model-correctness tests
reference/                     the handoff doc + Vijay's reference model.py
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.12 recommended
pip install -r requirements.txt

python scripts/build_inputs_xlsx.py                    # writes assets/data_asset/IropInputs.xlsx
python scripts/solve_from_excel.py                     # solve + console report
PYTHONPATH=. pytest -q                                 # 14 correctness tests

python dashboard/app.py                                # http://127.0.0.1:8050
```

## The demo

1. The dashboard opens on the day as scheduled, with tail **A2** grounded
   09:35–13:35 (picked via flight F202).
2. **Recovered plan** view: F203 and F204 move from A2 to **A3** (idle at the hub
   until its 15:00 departure), +20 min each, nothing cancelled — total delay
   40 min, cost 340.
3. Pick a different flight / drag the duration slider / **Run recovery** to throw
   a new disruption at the model. It re-solves in ~1 s.
4. When no swap helps, the headline says so plainly and the optimizer delays in
   place — e.g. the "no-swap / manual" baseline is **390 min** of delay.

## Model summary

| | |
|---|---|
| Variables | assign `x[tail,flight]`, `cancel[flight]`, `dep[flight]`, sequence `seq[tail,f1,f2]` |
| Objective | `min  1·(delay min) + 2000·(cancellations) + 150·(swaps)` (weights from `Parameter` table) |
| Constraints | coverage · freeze flights already departed · grounded tail blackout · 45-min turn · one continuous rotation per tail from its start location |
| Validation | every solve is walked leg-by-leg for continuity + turn time before it is shown |

See `IropModelDesign.md` for the full formulation and the differences from the
reference `model.py`.
