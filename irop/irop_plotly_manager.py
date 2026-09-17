"""IropPlotlyManager - figures for the recovery dashboard.

Subclass of ``dse_do_utils.plotlymanager.PlotlyManager``.  Each method is
self-contained (takes the recovery DataFrame explicitly) so it can also be
called from a notebook.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from dse_do_utils.plotlymanager import PlotlyManager

from irop.irop_data_manager import IropDataManager, fmt_hhmm

_BASE_DAY = datetime(2026, 1, 1)

COLORS = {
    "ontime": "#4C5766",
    "delay": "#F5A623",
    "swap": "#2DD4BF",
    "cancel": "#E5484D",
    "blackout": "rgba(229,72,77,0.12)",
    "grid": "#2B323C",
    "text": "#E7EAEE",
    "text_dim": "#8C97A6",
    "panel": "#191E25",
}


def _ts(minutes: float) -> datetime:
    return _BASE_DAY + timedelta(minutes=float(minutes))


class IropPlotlyManager(PlotlyManager[IropDataManager]):
    def __init__(self, dm: IropDataManager) -> None:
        super().__init__(dm)

    # ------------------------------------------------------------------
    def rotation_gantt(self, recovery: pd.DataFrame, view: str = "after") -> go.Figure:
        """One row per tail; each flight a bar.  view='before' shows the
        scheduled plan on the original tails, view='after' the recovered plan."""
        dm = self.dm
        tail_order = list(dm.tails.sort_index().index)  # A1..B2 top-to-bottom (y reversed)

        cat_color = {
            "on schedule": COLORS["ontime"],
            "delayed": COLORS["delay"],
            "swapped": COLORS["swap"],
        }

        rows = []
        for _, r in recovery.iterrows():
            tail = r["orig_tail"] if view == "before" else r["assigned_tail"]
            if tail is None or pd.isna(tail):
                continue
            if view == "before":
                dep, cat = int(r["sched_dep_min"]), "on schedule"
            else:
                if r["cancelled"]:
                    continue
                dep = int(r["actual_dep_min"])
                cat = ("swapped" if r["swapped"]
                       else "delayed" if r["delay_min"] > 0 else "on schedule")
            dur = dm.leg_minutes(r["flight"])
            rows.append(dict(
                Tail=tail, Start=_ts(dep), Finish=_ts(dep + dur), Flight=r["flight"],
                Status=cat, Route=f"{r['origin']}→{r['dest']}", Dep=fmt_hhmm(dep),
                Delay=(f"+{int(r['delay_min'])}m" if view == "after" and r["delay_min"] > 0 else ""),
            ))

        df = pd.DataFrame(rows)
        fig = px.timeline(
            df, x_start="Start", x_end="Finish", y="Tail", color="Status", text="Flight",
            color_discrete_map=cat_color,
            category_orders={"Tail": tail_order,
                             "Status": ["on schedule", "delayed", "swapped"]},
            custom_data=["Flight", "Route", "Dep", "Delay"],
        )
        fig.update_traces(
            width=0.55, textposition="inside", insidetextanchor="middle",
            textfont=dict(size=10, color="#0A1014"),
            hovertemplate="<b>%{customdata[0]}</b> %{customdata[1]}<br>"
                          "dep %{customdata[2]} %{customdata[3]}<extra></extra>",
        )
        fig.update_yaxes(autorange="reversed", title=None, gridcolor=COLORS["grid"])

        # blackout band(s)
        for _tail, (from_min, until_min) in dm.disrupted_tail_windows.items():
            fig.add_vrect(x0=_ts(from_min), x1=_ts(until_min),
                          fillcolor=COLORS["blackout"], line_width=0, layer="below")

        # cancelled markers
        if view == "after":
            for _, r in recovery[recovery["cancelled"]].iterrows():
                fig.add_trace(go.Scatter(
                    x=[_ts(r["sched_dep_min"])], y=[r["orig_tail"]],
                    mode="markers+text", marker=dict(symbol="x", size=12, color=COLORS["cancel"]),
                    text=[f" {r['flight']} cancelled"], textposition="middle right",
                    textfont=dict(color=COLORS["cancel"], size=10), hoverinfo="skip",
                    name="cancelled", legendgroup="cancelled",
                ))

        lo = _ts(dm.flights["sched_dep_min"].min() - 20)
        hi = _ts((dm.flights["sched_dep_min"] + dm.flights["duration_min"]).max() + 20)
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=COLORS["panel"], plot_bgcolor=COLORS["panel"],
            height=90 + 46 * len(tail_order),
            margin=dict(l=48, r=24, t=10, b=28),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, font=dict(size=11)),
            font=dict(color=COLORS["text"]),
            bargap=0.35,
        )
        fig.update_xaxes(range=[lo, hi], tickformat="%H:%M", dtick=3600000 * 2,
                         gridcolor=COLORS["grid"], title=None)
        return fig

    # ------------------------------------------------------------------
    @staticmethod
    def kpi_tiles(summary: Dict[str, object]) -> List[Dict[str, str]]:
        return [
            {"label": "Total delay added", "value": f"{summary['total_delay_min']} min"},
            {"label": "Flights swapped", "value": str(summary["num_swapped"])},
            {"label": "Flights cancelled", "value": str(summary["num_cancelled"])},
            {"label": "Recovery cost", "value": f"{summary['objective_value']:.0f}"},
        ]

    # ------------------------------------------------------------------
    @staticmethod
    def action_list(recovery: pd.DataFrame) -> List[str]:
        changed = recovery[recovery["swapped"] | (recovery["delay_min"] > 0) | recovery["cancelled"]]
        if changed.empty:
            return ["No changes needed - the schedule absorbs the disruption as planned."]
        lines: List[str] = []
        for _, r in changed.iterrows():
            route = f"{r['flight']} ({r['origin']}→{r['dest']})"
            if r["cancelled"]:
                lines.append(f"Cancel {route}")
            elif r["swapped"] and r["delay_min"] > 0:
                lines.append(f"Move {route} from {r['orig_tail']} to {r['assigned_tail']}, "
                             f"delay {int(r['delay_min'])} min")
            elif r["swapped"]:
                lines.append(f"Move {route} from {r['orig_tail']} to {r['assigned_tail']}")
            else:
                lines.append(f"Delay {route} by {int(r['delay_min'])} min")
        return lines

    @staticmethod
    def headline(summary: Dict[str, object], recovery: pd.DataFrame) -> str:
        if summary["num_swapped"] == 0 and summary["num_cancelled"] == 0 and summary["total_delay_min"] == 0:
            return "No beneficial recovery action - no aircraft has usable slack in this window."
        if summary["num_swapped"] == 0 and summary["num_cancelled"] == 0:
            return (f"No swap available - optimizer absorbed the disruption with "
                    f"{summary['total_delay_min']} min of delay in place.")
        bits = []
        if summary["num_swapped"]:
            bits.append(f"{summary['num_swapped']} flight(s) swapped")
        if summary["num_cancelled"]:
            bits.append(f"{summary['num_cancelled']} cancelled")
        bits.append(f"{summary['total_delay_min']} min total delay")
        return "Recovered: " + ", ".join(bits) + "."
