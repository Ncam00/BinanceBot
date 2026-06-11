#!/usr/bin/env python3
"""
BinanceBot Copy Trading Dashboard
Run:  python dashboard.py
Open: http://localhost:8050
"""
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output

# ── Config ────────────────────────────────────────────────────────────────────
LOG_PATH   = Path(__file__).parent / "copy_alerts.log"
REFRESH_MS = 10_000
DAILY_TARGET = 5.0
MAX_DEPLOYED = 500.0

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#0a0a0a"
CARD   = "#141414"
BORDER = "#242424"
YELLOW = "#F5C518"
GREEN  = "#00C853"
RED    = "#FF4444"
BLUE   = "#2196F3"
TEXT   = "#FFFFFF"
MUTED  = "#888888"
FONT   = "'Inter', 'Segoe UI', Arial, sans-serif"

# ── Log parser ────────────────────────────────────────────────────────────────
_TS   = r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
_HOLD = re.compile(_TS + r" \[HOLDING\] (.+?): ROI ([\d.+-]+)%\s+MDD ([\d.]+)%\s+Sharpe ([\d.+-]+)\s+WinRate ([\d.]+)%\s+~\$([\d.+-]+)/day\s+~\$([\d.+-]+)/wk\s+Score:(\d+)")
_PNL  = re.compile(_TS + r" \[P&L\] Est daily across (\d+) trader\(s\): \$([\d.+-]+)\s+Target: \$([\d.]+)/day\s+Gap: \$([\d.+-]+)\s+Total deployed: \$([\d.]+)/\$([\d.]+)")
_ENTER= re.compile(_TS + r" \[AUTO-ENTER\] (.+?) \(score (\d+)/100\)")
_EXIT = re.compile(_TS + r" \[AUTO-EXIT\] (.+?): (.+)")
_START= re.compile(_TS + r" \[AUTO-START\] .+ -> (\w+): (.+)")

def parse_log():
    if not LOG_PATH.exists():
        return {}, [], [], []

    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()

    holders   = {}   # name -> latest HOLDING data
    pnl_series= []   # list of (ts, daily_est)
    events    = []   # recent notable events (enter/exit/alert)
    raw_lines = lines[-120:]  # last 120 lines for event feed

    for line in lines:
        m = _HOLD.search(line)
        if m:
            ts, name, roi, mdd, sharpe, wr, daily, weekly, score = m.groups()
            holders[name] = {
                "name": name, "ts": ts,
                "roi": float(roi), "mdd": float(mdd),
                "sharpe": float(sharpe), "win_rate": float(wr),
                "daily": float(daily), "weekly": float(weekly),
                "score": int(score),
            }
            continue

        m = _PNL.search(line)
        if m:
            ts, n_traders, daily, target, gap, deployed, max_dep = m.groups()
            pnl_series.append({
                "ts": ts, "daily": float(daily),
                "n": int(n_traders), "deployed": float(deployed),
            })
            continue

        m = _ENTER.search(line)
        if m:
            events.append({"ts": m.group(1), "type": "ENTER", "text": f"Auto-entered {m.group(2)} (score {m.group(3)})"})
            continue

        m = _EXIT.search(line)
        if m:
            events.append({"ts": m.group(1), "type": "EXIT", "text": f"Auto-exited {m.group(2)}: {m.group(3)[:60]}"})
            continue

    return holders, pnl_series, events, raw_lines


def latest_pnl(pnl_series):
    return pnl_series[-1] if pnl_series else {"daily": 0.0, "n": 0, "deployed": 0.0}


# ── Chart builders ────────────────────────────────────────────────────────────
def daily_chart(pnl_series):
    fig = go.Figure()
    if not pnl_series:
        fig.add_annotation(text="No data yet", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(color=MUTED, size=13, family=FONT))
    else:
        # Group by date, take last reading of each day
        by_day = {}
        for p in pnl_series:
            day = p["ts"][:10]
            by_day[day] = p["daily"]
        days  = sorted(by_day.keys())[-14:]
        vals  = [by_day[d] for d in days]
        colors = [GREEN if v >= DAILY_TARGET else YELLOW if v > 0 else RED for v in vals]
        fig.add_trace(go.Bar(
            x=days, y=vals, marker_color=colors,
            hovertemplate="<b>%{x}</b><br>Est daily: $%{y:.2f}<extra></extra>",
        ))
        fig.add_hline(y=DAILY_TARGET, line_color=GREEN, line_width=1,
                      annotation_text=f"${DAILY_TARGET} target",
                      annotation_font_color=GREEN, annotation_font_size=11)
        fig.add_hline(y=0, line_color=BORDER, line_width=1)

    fig.update_layout(
        paper_bgcolor=CARD, plot_bgcolor=CARD,
        font=dict(color=MUTED, family=FONT, size=11),
        margin=dict(l=45, r=15, t=15, b=40),
        xaxis=dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=10, color=MUTED)),
        yaxis=dict(gridcolor=BORDER, zeroline=False, tickformat="$,.2f",
                   tickfont=dict(size=10, color=MUTED)),
        showlegend=False, height=240, bargap=0.3,
    )
    return fig


