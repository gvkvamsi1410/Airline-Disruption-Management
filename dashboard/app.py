"""IROP recovery dashboard - interactive disruption picker.

Flow: pick a flight in the table -> the aircraft operating it is grounded from
the moment it lands, for the chosen number of hours -> "Run recovery" re-solves
the CPLEX model -> the recovered plan, KPIs and action list update.

Run:  python dashboard/app.py   then open http://127.0.0.1:8050
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, dcc, html

from irop.irop_data_manager import fmt_hhmm
from irop.irop_plotly_manager import IropPlotlyManager
from irop.solve import disruption_from_flight, load_inputs_from_excel, solve_scenario

EXCEL = ROOT / "assets" / "data_asset" / "IropInputs.xlsx"
BASE_INPUTS = load_inputs_from_excel(EXCEL)

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

# cache of the most recent solve (single-user demo)
_LAST: dict = {}


def _run(flight_id: str, hours: float) -> None:
    disruption = disruption_from_flight(BASE_INPUTS, flight_id, hours)
    res = solve_scenario(BASE_INPUTS, disruption=disruption)
    _LAST.update(
        dm=res["dm"], recovery=res["flight_recovery"], summary=res["summary"],
        disruption=disruption, feasibility=res["feasibility"],
        pick=(flight_id, hours),
    )


_run("F202", 4)  # default scenario on load

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], title="IROP Recovery")

_TABLE_STYLE = dict(
    style_as_list_view=True,
    style_header={"backgroundColor": "#12161B", "color": "#8C97A6", "fontWeight": "600",
                  "border": "none", "fontSize": "12px"},
    style_cell={"backgroundColor": "#191E25", "color": "#E7EAEE", "border": "none",
                "fontSize": "13px", "fontFamily": "monospace", "padding": "6px 10px"},
    style_data_conditional=[{"if": {"state": "selected"},
                             "backgroundColor": "#1F3A37", "border": "1px solid #2DD4BF"}],
)


def kpi_card(label: str, value: str) -> dbc.Col:
    return dbc.Col(dbc.Card(dbc.CardBody([
        html.Div(label, style={"fontSize": "12px", "color": "#8C97A6"}),
        html.Div(value, style={"fontSize": "24px", "fontWeight": "700", "color": "#E7EAEE"}),
    ]), style={"backgroundColor": "#191E25", "border": "1px solid #232A32"}), md=3, xs=6)


app.layout = dbc.Container(fluid=True, style={"backgroundColor": "#12161B", "minHeight": "100vh",
                                              "padding": "22px 26px"}, children=[
    html.H4("IROP tail-swap recovery", style={"color": "#E7EAEE", "fontWeight": "600"}),
    html.Div(id="disruption-line", style={"color": "#F5A623", "fontSize": "13px", "marginBottom": "16px"}),

    dbc.Row([
        dbc.Col(md=4, children=[
            html.Div("1 · Pick the flight whose aircraft breaks",
                     style={"color": "#8C97A6", "fontSize": "12px", "marginBottom": "6px"}),
            html.Div(dash.dash_table.DataTable(
                id="flight-table", data=FLIGHT_RECORDS,
                columns=[{"name": n, "id": c} for c, n in [
                    ("flight", "FLIGHT"), ("origin", "FROM"), ("dest", "TO"),
                    ("dep", "DEP"), ("arr", "ARR"), ("orig_tail", "TAIL")]],
                row_selectable="single", selected_rows=[
                    next(i for i, r in enumerate(FLIGHT_RECORDS) if r["flight"] == "F202")],
                fixed_rows={"headers": True},
                style_table={"height": "430px", "overflowY": "auto"}, **_TABLE_STYLE,
            )),
            html.Div("2 · Grounding duration (hours)",
                     style={"color": "#8C97A6", "fontSize": "12px", "margin": "16px 0 10px"}),
            dcc.Slider(id="dur-slider", min=1, max=6, step=0.5, value=4,
                       marks={i: {"label": f"{i}h", "style": {"color": "#5C6673"}}
                              for i in range(1, 7)}),
            dbc.Button("Run recovery", id="run-btn", color="info", className="mt-4",
                       style={"fontWeight": "600", "width": "100%"}),
            html.Div(id="feasibility-note", style={"color": "#5C6673", "fontSize": "11px",
                                                   "marginTop": "16px", "lineHeight": "1.5"}),
        ]),
        dbc.Col(md=8, children=[
            dbc.Row(id="kpi-row", className="g-2"),
            html.Div(id="headline", style={"color": "#E7EAEE", "fontSize": "14px",
                                           "fontWeight": "600", "margin": "16px 0 4px"}),
            dcc.RadioItems(id="view-toggle",
                           options=[{"label": " Recovered plan", "value": "after"},
                                    {"label": " As scheduled", "value": "before"}],
                           value="after", inline=True,
                           labelStyle={"marginRight": "16px", "color": "#8C97A6", "fontSize": "12px"}),
            dcc.Loading(dcc.Graph(id="gantt", config={"displayModeBar": False}), type="dot"),
            html.Div("Recovery actions", style={"color": "#8C97A6", "fontSize": "12px",
                                                "margin": "6px 0 0"}),
            html.Ul(id="action-list", style={"color": "#E7EAEE", "fontSize": "13px"}),
        ]),
    ]),

    html.Div(
        "Synthetic schedule and disruption - built for this demo, not real airline data. "
        "CPLEX MIP via docplex, solved to proven optimality. Cost weights are illustrative "
        "(1/min delay, 2000/cancellation, 150/swap).",
        style={"color": "#5C6673", "fontSize": "11px", "marginTop": "22px",
               "borderTop": "1px solid #232A32", "paddingTop": "12px"},
    ),
    dcc.Store(id="solve-tick", data=0),
])


@app.callback(
    Output("solve-tick", "data"),
    Input("run-btn", "n_clicks"),
    State("flight-table", "selected_rows"),
    State("dur-slider", "value"),
    State("solve-tick", "data"),
    prevent_initial_call=True,
)
def on_run(_n, selected_rows, hours, tick):
    if not selected_rows:
        raise dash.exceptions.PreventUpdate
    flight_id = FLIGHT_RECORDS[selected_rows[0]]["flight"]
    _run(flight_id, float(hours))
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

    d = _LAST["disruption"].iloc[0]
    fid, hrs = _LAST["pick"]
    disruption_line = (f"{fid}: tail {d['tail']} grounded {d['unavailable_from']}–"
                       f"{d['unavailable_until']}  ({hrs:g}h)")

    rep = _LAST["feasibility"]
    feas = (f"Feasibility check: {'passed' if rep.ok else 'FAILED'} "
            f"({rep.checks_run} checks) · {summary['num_variables']} vars / "
            f"{summary['num_constraints']} constraints · {summary['solve_status']}")
    return fig, kpis, headline, actions, disruption_line, feas


if __name__ == "__main__":
    app.run(debug=False, port=8050)
