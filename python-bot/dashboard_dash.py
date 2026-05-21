"""
Smart Trader — Futuristic Dash Dashboard
http://localhost:8050
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

import plotly.graph_objs as go
from dash import Dash, dcc, html, Input, Output
from dotenv import load_dotenv

load_dotenv()

TRADE_LOG_PATH = os.path.join(os.path.dirname(__file__), "trade_log.jsonl")
WEEKLY_TARGET_USD = 7.00
RECOVERY_TARGET_USD = 12.00  # ~$20 NZD deficit @ 0.595 NZD/USD

# ──────────────────────────────────────────────────────────────────────
# CYBERPUNK PALETTE
# ──────────────────────────────────────────────────────────────────────
BG          = "#05060a"
CARD_BG     = "rgba(15, 20, 35, 0.6)"
CARD_BORDER = "rgba(0, 240, 255, 0.18)"
CARD_GLOW   = "0 0 24px rgba(0, 240, 255, 0.08), inset 0 0 16px rgba(0, 240, 255, 0.03)"
NEON_CYAN   = "#00f0ff"
NEON_PINK   = "#8B3078"
NEON_GREEN  = "#00ff9d"
NEON_RED    = "#ff3860"
NEON_AMBER  = "#ffb000"
NEON_PURPLE = "#b14aff"
TEXT        = "#dce6f5"
SUBTEXT     = "#6a7a96"
GRID        = "rgba(100, 130, 180, 0.12)"

MONO_FONT   = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"
HEAD_FONT   = "'Orbitron', 'Rajdhani', 'Segoe UI', sans-serif"

CARD_STYLE = {
    "background": CARD_BG,
    "border": f"1px solid {CARD_BORDER}",
    "borderRadius": "4px",
    "padding": "18px 20px",
    "flex": "1",
    "minWidth": "150px",
    "boxShadow": CARD_GLOW,
    "backdropFilter": "blur(10px)",
    "position": "relative",
}

STAT_CARD_STYLE = {
    **CARD_STYLE,
    "textAlign": "left",
    "borderLeft": f"2px solid {NEON_CYAN}",
}


# ──────────────────────────────────────────────────────────────────────
# DATA HELPERS
# ──────────────────────────────────────────────────────────────────────

def _normalize(t):
    if "status" in t and ("pnl_usd" in t or "entry" in t):
        return t
    out = dict(t)
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
    if "timestamp" not in t:
        out["timestamp"] = t.get("exit_time") or t.get("entry_time") or ""
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


def parse_ts(t):
    raw = t.get("timestamp", "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_summary(trades):
    closed = closed_trades(trades)
    base = {
        "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
        "net_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
        "today_pnl": 0.0, "week_pnl": 0.0, "best": 0.0, "worst": 0.0,
        "profit_factor": 0.0,
    }
    if not closed:
        return base
    wins   = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    n = len(closed)
    wr = len(wins) / n
    avg_win  = sum(t["pnl_usd"] for t in wins)  / len(wins)  if wins   else 0.0
    avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0.0
    gross_win = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (gross_win if gross_win > 0 else 0.0)

    now = datetime.now(timezone.utc)
    today = now.date()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    today_pnl = 0.0
    week_pnl = 0.0
    for t in closed:
        ts = parse_ts(t)
        if ts is None:
            continue
        if ts.date() == today:
            today_pnl += t.get("pnl_usd", 0)
        if ts >= week_start:
            week_pnl += t.get("pnl_usd", 0)

    pnls = [t.get("pnl_usd", 0) for t in closed]
    return {
        "total": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(wr * 100, 1),
        "net_pnl": round(sum(pnls), 4),
        "avg_win": round(avg_win, 4), "avg_loss": round(avg_loss, 4),
        "expectancy": round((wr * avg_win) + ((1 - wr) * avg_loss), 4),
        "today_pnl": round(today_pnl, 4), "week_pnl": round(week_pnl, 4),
        "best": round(max(pnls), 4) if pnls else 0.0,
        "worst": round(min(pnls), 4) if pnls else 0.0,
        "profit_factor": round(pf, 2),
    }


def bot_status():
    """Detect if the live bot process is running."""
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = p.info.get("cmdline") or []
                if any("smart_trader_v3_live" in str(c) for c in cmd):
                    return True, p.info.get("pid")
            except Exception:
                continue
    except ImportError:
        if os.path.exists(TRADE_LOG_PATH):
            age = time.time() - os.path.getmtime(TRADE_LOG_PATH)
            if age < 600:
                return True, None
    return False, None


def get_session_now():
    h = datetime.now(timezone.utc).hour
    if 7 <= h < 11:
        return "LONDON", NEON_GREEN
    if 13 <= h < 17:
        return "US", NEON_GREEN
    return "CLOSED", SUBTEXT


# ──────────────────────────────────────────────────────────────────────
# APP
# ──────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="Smart Trader · Live")

app.index_string = """
<!DOCTYPE html>
<html>
<head>
  {%metas%}
  <title>{%title%}</title>
  {%favicon%}
  {%css%}
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    body {
      margin: 0;
      background: radial-gradient(ellipse at top, #0a0e1a 0%, #05060a 60%, #02030a 100%);
      background-attachment: fixed;
      min-height: 100vh;
      color: #dce6f5;
      font-family: 'JetBrains Mono', monospace;
    }
    body::before {
      content: '';
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(0, 240, 255, 0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(0, 240, 255, 0.025) 1px, transparent 1px);
      background-size: 48px 48px;
      z-index: 0;
    }
    body::after {
      content: '';
      position: fixed;
      top: -20%; left: -10%;
      width: 60%; height: 60%;
      background: radial-gradient(circle, rgba(0, 240, 255, 0.06), transparent 70%);
      pointer-events: none;
      z-index: 0;
    }
    #_dash-app-content { position: relative; z-index: 1; }
    h1, h2, h3 { font-family: 'Orbitron', sans-serif; letter-spacing: 1.5px; }
    .pulse { animation: pulse 2s ease-in-out infinite; }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    table tr:hover td { background: rgba(0, 240, 255, 0.05); }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #05060a; }
    ::-webkit-scrollbar-thumb { background: rgba(0, 240, 255, 0.25); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(0, 240, 255, 0.5); }
  </style>
</head>
<body>
  {%app_entry%}
  <footer>
    {%config%}
    {%scripts%}
    {%renderer%}
  </footer>
</body>
</html>
"""


def _section_header(text, accent=NEON_CYAN):
    return html.Div([
        html.Span("◆", style={"color": accent, "marginRight": "8px", "fontSize": "0.7em"}),
        html.Span(text, style={
            "color": accent, "fontFamily": HEAD_FONT, "fontWeight": "700",
            "fontSize": "0.78em", "letterSpacing": "2.5px", "textTransform": "uppercase",
        }),
    ], style={"marginBottom": "14px"})


app.layout = html.Div(
    style={
        "minHeight": "100vh", "color": TEXT,
        "padding": "28px 32px", "fontFamily": MONO_FONT,
        "position": "relative", "zIndex": "1",
    },
    children=[
        dcc.Interval(id="interval", interval=10_000, n_intervals=0),

        # Header
        html.Div([
            html.Div([
                html.Div([
                    html.Span("⟦", style={"color": NEON_PINK, "fontSize": "1.6em", "marginRight": "6px"}),
                    html.Span("SMART", style={"color": NEON_CYAN, "fontWeight": "900"}),
                    html.Span("TRADER", style={"color": NEON_PINK, "fontWeight": "900", "marginLeft": "6px"}),
                    html.Span("⟧", style={"color": NEON_CYAN, "fontSize": "1.6em", "marginLeft": "6px"}),
                ], style={
                    "fontFamily": HEAD_FONT, "fontSize": "2.2em", "letterSpacing": "4px",
                    "textShadow": f"0 0 12px {NEON_CYAN}, 0 0 24px rgba(0,240,255,0.4)",
                }),
                html.Div(id="header-meta", style={
                    "color": SUBTEXT, "marginTop": "6px", "fontSize": "0.78em", "letterSpacing": "1.5px",
                }),
            ]),
            html.Div(id="status-pill"),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "marginBottom": "24px", "paddingBottom": "18px",
            "borderBottom": f"1px solid {CARD_BORDER}",
        }),

        # Progress bars
        html.Div(id="progress-bars", style={
            "display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px",
            "marginBottom": "20px",
        }),

        # Stat cards
        html.Div(id="stat-cards", style={
            "display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(150px, 1fr))",
            "gap": "12px", "marginBottom": "20px",
        }),

        # Charts row 1
        html.Div([
            html.Div([
                _section_header("Equity Curve", NEON_CYAN),
                dcc.Graph(id="pnl-chart", config={"displayModeBar": False}, style={"height": "280px"}),
            ], style={**CARD_STYLE, "flex": "2"}),

            html.Div([
                _section_header("Win / Loss Ratio", NEON_PINK),
                dcc.Graph(id="wl-chart", config={"displayModeBar": False}, style={"height": "280px"}),
            ], style={**CARD_STYLE, "flex": "1"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"}),

        # Charts row 2
        html.Div([
            html.Div([
                _section_header("Per-Symbol Performance", NEON_GREEN),
                dcc.Graph(id="symbol-chart", config={"displayModeBar": False}, style={"height": "240px"}),
            ], style={**CARD_STYLE, "flex": "1"}),

            html.Div([
                _section_header("Session Breakdown", NEON_AMBER),
                dcc.Graph(id="session-chart", config={"displayModeBar": False}, style={"height": "240px"}),
            ], style={**CARD_STYLE, "flex": "1"}),

            html.Div([
                _section_header("Exit Reasons", NEON_PURPLE),
                dcc.Graph(id="exit-chart", config={"displayModeBar": False}, style={"height": "240px"}),
            ], style={**CARD_STYLE, "flex": "1"}),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginBottom": "20px"}),

        # PnL bar
        html.Div([
            _section_header("PnL · Per Trade", NEON_CYAN),
            dcc.Graph(id="bar-chart", config={"displayModeBar": False}, style={"height": "260px"}),
        ], style={**CARD_STYLE, "marginBottom": "20px"}),

        # Open positions
        html.Div([
            _section_header("◉ Active Positions", NEON_AMBER),
            html.Div(id="open-positions"),
        ], style={**CARD_STYLE, "marginBottom": "20px",
                  "borderLeft": f"2px solid {NEON_AMBER}"}),

        # Trade log
        html.Div([
            _section_header("⌬ Trade Ledger", NEON_PINK),
            html.Div(id="trade-table"),
        ], style={**CARD_STYLE, "borderLeft": f"2px solid {NEON_PINK}"}),

        html.Div("◇ Auto-refresh 10s · UTC clock · v3.live",
                 style={"color": SUBTEXT, "fontSize": "0.7em", "textAlign": "center",
                        "marginTop": "24px", "letterSpacing": "2px"}),
    ]
)


# ──────────────────────────────────────────────────────────────────────
# UI BUILDERS
# ──────────────────────────────────────────────────────────────────────

def _stat(label, value, accent, sub=None):
    children = [
        html.Div(label, style={
            "color": SUBTEXT, "fontSize": "0.65em", "letterSpacing": "2px",
            "textTransform": "uppercase", "marginBottom": "8px", "fontFamily": HEAD_FONT,
        }),
        html.Div(value, style={
            "color": accent, "fontSize": "1.7em", "fontWeight": "700",
            "fontFamily": MONO_FONT, "letterSpacing": "0.5px",
            "textShadow": f"0 0 8px {accent}55",
        }),
    ]
    if sub:
        children.append(html.Div(sub, style={
            "color": SUBTEXT, "fontSize": "0.7em", "marginTop": "4px",
        }))
    return html.Div(children, style={**STAT_CARD_STYLE, "borderLeft": f"2px solid {accent}"})


def _progress_bar(label, current, target, accent):
    pct = max(0.0, min(1.0, current / target)) if target > 0 else 0.0
    bar_color = accent if current >= 0 else NEON_RED
    fill_pct = pct * 100 if current >= 0 else 0
    return html.Div([
        html.Div([
            html.Span(label, style={
                "color": SUBTEXT, "fontSize": "0.7em", "letterSpacing": "2px",
                "textTransform": "uppercase", "fontFamily": HEAD_FONT,
            }),
            html.Span(f"${current:+.2f} / ${target:.2f}", style={
                "color": bar_color, "fontSize": "0.85em", "fontWeight": "700",
                "fontFamily": MONO_FONT, "float": "right",
            }),
        ], style={"marginBottom": "8px"}),
        html.Div([
            html.Div(style={
                "width": f"{fill_pct}%", "height": "100%",
                "background": f"linear-gradient(90deg, {bar_color}, {NEON_CYAN})",
                "boxShadow": f"0 0 12px {bar_color}",
                "transition": "width 0.5s ease",
                "borderRadius": "2px",
            }),
        ], style={
            "width": "100%", "height": "8px",
            "background": "rgba(255,255,255,0.04)",
            "border": f"1px solid {CARD_BORDER}",
            "borderRadius": "2px", "overflow": "hidden",
        }),
    ], style={**CARD_STYLE, "padding": "14px 18px"})


def _table_header(cols):
    return html.Thead(html.Tr([
        html.Th(c, style={
            "color": NEON_CYAN, "fontFamily": HEAD_FONT, "fontWeight": "600",
            "padding": "10px 12px", "borderBottom": f"1px solid {CARD_BORDER}",
            "textAlign": "left", "fontSize": "0.7em", "letterSpacing": "2px",
            "textTransform": "uppercase",
        }) for c in cols
    ]))


# ──────────────────────────────────────────────────────────────────────
# CALLBACK
# ──────────────────────────────────────────────────────────────────────
@app.callback(
    Output("header-meta", "children"),
    Output("status-pill", "children"),
    Output("progress-bars", "children"),
    Output("stat-cards", "children"),
    Output("pnl-chart", "figure"),
    Output("wl-chart", "figure"),
    Output("symbol-chart", "figure"),
    Output("session-chart", "figure"),
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

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sess, sess_color = get_session_now()
    header_meta = html.Div([
        html.Span("LIVE TRADING TERMINAL", style={"color": NEON_PINK}),
        html.Span(" · ", style={"color": SUBTEXT}),
        html.Span(now_utc, style={"color": NEON_CYAN}),
        html.Span(" · ", style={"color": SUBTEXT}),
        html.Span(f"SESSION: {sess}", style={"color": sess_color, "fontWeight": "700"}),
    ])

    online, pid = bot_status()
    pill_color = NEON_GREEN if online else NEON_RED
    pill_text = f"BOT ONLINE · PID {pid}" if (online and pid) else ("BOT ONLINE" if online else "BOT OFFLINE")
    status_pill = html.Div([
        html.Span("●", className="pulse", style={
            "color": pill_color, "fontSize": "1.2em", "marginRight": "8px",
        }),
        html.Span(pill_text, style={
            "color": pill_color, "fontFamily": HEAD_FONT, "fontWeight": "700",
            "fontSize": "0.85em", "letterSpacing": "2px",
        }),
    ], style={
        "padding": "10px 18px",
        "border": f"1px solid {pill_color}",
        "borderRadius": "2px",
        "background": "rgba(0,0,0,0.3)",
        "boxShadow": f"0 0 16px {pill_color}33",
    })

    progress = [
        _progress_bar("Weekly Target Progress", s["week_pnl"], WEEKLY_TARGET_USD, NEON_GREEN),
        _progress_bar("Account Recovery (vs $780 NZD start)", s["net_pnl"], RECOVERY_TARGET_USD, NEON_AMBER),
    ]

    pnl_color = NEON_GREEN if s["net_pnl"] >= 0 else NEON_RED
    today_color = NEON_GREEN if s["today_pnl"] >= 0 else NEON_RED
    wr_color = NEON_GREEN if s["win_rate"] >= 50 else (NEON_AMBER if s["win_rate"] >= 40 else NEON_RED)
    cards = [
        _stat("Net PnL",       f"${s['net_pnl']:+.3f}",  pnl_color),
        _stat("Today",         f"${s['today_pnl']:+.3f}", today_color),
        _stat("Week",          f"${s['week_pnl']:+.3f}",  NEON_CYAN),
        _stat("Win Rate",      f"{s['win_rate']}%",       wr_color, sub=f"{s['wins']}W · {s['losses']}L"),
        _stat("Trades",        str(s["total"]),           NEON_PURPLE),
        _stat("Profit Factor", f"{s['profit_factor']:.2f}",
              NEON_GREEN if s["profit_factor"] >= 1 else NEON_RED),
        _stat("Expectancy",    f"${s['expectancy']:+.4f}",
              NEON_GREEN if s["expectancy"] >= 0 else NEON_RED),
        _stat("Open",          str(len(opened)),          NEON_AMBER),
    ]

    # Equity curve
    cum, times = [], []
    running = 0.0
    for t in sorted(closed, key=lambda x: x.get("timestamp", "")):
        running += t.get("pnl_usd", 0)
        cum.append(round(running, 5))
        times.append(t.get("timestamp", "")[:16])
    pnl_fig = go.Figure()
    if cum:
        pnl_fig.add_trace(go.Scatter(
            x=times, y=cum, mode="lines",
            line=dict(color=NEON_CYAN, width=2.5, shape="spline"),
            fill="tozeroy", fillcolor="rgba(0,240,255,0.10)",
            hovertemplate="%{x}<br>$%{y:.4f}<extra></extra>",
        ))
        pnl_fig.add_trace(go.Scatter(
            x=times, y=cum, mode="markers",
            marker=dict(color=[NEON_GREEN if v >= 0 else NEON_RED for v in cum],
                        size=8, line=dict(color=BG, width=1.5)),
            showlegend=False, hoverinfo="skip",
        ))
        pnl_fig.add_hline(y=0, line_color=GRID, line_width=1, line_dash="dot")
    pnl_fig.update_layout(**_chart_layout())

    # Win/Loss donut
    wl_fig = go.Figure(go.Pie(
        labels=["WINS", "LOSSES"],
        values=[s["wins"], s["losses"]],
        hole=0.65,
        marker=dict(colors=[NEON_GREEN, NEON_RED], line=dict(color=BG, width=2)),
        textfont=dict(color=TEXT, family=HEAD_FONT, size=12),
        textinfo="label+percent",
        hovertemplate="%{label}: %{value}<extra></extra>",
    ))
    wl_layout = _chart_layout()
    wl_layout["annotations"] = [dict(
        text=f"{s['win_rate']}%", showarrow=False,
        font=dict(color=NEON_CYAN, family=HEAD_FONT, size=24),
    )]
    wl_fig.update_layout(**wl_layout)

    # Per-symbol
    by_symbol = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})
    for t in closed:
        sym = t.get("symbol", "?")
        by_symbol[sym]["pnl"] += t.get("pnl_usd", 0)
        by_symbol[sym]["n"] += 1
        if t.get("pnl_usd", 0) > 0:
            by_symbol[sym]["wins"] += 1
    symbol_fig = go.Figure(go.Bar(
        x=list(by_symbol.keys()),
        y=[v["pnl"] for v in by_symbol.values()],
        marker_color=[NEON_GREEN if v["pnl"] >= 0 else NEON_RED for v in by_symbol.values()],
        marker_line_width=0,
        text=[f"{v['n']}T · {round(v['wins']/v['n']*100) if v['n'] else 0}%" for v in by_symbol.values()],
        textposition="outside",
        textfont=dict(color=SUBTEXT, family=MONO_FONT, size=10),
        hovertemplate="%{x}<br>PnL: $%{y:.4f}<br>%{text}<extra></extra>",
    ))
    symbol_fig.add_hline(y=0, line_color=GRID, line_width=1, line_dash="dot")
    symbol_fig.update_layout(**_chart_layout())

    # Session breakdown
    by_sess = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for t in closed:
        sess_label = (t.get("entry_session") or "unknown").upper()
        by_sess[sess_label]["pnl"] += t.get("pnl_usd", 0)
        by_sess[sess_label]["n"] += 1
    session_fig = go.Figure(go.Bar(
        x=list(by_sess.keys()),
        y=[v["pnl"] for v in by_sess.values()],
        marker_color=[NEON_GREEN if v["pnl"] >= 0 else NEON_RED for v in by_sess.values()],
        marker_line_width=0,
        text=[f"{v['n']}T" for v in by_sess.values()],
        textposition="outside",
        textfont=dict(color=SUBTEXT, family=MONO_FONT, size=10),
        hovertemplate="%{x}<br>$%{y:.4f}<extra></extra>",
    ))
    session_fig.add_hline(y=0, line_color=GRID, line_width=1, line_dash="dot")
    session_fig.update_layout(**_chart_layout())

    # Exit reasons
    reasons = defaultdict(int)
    for t in closed:
        reasons[(t.get("reason") or "UNKNOWN").strip().upper()] += 1
    exit_fig = go.Figure(go.Bar(
        x=list(reasons.keys()), y=list(reasons.values()),
        marker_color=NEON_PURPLE, marker_line_width=0,
        hovertemplate="%{x}: %{y}<extra></extra>",
    ))
    exit_fig.update_layout(**_chart_layout())

    # PnL per trade
    pnls   = [t.get("pnl_usd", 0) for t in closed]
    labels = [f"{(t.get('symbol','?') or '?')[:3]} #{i+1}" for i, t in enumerate(closed)]
    bar_fig = go.Figure(go.Bar(
        x=labels, y=pnls,
        marker_color=[NEON_GREEN if p >= 0 else NEON_RED for p in pnls],
        marker_line_width=0,
        hovertemplate="%{x}<br>$%{y:.5f}<extra></extra>",
    ))
    bar_fig.add_hline(y=0, line_color=GRID, line_width=1, line_dash="dot")
    bar_fig.update_layout(**_chart_layout())

    # Open positions
    if not opened:
        open_div = html.Div("◌ No active positions",
                            style={"color": SUBTEXT, "padding": "12px", "fontStyle": "italic"})
    else:
        rows = []
        for t in opened:
            rows.append(html.Tr([
                html.Td(t.get("symbol", ""),
                        style={"fontWeight": "700", "color": NEON_CYAN, "padding": "8px 12px"}),
                html.Td(t.get("tag", ""), style={"color": NEON_PINK, "padding": "8px 12px"}),
                html.Td(f"${t.get('entry', 0):.4f}", style={"padding": "8px 12px"}),
                html.Td(f"${t.get('sl', 0):.4f}", style={"color": NEON_RED, "padding": "8px 12px"}),
                html.Td(f"${t.get('tp', 0):.4f}", style={"color": NEON_GREEN, "padding": "8px 12px"}),
                html.Td(t.get("timestamp", "")[:16],
                        style={"color": SUBTEXT, "padding": "8px 12px"}),
            ]))
        open_div = html.Table(
            [_table_header(["Symbol", "Type", "Entry", "Stop", "Target", "Time"])] +
            [html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"},
        )

    # Closed log
    if not closed:
        table_div = html.Div("◌ No closed trades yet",
                             style={"color": SUBTEXT, "padding": "12px", "fontStyle": "italic"})
    else:
        rows = []
        for t in reversed(closed[-50:]):
            pnl = t.get("pnl_usd", 0)
            color = NEON_GREEN if pnl >= 0 else NEON_RED
            rows.append(html.Tr([
                html.Td(t.get("symbol", ""),
                        style={"color": NEON_CYAN, "fontWeight": "600", "padding": "8px 12px"}),
                html.Td(t.get("tag", ""), style={"color": SUBTEXT, "padding": "8px 12px"}),
                html.Td(f"${t.get('entry', 0):.4f}", style={"padding": "8px 12px"}),
                html.Td(f"${t.get('exit', 0):.4f}", style={"padding": "8px 12px"}),
                html.Td(f"${pnl:+.5f}",
                        style={"color": color, "fontWeight": "700", "padding": "8px 12px"}),
                html.Td(t.get("reason", "").strip(),
                        style={"color": SUBTEXT, "padding": "8px 12px", "fontSize": "0.85em"}),
                html.Td(t.get("timestamp", "")[:16],
                        style={"color": SUBTEXT, "padding": "8px 12px", "fontSize": "0.8em"}),
            ]))
        table_div = html.Div(
            html.Table(
                [_table_header(["Symbol", "Type", "Entry", "Exit", "PnL", "Reason", "Time"])] +
                [html.Tbody(rows)],
                style={"width": "100%", "borderCollapse": "collapse"},
            ),
            style={"maxHeight": "440px", "overflowY": "auto"},
        )

    return (header_meta, status_pill, progress, cards,
            pnl_fig, wl_fig, symbol_fig, session_fig, exit_fig, bar_fig,
            open_div, table_div)


def _chart_layout():
    return dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, family=MONO_FONT, size=11),
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis=dict(showgrid=False, color=SUBTEXT, tickfont=dict(size=10),
                   linecolor=GRID, zerolinecolor=GRID),
        yaxis=dict(showgrid=True, gridcolor=GRID, color=SUBTEXT, tickfont=dict(size=10),
                   linecolor=GRID, zerolinecolor=GRID),
        showlegend=False,
        hoverlabel=dict(bgcolor="#0a0e1a", bordercolor=NEON_CYAN,
                        font=dict(color=TEXT, family=MONO_FONT)),
    )


if __name__ == "__main__":
    print("🚀 Smart Trader Dashboard · http://localhost:8050")
    app.run(debug=False, port=8050)