def target_gauge(daily_est):
    pct = min(daily_est / DAILY_TARGET * 100, 100)
    color = GREEN if pct >= 100 else YELLOW if pct >= 50 else RED
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=daily_est,
        number={"prefix": "$", "suffix": "/day", "font": {"size": 26, "color": TEXT, "family": FONT}},
        gauge={
            "axis": {"range": [0, DAILY_TARGET], "tickcolor": MUTED,
                     "tickfont": {"size": 10, "color": MUTED, "family": FONT}},
            "bar":  {"color": color, "thickness": 0.25},
            "bgcolor": BORDER, "borderwidth": 0,
            "steps": [{"range": [0, DAILY_TARGET], "color": CARD}],
            "threshold": {"line": {"color": GREEN, "width": 2},
                          "thickness": 0.75, "value": DAILY_TARGET},
        },
        domain={"x": [0, 1], "y": [0, 1]},
    ))
    fig.update_layout(
        paper_bgcolor=CARD, font_family=FONT,
        margin=dict(l=20, r=20, t=30, b=10), height=160,
    )
    return fig


# ── UI helpers ────────────────────────────────────────────────────────────────
def card(children, extra_style=None):
    style = {
        "background": CARD, "border": f"1px solid {BORDER}",
        "borderRadius": "12px", "padding": "20px 24px",
    }
    if extra_style:
        style.update(extra_style)
    return html.Div(children, style=style)


def label(text):
    return html.Div(text, style={
        "color": MUTED, "fontFamily": FONT, "fontSize": "11px", "fontWeight": "600",
        "letterSpacing": "0.8px", "marginBottom": "8px", "textTransform": "uppercase",
    })


def big_number(value, prefix="$", color=None):
    try:
        v = float(value)
    except Exception:
        return html.Div("—", style={"color": MUTED, "fontFamily": FONT, "fontSize": "32px", "fontWeight": "700"})
    c = color or (GREEN if v >= 0 else RED)
    sign = "+" if v > 0 else ""
    return html.Div(f"{sign}{prefix}{abs(v):,.2f}",
                    style={"color": c, "fontFamily": FONT, "fontSize": "32px",
                           "fontWeight": "700", "lineHeight": "1.1"})


def sub(text, color=None):
    return html.Div(text, style={"color": color or MUTED, "fontFamily": FONT,
                                  "fontSize": "13px", "marginTop": "6px"})


def score_badge(score):
    color = GREEN if score >= 80 else YELLOW if score >= 55 else RED
    return html.Span(f"{score}", style={
        "background": color, "color": BG, "fontFamily": FONT,
        "fontSize": "11px", "fontWeight": "700", "borderRadius": "4px",
        "padding": "2px 7px",
    })


