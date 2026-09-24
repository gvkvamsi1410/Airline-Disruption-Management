# IROP Tail-Swap Recovery Demo

Irregular-operations (IROP) recovery: when an aircraft breaks mid-day, a CPLEX
MIP re-plans the rest of the day's tail-to-flight assignments — swapping tails,
delaying flights, or (last resort) cancelling — to minimise downstream
disruption, subject to fleet type, minimum turn time and airport continuity.

Built on the **DSE DO Enterprise framework** (`dse_do_utils` Core01 classes).
All schedule/disruption data is synthetic, constructed for this demo.

---

## 1. Requirements

- **Python 3.12** recommended. 3.13 also works (verified) — you'll see a
  one-line `RuntimeWarning` about `docplex.cp` not supporting 3.13 at import
  time; it's harmless (this project only uses `docplex.mp`, the MIP side, not
  `docplex.cp`) and is suppressed automatically. Python 3.9–3.11 should also
  work but haven't been tested against this repo.
- **No CPLEX license or IBM account needed.** `pip install cplex` on its own
  installs the free Community Edition, which is all this project uses. The
  model is ~226 variables / ~590 constraints — the CE cap is 1000/1000, so
  there's headroom.
- **No database, no Docker, nothing else to install.** The dashboard is a
  self-contained Python/Dash app that reads a local Excel file. It does not
  use the scenario database or multi-page dashboard shell that the wider
  DSE DO framework normally ships with.
- Works on macOS, Linux and Windows. (Built and tested on macOS.)

## 2. Setup (do this once)

Open a terminal **in this folder** and run:

```bash
python3 -m venv .venv
```

Activate it:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (cmd.exe)
.venv\Scripts\activate.bat
```

Your prompt should now show `(.venv)` at the start of the line. Then install everything:

```bash
pip install -r requirements.txt
```

This takes a minute or two — `cplex` is the largest package. If it fails, see
[Troubleshooting](#7-troubleshooting) below before doing anything else.

> If you'd rather not use a virtual environment, `pip install -r requirements.txt`
> works the same against your system Python — just skip the `venv`/`activate`
> steps above. Nothing in this project needs an isolated environment to run
> correctly; a venv just keeps it from touching anything else on your machine.

## 3. Verify the install worked

Run these two commands, in order, from the project folder (they work from any
current directory and don't need any environment variables set):

```bash
python scripts/build_inputs_xlsx.py
```
Should print `wrote .../assets/data_asset/IropInputs.xlsx  (6 sheets)`.

```bash
python -m pytest -q
```
Should print `28 passed` with no errors. **This is the single command that
proves the install is correct and the model is intact** — if this passes,
everything downstream (the dashboard, the scripts) will work.

## 4. Run the dashboard

```bash
python dashboard/app.py
```

Leave that running, then open **http://127.0.0.1:8050** in a browser.
To stop it, go back to the terminal and press `Ctrl+C`.

If port 8050 is already taken by something else, edit the last line of
`dashboard/app.py` (`app.run(debug=False, port=8050)`) to a free port, e.g.
`port=8060`, and open that port instead.

## 5. Other useful commands

```bash
# console report for the default scenario (also writes assets/data_asset/result.json)
python scripts/solve_from_excel.py

# comparison table: optimized vs. the no-swap "manual" baseline vs. other picks
python scripts/run_scenarios.py

# regenerate IropInputs.xlsx from source (only needed if you edit the scenario data
# in scripts/build_inputs_xlsx.py itself — editing the .xlsx directly also works,
# the model always reads whatever is in assets/data_asset/IropInputs.xlsx)
python scripts/build_inputs_xlsx.py

