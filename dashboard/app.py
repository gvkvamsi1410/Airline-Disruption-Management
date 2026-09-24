"""IROP recovery dashboard - interactive disruption picker.

Flow: pick one or more flights in the table -> the aircraft operating each
one is grounded from the moment it lands, for its own chosen number of hours
(edit the "HOURS" cell per pick) -> "Run recovery" re-solves the CPLEX model
for all of them at once -> the recovered plan, KPIs and action list update.

Two flights on the SAME aircraft can't both be picked (a tail can only be
grounded once per scenario) - the picker rejects the second one with a
message rather than silently producing a broken scenario.

Run:  python dashboard/app.py   then open http://127.0.0.1:8050
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Dict, List, Tuple

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, dcc, html

from irop.irop_data_manager import fmt_hhmm
from irop.irop_plotly_manager import IropPlotlyManager
from irop.solve import (
    disruptions_from_flights,
    find_duplicate_tail_picks,
    load_inputs_from_excel,
    solve_scenario,
)

EXCEL = ROOT / "assets" / "data_asset" / "IropInputs.xlsx"
BASE_INPUTS = load_inputs_from_excel(EXCEL)
DEFAULT_HOURS = 4.0

# flight table (chronological, with a readable arrival time)
_ft = BASE_INPUTS["Flight"].copy()
_legs = {(r.origin, r.dest): r.duration_min for r in BASE_INPUTS["LegDuration"].itertuples()}
_ft["dep_min"] = _ft["sched_dep"].map(lambda s: int(s[:2]) * 60 + int(s[3:]))
_ft["arr"] = [
    fmt_hhmm(dm + _legs[(o, d)]) for dm, o, d in zip(_ft["dep_min"], _ft["origin"], _ft["dest"])
]
_ft = _ft.sort_values("dep_min")
FLIGHT_RECORDS = _ft.rename(columns={"sched_dep": "dep"})[
    ["flight", "origin", "dest", "dep", "arr", "subtype_required", "orig_tail"]
].to_dict("records")
FLIGHT_INDEX_BY_ID = {r["flight"]: i for i, r in enumerate(FLIGHT_RECORDS)}
DEFAULT_PICK_FLIGHT = "F202"

# cache of the most recent solve (single-user demo)
_LAST: dict = {}


def _run(picks: List[Tuple[str, float]]) -> None:
    disruption = disruptions_from_flights(BASE_INPUTS, picks)
    res = solve_scenario(BASE_INPUTS, disruption=disruption)
    _LAST.update(
        dm=res["dm"], recovery=res["flight_recovery"], summary=res["summary"],
        disruption=disruption, feasibility=res["feasibility"],
        picks=picks,
    )


_run([(DEFAULT_PICK_FLIGHT, DEFAULT_HOURS)])  # default scenario on load

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.FLATLY], title="IROP Recovery")

# Light theme palette (page: pale blue, cards/table: white, text: dark navy).
PAGE_BG = "#EAF2FA"
PANEL_BG = "#FFFFFF"
BORDER = "#D7E3EE"
TEXT = "#152A3A"
TEXT_DIM = "#5B7086"
ACCENT_AMBER = "#B45309"
ACCENT_RED = "#B42318"
SELECT_BG = "#E3F6F3"
SELECT_BORDER = "#14B8A6"

_TABLE_STYLE = dict(
    style_as_list_view=True,
    style_header={"backgroundColor": "#F1F6FB", "color": TEXT_DIM, "fontWeight": "600",
                  "border": "none", "fontSize": "12px"},
    style_cell={"backgroundColor": PANEL_BG, "color": TEXT, "border": "none",
                "fontSize": "13px", "fontFamily": "monospace", "padding": "6px 10px"},
    style_data_conditional=[{"if": {"state": "selected"},
                             "backgroundColor": SELECT_BG, "border": f"1px solid {SELECT_BORDER}"}],
)


def kpi_card(label: str, value: str) -> dbc.Col:
    return dbc.Col(dbc.Card(dbc.CardBody([
        html.Div(label, style={"fontSize": "12px", "color": TEXT_DIM}),
        html.Div(value, style={"fontSize": "24px", "fontWeight": "700", "color": TEXT}),
    ]), style={"backgroundColor": PANEL_BG, "border": f"1px solid {BORDER}"}), md=3, xs=6)


def _picks_table_row(flight_id: str, hours: float) -> dict:
    r = FLIGHT_RECORDS[FLIGHT_INDEX_BY_ID[flight_id]]
    return {"flight": flight_id, "route": f"{r['origin']}→{r['dest']}",
            "tail": r["orig_tail"], "hours": hours}


DEFAULT_PICKS_DATA = [_picks_table_row(DEFAULT_PICK_FLIGHT, DEFAULT_HOURS)]

app.layout = dbc.Container(fluid=True, style={"backgroundColor": PAGE_BG, "minHeight": "100vh",
                                              "padding": "22px 26px"}, children=[
    html.H4("IROP tail-swap recovery", style={"color": TEXT, "fontWeight": "600"}),
    html.Div(id="disruption-line", style={"color": ACCENT_AMBER, "fontSize": "13px",
                                          "fontWeight": "600", "marginBottom": "16px",
                                          "whiteSpace": "pre-line"}),

    dbc.Row([
        dbc.Col(md=4, children=[
            html.Div("1 · Pick one or more flights whose aircraft breaks "
                     "(check the box on each row)",
                     style={"color": TEXT_DIM, "fontSize": "12px", "marginBottom": "6px"}),
            html.Div(dash.dash_table.DataTable(
                id="flight-table", data=FLIGHT_RECORDS,
                columns=[{"name": n, "id": c} for c, n in [
                    ("flight", "FLIGHT"), ("origin", "FROM"), ("dest", "TO"),
                    ("dep", "DEP"), ("arr", "ARR"), ("orig_tail", "TAIL")]],
                row_selectable="multi",
                selected_rows=[FLIGHT_INDEX_BY_ID[DEFAULT_PICK_FLIGHT]],
                fixed_rows={"headers": True},
                style_table={"height": "300px", "overflowY": "auto",
                            "border": f"1px solid {BORDER}", "borderRadius": "6px"},
                **_TABLE_STYLE,
            )),

            html.Div("2 · Grounding duration per pick (hours - click a HOURS "
                     "cell to edit)",
                     style={"color": TEXT_DIM, "fontSize": "12px", "margin": "14px 0 6px"}),
            html.Div(dash.dash_table.DataTable(
                id="picks-table", data=DEFAULT_PICKS_DATA,
                columns=[
                    {"name": "FLIGHT", "id": "flight", "editable": False},
                    {"name": "ROUTE", "id": "route", "editable": False},
                    {"name": "TAIL", "id": "tail", "editable": False},
                    {"name": "HOURS", "id": "hours", "editable": True, "type": "numeric"},
                ],
                editable=True,
                style_table={"border": f"1px solid {BORDER}", "borderRadius": "6px"},
                **_TABLE_STYLE,
            )),
            html.Div(id="pick-warning", style={"color": ACCENT_RED, "fontSize": "11px",
                                               "marginTop": "8px", "lineHeight": "1.5"}),

            dbc.Button("Run recovery", id="run-btn", color="info", className="mt-4",
                       style={"fontWeight": "600", "width": "100%"}),
            html.Div(id="feasibility-note", style={"color": TEXT_DIM, "fontSize": "11px",
                                                   "marginTop": "16px", "lineHeight": "1.5"}),
        ]),
        dbc.Col(md=8, children=[
            dbc.Row(id="kpi-row", className="g-2"),
            html.Div(id="headline", style={"color": TEXT, "fontSize": "14px",
                                           "fontWeight": "600", "margin": "16px 0 4px"}),
            dcc.RadioItems(id="view-toggle",
                           options=[{"label": " Recovered plan", "value": "after"},
                                    {"label": " As scheduled", "value": "before"}],
                           value="after", inline=True,
                           labelStyle={"marginRight": "16px", "color": TEXT_DIM, "fontSize": "12px"}),
            dcc.Loading(dcc.Graph(id="gantt", config={"displayModeBar": False}), type="dot"),
            html.Div("Recovery actions", style={"color": TEXT_DIM, "fontSize": "12px",
                                                "margin": "6px 0 0"}),
            html.Ul(id="action-list", style={"color": TEXT, "fontSize": "13px"}),
        ]),
    ]),

    html.Div(
        "Synthetic schedule and disruption - built for this demo, not real airline data. "
        "CPLEX MIP via docplex, solved to proven optimality. Cost weights are illustrative "
        "(1/min delay, 2000/cancellation, 150/swap).",
        style={"color": TEXT_DIM, "fontSize": "11px", "marginTop": "22px",
               "borderTop": f"1px solid {BORDER}", "paddingTop": "12px"},
    ),
    dcc.Store(id="solve-tick", data=0),
])


# ----------------------------------------------------------------------
# Selection -> picks table (adds/removes rows, keeps edited hours,
# rejects a second flight on a tail already picked).
# ----------------------------------------------------------------------
@app.callback(
    Output("picks-table", "data"),
    Output("pick-warning", "children"),
    Output("run-btn", "disabled"),
    Input("flight-table", "selected_rows"),
    State("picks-table", "data"),
)
def on_selection_change(selected_rows, current_picks_data):
    current_hours = {row["flight"]: row["hours"] for row in (current_picks_data or [])}
    selected_flight_ids = [FLIGHT_RECORDS[i]["flight"] for i in (selected_rows or [])]

    kept: List[dict] = []
    tail_owner: Dict[str, str] = {}
    warning = ""
    for fid in selected_flight_ids:
        tail = FLIGHT_RECORDS[FLIGHT_INDEX_BY_ID[fid]]["orig_tail"]
        if tail in tail_owner:
            warning = (f"{fid} is on tail {tail}, already grounded via "
                       f"{tail_owner[tail]} — deselect one of them "
                       f"(only one grounding per aircraft).")
            continue
        tail_owner[tail] = fid
        hours = current_hours.get(fid, DEFAULT_HOURS)
        kept.append(_picks_table_row(fid, hours))

    return kept, warning, (len(kept) == 0)


@app.callback(
    Output("solve-tick", "data"),
    Input("run-btn", "n_clicks"),
    State("picks-table", "data"),
    State("solve-tick", "data"),
    prevent_initial_call=True,
)
def on_run(_n, picks_data, tick):
    if not picks_data:
        raise dash.exceptions.PreventUpdate
    picks: List[Tuple[str, float]] = []
    for row in picks_data:
        try:
            hours = float(row["hours"])
        except (TypeError, ValueError):
            continue
        if hours > 0:
            picks.append((row["flight"], hours))
    if not picks:
        raise dash.exceptions.PreventUpdate
    # belt-and-suspenders: the picker already prevents duplicate-tail picks
    # reaching here, but check again before solving so a bad state never
    # gets silently overwritten instead of raising.
    dup = find_duplicate_tail_picks(BASE_INPUTS, picks)
    if dup:
        raise dash.exceptions.PreventUpdate
    _run(picks)
    return (tick or 0) + 1


@app.callback(
    Output("gantt", "figure"),
    Output("kpi-row", "children"),
    Output("headline", "children"),
    Output("action-list", "children"),
    Output("disruption-line", "children"),
    Output("feasibility-note", "children"),
    Input("solve-tick", "data"),
    Input("view-toggle", "value"),
)
def render(_tick, view):
    dm = _LAST["dm"]
    recovery: pd.DataFrame = _LAST["recovery"]
    summary = _LAST["summary"]
    pm = IropPlotlyManager(dm)

    fig = pm.rotation_gantt(recovery, view=view)
    kpis = [kpi_card(t["label"], t["value"]) for t in pm.kpi_tiles(summary)]
    headline = pm.headline(summary, recovery)
    actions = [html.Li(line) for line in pm.action_list(recovery)]

    disruption_df: pd.DataFrame = _LAST["disruption"]
    picks: List[Tuple[str, float]] = _LAST["picks"]
    hours_by_tail = {}
    for fid, hrs in picks:
        tail = FLIGHT_RECORDS[FLIGHT_INDEX_BY_ID[fid]]["orig_tail"]
        hours_by_tail[tail] = (fid, hrs)
    lines = []
    for _, d in disruption_df.iterrows():
        fid, hrs = hours_by_tail.get(d["tail"], ("?", 0))
        lines.append(f"{fid}: tail {d['tail']} grounded {d['unavailable_from']}–"
                     f"{d['unavailable_until']}  ({hrs:g}h)")
    disruption_line = "\n".join(lines)

    rep = _LAST["feasibility"]
    feas = (f"Feasibility check: {'passed' if rep.ok else 'FAILED'} "
            f"({rep.checks_run} checks) · {summary['num_variables']} vars / "
            f"{summary['num_constraints']} constraints · {summary['solve_status']}")
    return fig, kpis, headline, actions, disruption_line, feas


if __name__ == "__main__":
    app.run(debug=False, port=8050)