def active_trader_card(t):
    roi_color = GREEN if t["roi"] >= 20 else YELLOW if t["roi"] >= 0 else RED
    return html.Div([
        html.Div([
            html.Span(t["name"], style={"color": TEXT, "fontFamily": FONT,
                                        "fontSize": "14px", "fontWeight": "600"}),
            score_badge(t["score"]),
        ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                  "marginBottom": "10px"}),
        html.Div([
            html.Div([
                html.Div("ROI 30D", style={"color": MUTED, "fontFamily": FONT, "fontSize": "10px", "fontWeight": "600", "letterSpacing": "0.5px"}),
                html.Div(f"{t['roi']:+.1f}%", style={"color": roi_color, "fontFamily": FONT, "fontSize": "18px", "fontWeight": "700"}),
            ], style={"flex": "1"}),
            html.Div([
                html.Div("MDD", style={"color": MUTED, "fontFamily": FONT, "fontSize": "10px", "fontWeight": "600", "letterSpacing": "0.5px"}),
                html.Div(f"{t['mdd']:.1f}%", style={"color": YELLOW if t['mdd'] < 10 else RED, "fontFamily": FONT, "fontSize": "18px", "fontWeight": "700"}),
            ], style={"flex": "1"}),
            html.Div([
                html.Div("Sharpe", style={"color": MUTED, "fontFamily": FONT, "fontSize": "10px", "fontWeight": "600", "letterSpacing": "0.5px"}),
                html.Div(f"{t['sharpe']:.2f}", style={"color": TEXT, "fontFamily": FONT, "fontSize": "18px", "fontWeight": "700"}),
            ], style={"flex": "1"}),
            html.Div([
                html.Div("Est Daily", style={"color": MUTED, "fontFamily": FONT, "fontSize": "10px", "fontWeight": "600", "letterSpacing": "0.5px"}),
                html.Div(f"${t['daily']:+.2f}", style={"color": GREEN, "fontFamily": FONT, "fontSize": "18px", "fontWeight": "700"}),
            ], style={"flex": "1"}),
        ], style={"display": "flex", "gap": "8px"}),
        html.Div(f"Last update: {t['ts']}", style={"color": MUTED, "fontFamily": FONT,
                                                    "fontSize": "11px", "marginTop": "8px"}),
    ], style={
        "background": BG, "border": f"1px solid {BORDER}",
        "borderRadius": "8px", "padding": "14px 16px", "marginBottom": "10px",
    })


# ── App ───────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="Copy Trading Dashboard")

app.index_string = '''
<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; font-family: 'Inter', sans-serif; }
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #141414; }
::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
</style></head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>
'''

