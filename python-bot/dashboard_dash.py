"""
Smart Trader — Dash Dashboard
http://localhost:8050
"""

import json
import os
from datetime import datetime, timezone

import plotly.graph_objs as go
from dash import Dash, dcc, html, Input, Output
from dotenv import load_dotenv

load_dotenv()

TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")

# ──────────────────────────────────────────────────────────────────────
# DATA HELPERS
# ──────────────────────────────────────────────────────────────────────

def _normalize(t):
    """Map new-schema trade entries (live bot) to the old schema used by the dashboard.
    New schema: pair, profit, entry_price, exit_price, exit_time, exit_reason, trade_type, win
    Old schema: symbol, pnl_usd, entry, exit, timestamp, reason, tag, status
    """
    # Already in old schema (paper-trade entries)
    if "status" in t and ("pnl_usd" in t or "entry" in t):
        return t

    out = dict(t)
    # Field aliases
    if "pair" in t and "symbol" not in t:
        out["symbol"] = t["pair"]
    if "profit" in t and "pnl_usd" not in t:
        out["pnl_usd"] = t["profit"]
    if "entry_price" in t and "entry" not in t:
        out["entry"] = t["entry_price"]
    if "exit_price" in t and "exit" not in t:
        out["exit"] = t["exit_price"]
    if "exit_reason" in t and "reason" not in t:
        out["reason"] = t["exit_reason"]
    if "trade_type" in t and "tag" not in t:
        out["tag"] = t["trade_type"]
    if "stop_loss" in t and "sl" not in t:
        out["sl"] = t["stop_loss"]
    if "take_profit" in t and "tp" not in t:
        out["tp"] = t["take_profit"]
    # Timestamp: prefer exit_time, fall back to entry_time
    if "timestamp" not in t:
        out["timestamp"] = t.get("exit_time") or t.get("entry_time") or ""
    # Status: presence of exit_time/exit_price = closed
    if "status" not in t:
        out["status"] = "closed" if (t.get("exit_time") or t.get("exit_price") is not None) else "open"
    return out


def load_trades():
    trades = []
    if not os.path.exists(TRADE_LOG_PATH):
        return trades
    with open(TRADE_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(_normalize(json.loads(line)))
                except Exception:
                    pass
    return trades


def closed_trades(trades):
    return [t for t in trades if t.get("status") == "closed"]


def open_trades(trades):
    return [t for t in trades if t.get("status") == "open"]


def get_summary(trades):
    closed = closed_trades(trades)
    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "net_pnl": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
        }
    wins   = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    n = len(closed)
    wr = len(wins) / n
    avg_win  = sum(t["pnl_usd"] for t in wins)  / len(wins)  if wins   else 0.0
    avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0.0
    return {
        "total":      n,
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   round(wr * 100, 1),
        "net_pnl":    round(sum(t.get("pnl_usd", 0) for t in closed), 4),
        "avg_win":    round(avg_win, 4),
        "avg_loss":   round(avg_loss, 4),
        "expectancy": round((wr * avg_win) + ((1 - wr) * avg_loss), 4),
    }


# ──────────────────────────────────────────────────────────────────────
# COLOURS
# ──────────────────────────────────────────────────────────────────────
BG        = "#0d1117"
CARD_BG   = "#161b22"
BORDER    = "#30363d"
GREEN     = "#00ff88"
RED       = "#ff6b6b"
BLUE      = "#00d4ff"
YELLOW    = "#ffd700"
TEXT      = "#e6edf3"
SUBTEXT   = "#8b949e"

CARD_STYLE = {
    "background": CARD_BG,
    "border": f"1px solid {BORDER}",
    "borderRadius": "12px",
    "padding": "20px",
    "flex": "1",
    "minWidth": "160px",
    "textAlign": "center",
}

