"""IROP tail-swap recovery demo — built on the DSE DO Enterprise framework
(dse_do_utils Core01 classes).

Package layout:
    irop_dataframe_schema.py   - Pandera schemas for the input/output tables
    irop_data_manager.py       - IropDataManager: load, validate, derive
    irop_optimization_engine.py- IropOptimizationEngine: the CPLEX MIP
    irop_feasibility.py        - post-solve feasibility checker
    irop_scenario_generator.py - what-if variants (no-swap baseline, ...)
    irop_plotly_manager.py     - rotation Gantt + KPI figures
    solve.py                   - solve_scenario() convenience wrapper
"""

import warnings as _warnings

# docplex.cp is imported transitively by dse_do_utils but never used here (we
# build an mp.Model, not a CpoModel).  Silence its py-version RuntimeWarning.
_warnings.filterwarnings("ignore", message="docplex.cp is supported by Python")

__version__ = "0.1.0"