```

## 6. The demo — what to click

1. Dashboard opens on the day as scheduled, with tail **A2** grounded
   09:35–13:35 (picked via flight F202, 4h).
2. **Recovered plan** view: F203 and F204 move from A2 to **A3** (idle at the
   hub until its 15:00 departure), +20 min each, nothing cancelled — total
   delay 40 min, cost 340. Toggle **As scheduled** to see the original plan
   for comparison — note the visible gap on A3's row that makes the swap possible.
3. Check a different flight's box in the table, edit its hours in the
   "Selected disruptions" table below it, click **Run recovery** — it
   re-solves in about a second. Selecting a flight means "the aircraft that
   flew this flight breaks the moment it lands" — not that the flight itself
   is disrupted.
4. **Multiple simultaneous disruptions:** check more than one flight's box to
   ground more than one aircraft at once — each gets its own row (and its own
   hours) in the "Selected disruptions" table, and the model solves all of
   them together in one pass. Two flights that were originally on the *same*
   tail can't both be picked — the picker rejects the second one with a
   message rather than building a broken scenario.
5. When no swap helps, the headline says so plainly and the optimizer delays
   in place instead — e.g. ground A1 via flight F102 for 4h and it delays two
   flights by 195 min each rather than force a swap that isn't actually cheaper.

See `IropModelDesign.md` for the full mathematical formulation (§8 covers how
multiple simultaneous disruptions are handled), and the demo script / talking
points that were prepared alongside this build for exactly this walkthrough.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `pip install` fails on `cplex` | Confirm you're on Python 3.9–3.13 (`python3 --version`) — cplex ships no wheel for much older/newer versions. On Apple Silicon, make sure you're not in an x86 Rosetta terminal. |
| `ModuleNotFoundError: No module named 'irop'` when running a script directly | Shouldn't happen — every entry point (`dashboard/app.py`, everything in `scripts/`, `tests/test_model.py`) adds the project root to `sys.path` itself. If you still see this, confirm you didn't rename or move the `irop/` folder relative to the file you're running. |
| `RuntimeWarning: docplex.cp is supported by Python versions ... not '3.13...'` | Harmless — this project never uses `docplex.cp`. Already suppressed for anything importing the `irop` package; you'd only see it if you `import docplex` directly yourself. |
| A line like `-- cannot find parameters matching version: 22.2.0.0, using: 22.1.1.0` when solving | Harmless CPLEX version-compatibility notice from the C library itself, not an error. It's suppressed in the dashboard/scripts (they redirect solver stdout); you'd only see it running raw docplex code yourself. |
| Dashboard page loads but looks broken / blank chart | Check the terminal running `python dashboard/app.py` for a traceback — it prints Python errors there, not in the browser. Also hard-refresh the browser tab (cached JS from a previous run). |
| `Address already in use` / port 8050 busy | Something else is already listening on 8050. Either stop it, or change the port as described in [§4](#4-run-the-dashboard). |
| `pytest` reports failures | Don't proceed to the demo — this means the model itself is broken, not an environment issue. Re-run `pip install -r requirements.txt` to make sure nothing is missing/mismatched, and if it still fails, get in touch before the demo rather than presenting on an unverified build. |

## 8. Known cosmetic item

A small rendering artifact occasionally shows near the hours-slider handle —
harmless, doesn't affect any functionality, on the polish list.

---

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
tests/test_model.py            model-correctness tests (28 tests)
reference/                     the original handoff doc + the reference model.py
requirements.txt               pinned, verified dependency list (see file for exact versions)
IropModelDesign.md             the full model write-up (sets, variables, objective, constraints)
```

## Model summary

| | |
|---|---|
| Variables | assign `x[tail,flight]`, `cancel[flight]`, `dep[flight]`, sequence `seq[tail,f1,f2]` |
| Objective | `min  1·(delay min) + 2000·(cancellations) + 150·(swaps)` (weights from `Parameter` table) |
| Constraints | coverage · freeze flights already departed · grounded tail blackout · 45-min turn · one continuous rotation per tail from its start location |
| Validation | every solve is walked leg-by-leg for continuity + turn time before it is shown |

See `IropModelDesign.md` for the full formulation and the differences from the
reference `model.py` in `reference/`.