app.layout = html.Div([
    dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),

    # Header
    html.Div([
        html.Div([
            html.Span("COPY TRADING", style={"color": YELLOW, "fontFamily": FONT,
                                              "fontSize": "16px", "fontWeight": "700", "letterSpacing": "2px"}),
            html.Span(" · ", style={"color": BORDER, "margin": "0 10px"}),
            html.Span("● LIVE", style={"color": GREEN, "fontFamily": FONT, "fontSize": "13px", "fontWeight": "700"}),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Div(id="hdr-time", style={"color": MUTED, "fontFamily": FONT, "fontSize": "13px"}),
    ], style={
        "display": "flex", "justifyContent": "space-between", "alignItems": "center",
        "padding": "16px 24px", "borderBottom": f"1px solid {BORDER}",
        "background": CARD, "marginBottom": "20px", "borderRadius": "12px",
    }),

    # Top stat row
    html.Div([
        card([
            label("Est Daily P&L"),
            html.Div(id="card-daily"),
            html.Div(id="card-daily-sub"),
        ], extra_style={"flex": "1"}),
        card([
            label("Daily Target Progress"),
            dcc.Graph(id="gauge", config={"displayModeBar": False},
                      style={"marginTop": "-10px"}),
        ], extra_style={"flex": "1"}),
        card([
            label("Deployed Capital"),
            html.Div(id="card-deployed"),
            html.Div(id="card-deployed-sub"),
        ], extra_style={"flex": "1"}),
        card([
            label("Active Traders"),
            html.Div(id="card-traders"),
            html.Div(id="card-traders-sub"),
        ], extra_style={"flex": "1"}),
    ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

    # Middle row: active copies + daily chart
    html.Div([
        card([
            label("Active Copies"),
            html.Div(id="active-copies"),
        ], extra_style={"width": "38%", "flexShrink": "0", "overflowY": "auto", "maxHeight": "340px"}),
        card([
            label("Estimated Daily P&L — Last 14 Days"),
            dcc.Graph(id="daily-chart", config={"displayModeBar": False}),
        ], extra_style={"flex": "1"}),
    ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

    # Event log
    card([
        label("Recent Events"),
        html.Div(id="event-log"),
    ]),

], style={"padding": "20px", "minHeight": "100vh", "background": BG})


# ── Callback ──────────────────────────────────────────────────────────────────
@app.callback(
    Output("hdr-time",          "children"),
    Output("card-daily",        "children"),
    Output("card-daily-sub",    "children"),
    Output("gauge",             "figure"),
    Output("card-deployed",     "children"),
    Output("card-deployed-sub", "children"),
    Output("card-traders",      "children"),
    Output("card-traders-sub",  "children"),
    Output("active-copies",     "children"),
    Output("daily-chart",       "figure"),
    Output("event-log",         "children"),
    Input("tick",               "n_intervals"),
)
def refresh(_):
    holders, pnl_series, events, raw_lines = parse_log()
    latest = latest_pnl(pnl_series)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    daily_est = latest["daily"]
    deployed  = latest["deployed"]
    n_traders = latest["n"]

    # Daily P&L card
    gap = DAILY_TARGET - daily_est
    daily_color = GREEN if daily_est >= DAILY_TARGET else YELLOW if daily_est > 0 else RED
    card_daily     = big_number(daily_est, color=daily_color)
    card_daily_sub = sub(f"Gap to ${DAILY_TARGET:.0f} target: ${gap:+.2f}/day",
                         GREEN if gap <= 0 else MUTED)

    # Deployed card
    dep_pct = (deployed / MAX_DEPLOYED * 100) if MAX_DEPLOYED else 0
    card_dep     = html.Div(f"${deployed:.0f}", style={"color": TEXT, "fontFamily": FONT,
                                                        "fontSize": "32px", "fontWeight": "700"})
    card_dep_sub = sub(f"{dep_pct:.0f}% of ${MAX_DEPLOYED:.0f} max  ·  ${MAX_DEPLOYED-deployed:.0f} available")

    # Traders card
    card_tr     = html.Div(str(n_traders), style={"color": TEXT, "fontFamily": FONT,
                                                    "fontSize": "32px", "fontWeight": "700"})
    card_tr_sub = sub(f"of 5 max  ·  {5 - n_traders} slot(s) open")

    # Active copy cards
    if holders:
        copy_cards = [active_trader_card(t) for t in sorted(holders.values(),
                                                              key=lambda x: x["score"], reverse=True)]
    else:
        copy_cards = [html.Div("No active copies yet", style={"color": MUTED, "fontFamily": FONT,
                                                               "fontSize": "13px"})]

    # Event log (last 30 lines, coloured)
    event_rows = []
    for line in reversed(raw_lines[-30:]):
        line = line.strip()
        if not line:
            continue
        if "AUTO-ENTER" in line or "AUTO-EXIT" in line:
            color = GREEN if "ENTER" in line else RED
        elif "HOLDING" in line or "P&L" in line:
            color = MUTED
        elif "ERROR" in line or "failed" in line.lower():
            color = RED
        elif "ALERT" in line:
            color = YELLOW
        else:
            color = MUTED
        event_rows.append(html.Div(line, style={
            "color": color, "fontFamily": "monospace", "fontSize": "12px",
            "padding": "3px 0", "borderBottom": f"1px solid {BORDER}",
            "whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis",
        }))

    return (
        now,
        card_daily, card_daily_sub,
        target_gauge(daily_est),
        card_dep, card_dep_sub,
        card_tr, card_tr_sub,
        copy_cards,
        daily_chart(pnl_series),
        html.Div(event_rows, style={"maxHeight": "320px", "overflowY": "auto"}),
    )


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("   COPY TRADING DASHBOARD")
    print("=" * 50)
    print(f"   Log:  {LOG_PATH}")
    print(f"   URL:  http://localhost:8050")
    print(f"   Auto-refresh every {REFRESH_MS // 1000}s")
    print("=" * 50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=8050)

# ── Time helpers ──────────────────────────────────────────────────────────────
def _nzst():
    return datetime.now(timezone(timedelta(hours=12)))

def nzst_str():
    return _nzst().strftime("%H:%M NZST")

def session_str():
    h = _nzst().hour
    if 11 <= h < 19:
        return "ASIA SESSION"
    elif h >= 19 or h < 3:
        return "EU / LONDON SESSION"
    return "US SESSION"

def today_prefix():
    return datetime.utcnow().date().isoformat()

# ── Chart builders ────────────────────────────────────────────────────────────
def sparkline(df_bal):
    fig = go.Figure()
    if not df_bal.empty and "balance_usdt" in df_bal.columns:
        y = df_bal["balance_usdt"].astype(float).tail(50)
        color = YELLOW if y.iloc[-1] >= y.iloc[0] else RED
        fig.add_trace(go.Scatter(
            y=y, mode="lines",
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor=f"rgba(245,197,24,0.08)" if color == YELLOW else "rgba(255,68,68,0.08)",
            hoverinfo="skip",
        ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=0, b=0), height=60,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig

def pnl_by_day_chart(df):
    fig = go.Figure()
    if not df.empty and "exit_time" in df.columns and "pnl_usdt" in df.columns:
        df = df.copy()
        df["day"] = pd.to_datetime(df["exit_time"]).dt.date
        daily = df.groupby("day")["pnl_usdt"].sum().reset_index()
        daily = daily.sort_values("day").tail(14)
        colors = [YELLOW if v >= 0 else RED for v in daily["pnl_usdt"]]
        fig.add_trace(go.Bar(
            x=daily["day"].astype(str),
            y=daily["pnl_usdt"],
            marker_color=colors,
            hovertemplate="<b>%{x}</b><br>P&L: $%{y:.2f}<extra></extra>",
        ))
        fig.add_hline(y=0, line_color=BORDER, line_width=1)
    else:
        fig.add_annotation(
            text="No trade data yet", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(color=MUTED, size=13, family=FONT),
        )
    fig.update_layout(
        paper_bgcolor=CARD, plot_bgcolor=CARD,
        font=dict(color=MUTED, family=FONT, size=11),
        margin=dict(l=45, r=15, t=15, b=40),
        xaxis=dict(
            gridcolor=BORDER, zeroline=False,
            tickfont=dict(size=10, color=MUTED),
            showline=False,
        ),
        yaxis=dict(
            gridcolor=BORDER, zeroline=False,
            tickformat="$,.2f",
            tickfont=dict(size=10, color=MUTED),
        ),
        showlegend=False, height=240,
        bargap=0.3,
    )
    return fig

# ── UI helpers ────────────────────────────────────────────────────────────────
def card(children, extra_style=None):
    style = {
        "background": CARD,
        "border": f"1px solid {BORDER}",
        "borderRadius": "12px",
        "padding": "20px 24px",
    }
    if extra_style:
        style.update(extra_style)
    return html.Div(children, style=style)

def label(text):
    return html.Div(text, style={
        "color": MUTED, "fontFamily": FONT,
        "fontSize": "12px", "fontWeight": "500",
        "letterSpacing": "0.5px", "marginBottom": "8px",
        "textTransform": "uppercase",
    })

def big_number(value, prefix="$", positive_yellow=True):
    try:
        v = float(value)
    except Exception:
        return html.Div("—", style={"color": MUTED, "fontFamily": FONT, "fontSize": "32px", "fontWeight": "700"})
    color = YELLOW if (v >= 0 and positive_yellow) else RED if v < 0 else TEXT
    sign = "+" if v > 0 else ""
    return html.Div(
        f"{sign}{prefix}{v:,.2f}",
        style={"color": color, "fontFamily": FONT, "fontSize": "32px", "fontWeight": "700", "lineHeight": "1.1"},
    )

def sub_text(text, color=None):
    return html.Div(text, style={
        "color": color or MUTED, "fontFamily": FONT,
        "fontSize": "13px", "marginTop": "6px",
    })

def performer_card(title, symbol, pnl, is_best=True):
    if symbol is None:
        content = html.Div("—", style={"color": MUTED, "fontFamily": FONT, "fontSize": "14px"})
    else:
        try:
            v = float(pnl)
        except Exception:
            v = 0
        color = YELLOW if is_best else RED
        sign = "+" if v >= 0 else ""
        content = html.Div([
            html.Div(str(symbol), style={
                "color": TEXT, "fontFamily": FONT,
                "fontSize": "16px", "fontWeight": "600", "marginBottom": "4px",
            }),
            html.Div(f"{sign}${v:.2f}", style={
                "color": color, "fontFamily": FONT,
                "fontSize": "20px", "fontWeight": "700",
            }),
        ])
    return card([
        label(title),
        content,
    ], extra_style={"marginBottom": "12px"})

# ── App ───────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="BinanceBot")

app.index_string = '''
<!DOCTYPE html>
<html>
<head>
    {%metas%}
    <title>{%title%}</title>
    {%favicon%}
    {%css%}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0a0a0a; font-family: 'Inter', sans-serif; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: #141414; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
    </style>
</head>
<body>
    {%app_entry%}
    <footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>
'''

app.layout = html.Div([
    dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),

    # ── Header ────────────────────────────────────────────────────────────────
    html.Div([
        html.Div([
            html.Span("BINANCEBOT", style={
                "color": YELLOW, "fontFamily": FONT,
                "fontSize": "16px", "fontWeight": "700", "letterSpacing": "2px",
            }),
            html.Span(" · ", style={"color": BORDER, "margin": "0 10px"}),
            html.Span(id="hdr-mode"),
        ], style={"display": "flex", "alignItems": "center"}),
        html.Div([
            html.Span(id="hdr-session", style={"color": MUTED, "fontFamily": FONT, "fontSize": "13px"}),
            html.Span(" · ", style={"color": BORDER, "margin": "0 10px"}),
            html.Span(id="hdr-time", style={"color": MUTED, "fontFamily": FONT, "fontSize": "13px"}),
        ], style={"display": "flex", "alignItems": "center"}),
    ], style={
        "display": "flex", "justifyContent": "space-between", "alignItems": "center",
        "padding": "16px 24px", "borderBottom": f"1px solid {BORDER}",
        "background": CARD, "marginBottom": "20px",
        "borderRadius": "12px",
    }),

    # ── Top 3 stat cards ──────────────────────────────────────────────────────
    html.Div([

        # Balance card with sparkline
        card([
            label("Current Balance"),
            html.Div(id="card-balance"),
            html.Div(id="card-balance-sub"),
            dcc.Graph(id="sparkline", config={"displayModeBar": False},
                      style={"marginTop": "12px"}),
        ], extra_style={"flex": "1"}),

        # Total P&L
        card([
            label("Total P / L"),
            html.Div(id="card-total-pnl"),
            html.Div(id="card-total-pnl-sub"),
            html.Div(id="card-winrate", style={"marginTop": "16px"}),
        ], extra_style={"flex": "1"}),

        # Today's P&L
        card([
            label("Today's P / L"),
            html.Div(id="card-today-pnl"),
            html.Div(id="card-today-sub"),
            html.Div(id="card-today-trades", style={"marginTop": "16px"}),
        ], extra_style={"flex": "1"}),

    ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

    # ── Middle row ────────────────────────────────────────────────────────────
    html.Div([

        # Best / Worst performer
        html.Div([
            html.Div(id="best-trade"),
            html.Div(id="worst-trade"),
            # Open positions mini
            card([
                label("Open Positions"),
                html.Div(id="open-positions"),
            ]),
        ], style={"width": "28%", "flexShrink": "0"}),

        # P&L by day chart
        card([
            html.Div([
                label("P&L by Day"),
                html.Div(id="vol-label"),
            ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start"}),
            dcc.Graph(id="pnl-day-chart", config={"displayModeBar": False}),
        ], extra_style={"flex": "1"}),

    ], style={"display": "flex", "gap": "16px", "marginBottom": "16px"}),

    # ── Trade history table ───────────────────────────────────────────────────
    card([
        html.Div([
            html.Div("Trade History", style={
                "color": TEXT, "fontFamily": FONT,
                "fontSize": "15px", "fontWeight": "600",
            }),
            html.Div(id="trade-count-badge", style={
                "color": MUTED, "fontFamily": FONT, "fontSize": "12px",
                "alignSelf": "center",
            }),
        ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "16px"}),
        html.Div(id="trade-table"),
    ]),

], style={"padding": "20px", "minHeight": "100vh", "background": BG})


# ── Callback ──────────────────────────────────────────────────────────────────
@app.callback(
    Output("hdr-mode",           "children"),
    Output("hdr-mode",           "style"),
    Output("hdr-session",        "children"),
    Output("hdr-time",           "children"),
    Output("card-balance",       "children"),
    Output("card-balance-sub",   "children"),
    Output("sparkline",          "figure"),
    Output("card-total-pnl",     "children"),
    Output("card-total-pnl-sub", "children"),
    Output("card-winrate",       "children"),
    Output("card-today-pnl",     "children"),
    Output("card-today-sub",     "children"),
    Output("card-today-trades",  "children"),
    Output("best-trade",         "children"),
    Output("worst-trade",        "children"),
    Output("open-positions",     "children"),
    Output("vol-label",          "children"),
    Output("pnl-day-chart",      "figure"),
    Output("trade-count-badge",  "children"),
    Output("trade-table",        "children"),
    Input("tick",                "n_intervals"),
)
def refresh(_):
    trades   = load_closed_trades()
    open_pos = load_open_trades()
    bal_hist = load_balance_history()

    # ── Aggregates ────────────────────────────────────────────────────────────
    total      = len(trades)
    wins       = int((trades["pnl_usdt"] > 0).sum())       if not trades.empty else 0
    total_pnl  = float(trades["pnl_usdt"].sum())            if not trades.empty else 0.0
    wr         = wins / total * 100                          if total else 0.0

    # Balance
    if not bal_hist.empty and "balance_usdt" in bal_hist.columns:
        current_bal  = float(bal_hist["balance_usdt"].iloc[-1])
        start_bal    = float(bal_hist["balance_usdt"].iloc[0])
        bal_change   = current_bal - start_bal
    else:
        current_bal = start_bal = bal_change = 0.0

    # Today
    today = today_prefix()
    if not trades.empty and "exit_time" in trades.columns:
        today_t   = trades[trades["exit_time"].str.startswith(today, na=False)]
        today_pnl = float(today_t["pnl_usdt"].sum()) if not today_t.empty else 0.0
        today_n   = len(today_t)
        today_w   = int((today_t["pnl_usdt"] > 0).sum()) if not today_t.empty else 0
    else:
        today_pnl = 0.0; today_n = 0; today_w = 0

    # Best / worst single trade
    if not trades.empty and "pnl_usdt" in trades.columns:
        best_idx  = trades["pnl_usdt"].idxmax()
        worst_idx = trades["pnl_usdt"].idxmin()
        best_sym  = trades.loc[best_idx, "symbol"]  if "symbol" in trades.columns else "?"
        worst_sym = trades.loc[worst_idx, "symbol"] if "symbol" in trades.columns else "?"
        best_pnl  = float(trades.loc[best_idx,  "pnl_usdt"])
        worst_pnl = float(trades.loc[worst_idx, "pnl_usdt"])
    else:
        best_sym = worst_sym = None
        best_pnl = worst_pnl = 0.0

    # ── Header ────────────────────────────────────────────────────────────────
    mode_text  = "● LIVE"
    mode_style = {"color": RED, "fontFamily": FONT, "fontSize": "13px", "fontWeight": "700"}

    # ── Balance card ──────────────────────────────────────────────────────────
    bal_display = big_number(current_bal, positive_yellow=True)
    bal_pct = (bal_change / start_bal * 100) if start_bal else 0
    sign = "+" if bal_change >= 0 else ""
    bal_sub = sub_text(f"{sign}${bal_change:.2f}  ({sign}{bal_pct:.1f}%)",
                       YELLOW if bal_change >= 0 else RED)

    # ── Total P&L card ────────────────────────────────────────────────────────
    pnl_display = big_number(total_pnl)
    pnl_pct = (total_pnl / start_bal * 100) if start_bal else 0
    sign = "+" if total_pnl >= 0 else ""
    pnl_sub  = sub_text(f"{sign}{pnl_pct:.1f}% all time")
    wr_text  = html.Div([
        html.Span(f"{wins}W  {total - wins}L", style={
            "color": TEXT, "fontFamily": FONT, "fontSize": "14px", "fontWeight": "600",
        }),
        html.Span(f"  ·  {wr:.0f}% win rate", style={"color": MUTED, "fontFamily": FONT, "fontSize": "13px"}),
    ])

    # ── Today card ────────────────────────────────────────────────────────────
    today_display = big_number(today_pnl)
    today_sub     = sub_text(f"Daily target  $7.00", MUTED)
    today_trades  = html.Div([
        html.Span(f"{today_n} trade{'s' if today_n != 1 else ''} today", style={
            "color": MUTED, "fontFamily": FONT, "fontSize": "13px",
        }),
        html.Span(f"  ·  {today_w}W", style={"color": YELLOW, "fontFamily": FONT, "fontSize": "13px"}),
    ])

    # ── Best / Worst ──────────────────────────────────────────────────────────
    best_card  = performer_card("Best Trade",  best_sym,  best_pnl,  is_best=True)
    worst_card = performer_card("Worst Trade", worst_sym, worst_pnl, is_best=False)

    # ── Open positions ────────────────────────────────────────────────────────
    if open_pos.empty:
        open_panel = html.Div("No open positions", style={
            "color": MUTED, "fontFamily": FONT, "fontSize": "13px",
        })
    else:
        rows = []
        for _, r in open_pos.iterrows():
            sym = r.get("symbol", "?")
            ep  = r.get("entry_price", 0)
            rows.append(html.Div([
                html.Span(str(sym), style={"color": TEXT, "fontFamily": FONT, "fontSize": "13px", "fontWeight": "600"}),
                html.Span(f"@${float(ep):.2f}", style={"color": MUTED, "fontFamily": FONT, "fontSize": "12px"}),
            ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "6px"}))
        open_panel = html.Div(rows)

    # ── P&L by day label ──────────────────────────────────────────────────────
    if not trades.empty:
        positive_days = 0
        if "exit_time" in trades.columns:
            df_tmp = trades.copy()
            df_tmp["day"] = pd.to_datetime(df_tmp["exit_time"], errors="coerce").dt.date
            daily = df_tmp.groupby("day")["pnl_usdt"].sum()
            positive_days = int((daily > 0).sum())
        vol_label = html.Div(f"{positive_days} profitable day{'s' if positive_days != 1 else ''}",
                             style={"color": YELLOW, "fontFamily": FONT, "fontSize": "13px", "fontWeight": "600",
                                    "alignSelf": "center"})
    else:
        vol_label = html.Div()

    # ── Trade table ───────────────────────────────────────────────────────────
    badge = html.Span(f"{total} total trade{'s' if total != 1 else ''}",
                      style={"color": MUTED, "fontFamily": FONT, "fontSize": "12px"})

    if trades.empty:
        trade_table = html.Div("No trades yet — bot is running", style={
            "color": MUTED, "fontFamily": FONT, "fontSize": "14px",
            "textAlign": "center", "padding": "40px 0",
        })
    else:
        disp = trades.head(50).copy()

        def fmt_pnl(v):
            try:
                f = float(v)
                return f"+${f:.2f}" if f >= 0 else f"-${abs(f):.2f}"
            except Exception:
                return "—"

        disp["P&L"]        = disp["pnl_usdt"].apply(fmt_pnl)
        disp["Entry $"]    = disp["entry_price"].apply(lambda v: f"${float(v):,.4f}" if v else "—")
        disp["Exit $"]     = disp["exit_price"].apply(lambda v: f"${float(v):,.4f}" if v else "—")
        disp["Date"]       = pd.to_datetime(disp["exit_time"], errors="coerce").dt.strftime("%d %b  %H:%M")
        disp["Type"]       = "LONG"
        disp["Symbol"]     = disp.get("symbol", "?")
        disp["Exit Reason"] = disp.get("exit_reason", "—").fillna("—").str.upper()

        cols = ["Date", "Symbol", "Type", "Entry $", "Exit $", "P&L", "Exit Reason"]
        cols = [c for c in cols if c in disp.columns]

        trade_table = dash_table.DataTable(
            data=disp[cols].to_dict("records"),
            columns=[{"name": c, "id": c} for c in cols],
            page_size=15,
            sort_action="native",
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": CARD,
                "color": TEXT,
                "fontFamily": FONT,
                "fontSize": "13px",
                "border": f"1px solid {BORDER}",
                "padding": "10px 14px",
                "textAlign": "left",
                "whiteSpace": "nowrap",
            },
            style_header={
                "backgroundColor": BG,
                "color": MUTED,
                "fontFamily": FONT,
                "fontSize": "11px",
                "fontWeight": "600",
                "border": f"1px solid {BORDER}",
                "textTransform": "uppercase",
                "letterSpacing": "0.8px",
                "padding": "10px 14px",
            },
            style_data_conditional=[
                {"if": {"filter_query": '{P&L} contains "+"'}, "color": YELLOW},
                {"if": {"filter_query": '{P&L} contains "-"'}, "color": RED},
                {"if": {"column_id": "Type"},                  "color": YELLOW},
                {"if": {"state": "active"}, "backgroundColor": "#1e1e1e", "border": f"1px solid {YELLOW}"},
            ],
        )

    return (
        mode_text, mode_style,
        session_str(), nzst_str(),
        bal_display, bal_sub, sparkline(bal_hist),
        pnl_display, pnl_sub, wr_text,
        today_display, today_sub, today_trades,
        best_card, worst_card,
        open_panel,
        vol_label,
        pnl_by_day_chart(trades),
        badge,
        trade_table,
    )


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("   BINANCEBOT  —  DASHBOARD")
    print("=" * 50)
    print(f"   DB:   {DB_PATH}")
    print(f"   URL:  http://localhost:8050")
    print(f"   Auto-refresh every {REFRESH_MS // 1000}s")
    print("=" * 50 + "\n")
    app.run(debug=False, host="0.0.0.0", port=8050)