# ──────────────────────────────────────────────────────────────────────
# APP
# ──────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="Smart Trader Dashboard")
app.layout = html.Div(
    style={"background": BG, "minHeight": "100vh", "fontFamily": "'Segoe UI', Arial, sans-serif", "color": TEXT, "padding": "24px"},
    children=[
        # ── Header ──────────────────────────────────────────────────
        html.Div([
            html.H1("Smart Trader", style={"background": f"linear-gradient(90deg, {BLUE}, {GREEN})",
                                            "WebkitBackgroundClip": "text", "WebkitTextFillColor": "transparent",
                                            "margin": "0", "fontSize": "2.2em"}),
            html.P("Paper Trading Dashboard  •  Auto-refreshes every 30s",
                   style={"color": SUBTEXT, "margin": "4px 0 0 0"}),
        ], style={"textAlign": "center", "marginBottom": "28px"}),

        # ── Interval ────────────────────────────────────────────────
        dcc.Interval(id="interval", interval=30_000, n_intervals=0),

        # ── Stat cards ──────────────────────────────────────────────
        html.Div(id="stat-cards", style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "24px"}),

        # ── Charts row ──────────────────────────────────────────────
        html.Div([
            html.Div([
                html.H3("Cumulative PnL", style={"color": BLUE, "margin": "0 0 12px 0", "fontSize": "1em"}),
                dcc.Graph(id="pnl-chart", config={"displayModeBar": False}, style={"height": "260px"}),
            ], style={**CARD_STYLE, "flex": "2", "textAlign": "left"}),

            html.Div([
                html.H3("Win / Loss", style={"color": BLUE, "margin": "0 0 12px 0", "fontSize": "1em"}),
                dcc.Graph(id="wl-chart", config={"displayModeBar": False}, style={"height": "260px"}),
            ], style={**CARD_STYLE, "flex": "1", "textAlign": "left"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "24px"}),

        # ── Exit reason + PnL per trade ─────────────────────────────
        html.Div([
            html.Div([
                html.H3("Exit Reasons", style={"color": BLUE, "margin": "0 0 12px 0", "fontSize": "1em"}),
                dcc.Graph(id="exit-chart", config={"displayModeBar": False}, style={"height": "240px"}),
            ], style={**CARD_STYLE, "flex": "1", "textAlign": "left"}),

            html.Div([
                html.H3("PnL per Trade", style={"color": BLUE, "margin": "0 0 12px 0", "fontSize": "1em"}),
                dcc.Graph(id="bar-chart", config={"displayModeBar": False}, style={"height": "240px"}),
            ], style={**CARD_STYLE, "flex": "2", "textAlign": "left"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "24px"}),

        # ── Open positions ───────────────────────────────────────────
        html.Div([
            html.H3("Open Positions", style={"color": BLUE, "margin": "0 0 14px 0", "fontSize": "1em"}),
            html.Div(id="open-positions"),
        ], style={**CARD_STYLE, "textAlign": "left", "marginBottom": "24px"}),

        # ── Trade log ───────────────────────────────────────────────
        html.Div([
            html.H3("Closed Trade Log", style={"color": BLUE, "margin": "0 0 14px 0", "fontSize": "1em"}),
            html.Div(id="trade-table"),
        ], style={**CARD_STYLE, "textAlign": "left"}),
    ]
)


# ──────────────────────────────────────────────────────────────────────
# CALLBACKS
# ──────────────────────────────────────────────────────────────────────

def _card(label, value, colour):
    return html.Div([
        html.Div(label, style={"color": SUBTEXT, "fontSize": "0.8em", "marginBottom": "6px"}),
        html.Div(value, style={"color": colour, "fontSize": "1.9em", "fontWeight": "bold"}),
    ], style=CARD_STYLE)


@app.callback(
    Output("stat-cards", "children"),
    Output("pnl-chart", "figure"),
    Output("wl-chart", "figure"),
    Output("exit-chart", "figure"),
    Output("bar-chart", "figure"),
    Output("open-positions", "children"),
    Output("trade-table", "children"),
    Input("interval", "n_intervals"),
)
def refresh(_):
    trades = load_trades()
    closed = closed_trades(trades)
    opened = open_trades(trades)
    s = get_summary(trades)

    # ── Stat cards ────────────────────────────────────────────────
    pnl_colour = GREEN if s["net_pnl"] >= 0 else RED
    cards = [
        _card("Closed Trades",  str(s["total"]),                     BLUE),
        _card("Win Rate",       f"{s['win_rate']}%",                  GREEN if s["win_rate"] >= 40 else RED),
        _card("Net PnL",        f"${s['net_pnl']:.3f}",              pnl_colour),
        _card("Avg Win",        f"${s['avg_win']:.4f}",              GREEN),
        _card("Avg Loss",       f"${s['avg_loss']:.4f}",             RED),
        _card("Expectancy",     f"${s['expectancy']:.4f}",           GREEN if s["expectancy"] >= 0 else RED),
        _card("Open Now",       str(len(opened)),                     YELLOW),
    ]

    # ── Cumulative PnL chart ──────────────────────────────────────
    cum, times = [], []
    running = 0.0
    for t in sorted(closed, key=lambda x: x.get("timestamp", "")):
        running += t.get("pnl_usd", 0)
        cum.append(round(running, 5))
        times.append(t.get("timestamp", "")[:16])

    pnl_fig = go.Figure()
    if cum:
        colours = [GREEN if v >= 0 else RED for v in cum]
        pnl_fig.add_trace(go.Scatter(
            x=times, y=cum,
            mode="lines+markers",
            line=dict(color=BLUE, width=2),
            marker=dict(color=colours, size=7),
            fill="tozeroy",
            fillcolor="rgba(0,212,255,0.07)",
            hovertemplate="%{x}<br>Cum PnL: $%{y:.5f}<extra></extra>",
        ))
    pnl_fig.update_layout(**_chart_layout())

    # ── Win/Loss pie ──────────────────────────────────────────────
    wl_fig = go.Figure(go.Pie(
        labels=["Wins", "Losses"],
        values=[s["wins"], s["losses"]],
        hole=0.55,
        marker=dict(colors=[GREEN, RED]),
        textfont=dict(color=TEXT),
        hovertemplate="%{label}: %{value}<extra></extra>",
    ))
    wl_fig.update_layout(**_chart_layout())

    # ── Exit reason bar ───────────────────────────────────────────
    reasons = {}
    for t in closed:
        r = t.get("reason", "UNKNOWN").strip()
        reasons[r] = reasons.get(r, 0) + 1
    exit_fig = go.Figure(go.Bar(
        x=list(reasons.keys()),
        y=list(reasons.values()),
        marker_color=BLUE,
        hovertemplate="%{x}: %{y}<extra></extra>",
    ))
    exit_fig.update_layout(**_chart_layout())

    # ── PnL per trade bars ────────────────────────────────────────
    pnls   = [t.get("pnl_usd", 0) for t in closed]
    labels = [f"{t.get('symbol','?')} #{i+1}" for i, t in enumerate(closed)]
    bar_colours = [GREEN if p >= 0 else RED for p in pnls]
    bar_fig = go.Figure(go.Bar(
        x=labels, y=pnls,
        marker_color=bar_colours,
        hovertemplate="%{x}<br>PnL: $%{y:.5f}<extra></extra>",
    ))
    bar_fig.add_hline(y=0, line_color=BORDER, line_width=1)
    bar_fig.update_layout(**_chart_layout())

    # ── Open positions ────────────────────────────────────────────
    if not opened:
        open_div = html.P("No open positions", style={"color": SUBTEXT})
    else:
        rows = []
        for t in opened:
            rows.append(html.Tr([
                html.Td(t.get("symbol", ""), style={"fontWeight": "bold"}),
                html.Td(t.get("tag", "")),
                html.Td(f"${t.get('entry', 0):.2f}"),
                html.Td(f"${t.get('sl', 0):.2f}", style={"color": RED}),
                html.Td(f"${t.get('tp', 0):.2f}", style={"color": GREEN}),
                html.Td(t.get("timestamp", "")[:16], style={"color": SUBTEXT}),
            ]))
        open_div = html.Table(
            [_table_header(["Symbol", "Tag", "Entry", "SL", "TP", "Time"])] +
            [html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"},
        )

    # ── Closed trade log ──────────────────────────────────────────
    if not closed:
        table_div = html.P("No closed trades yet", style={"color": SUBTEXT})
    else:
        rows = []
        for t in reversed(closed):
            pnl = t.get("pnl_usd", 0)
            colour = GREEN if pnl >= 0 else RED
            rows.append(html.Tr([
                html.Td(t.get("symbol", "")),
                html.Td(t.get("tag", ""), style={"color": SUBTEXT}),
                html.Td(f"${t.get('entry', 0):.2f}"),
                html.Td(f"${t.get('exit', 0):.2f}"),
                html.Td(f"${pnl:.5f}", style={"color": colour, "fontWeight": "bold"}),
                html.Td(t.get("reason", "").strip(), style={"color": SUBTEXT}),
                html.Td(t.get("timestamp", "")[:16], style={"color": SUBTEXT, "fontSize": "0.85em"}),
            ]))
        table_div = html.Table(
            [_table_header(["Symbol", "Tag", "Entry", "Exit", "PnL", "Reason", "Time"])] +
            [html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"},
        )

    return cards, pnl_fig, wl_fig, exit_fig, bar_fig, open_div, table_div


# ──────────────────────────────────────────────────────────────────────
# LAYOUT HELPERS
# ──────────────────────────────────────────────────────────────────────

def _chart_layout():
    return dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, family="Segoe UI, Arial"),
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis=dict(showgrid=False, color=SUBTEXT, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor=BORDER, color=SUBTEXT, tickfont=dict(size=10)),
        showlegend=False,
    )


def _table_header(cols):
    return html.Thead(html.Tr([
        html.Th(c, style={
            "color": BLUE, "fontWeight": "600",
            "padding": "8px 12px", "borderBottom": f"1px solid {BORDER}",
            "textAlign": "left", "fontSize": "0.85em",
        }) for c in cols
    ]))


# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 Dash dashboard starting at http://localhost:8050")
    app.run(debug=False, port=8050)
