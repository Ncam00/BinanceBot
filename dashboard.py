#!/usr/bin/env python3
"""
BinanceBot Trading Dashboard
Run:  python dashboard.py
Open: http://localhost:8050
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html, dash_table
from dash.dependencies import Input, Output

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH         = Path(__file__).parent / "data" / "trades.db"
REFRESH_MS      = 5_000
DAILY_TARGET    = 7.0
DAILY_LOSS_LIM  = 7.0
EU_CAP          = 2
US_CAP_BASE     = 1

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = "#060810"
PANEL   = "#0c1018"
BORDER  = "#1c2535"
GOLD    = "#f7a435"
TEAL    = "#00d4aa"
RED     = "#ff3d55"
GREEN   = "#00e676"
DIM     = "#3d4f6a"
TEXT    = "#b0bec5"
BRIGHT  = "#e8edf2"
FONT    = "'Courier New', monospace"

# ── Data helpers ──────────────────────────────────────────────────────────────
def _db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def _sql(query, params=()):
    conn = _db()
    if not conn:
        return pd.DataFrame()
    try:
        df = pd.read_sql(query, conn, params=params)
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

def load_closed_trades():
    return _sql("SELECT * FROM trades WHERE exit_time IS NOT NULL ORDER BY exit_time DESC LIMIT 200")

def load_open_trades():
    return _sql("SELECT * FROM trades WHERE exit_time IS NULL ORDER BY entry_time DESC")

def load_balance_history():
    return _sql("SELECT * FROM balance_history ORDER BY recorded_at ASC LIMIT 1000")

def load_signals():
    return _sql("SELECT * FROM signals ORDER BY created_at DESC LIMIT 12")

def nzst_now():
    nzst = timezone(timedelta(hours=12))
    return datetime.now(nzst).strftime("%H:%M NZST")

def nzst_hour():
    nzst = timezone(timedelta(hours=12))
    return datetime.now(nzst).hour

def current_session():
    h = nzst_hour()
    if 11 <= h < 19:
        return "ASIA"
    elif h >= 19 or h < 3:
        return "EU/LONDON"
    return "US"

# ── Style helpers ─────────────────────────────────────────────────────────────
def panel_style(extra=None):
    s = {
        "background": PANEL, "border": f"1px solid {BORDER}",
        "borderRadius": "4px", "padding": "14px", "marginBottom": "10px",
    }
    if extra:
        s.update(extra)
    return s

def section_title(text):
    return html.Div(f"// {text}", style={
        "color": GOLD, "fontFamily": FONT, "fontSize": "10px",
        "letterSpacing": "2px", "marginBottom": "10px",
        "textTransform": "uppercase",
    })

def kv_row(label, value, color=BRIGHT):
    return html.Div([
        html.Span(label, style={"color": DIM, "fontFamily": FONT, "fontSize": "11px", "flex": "1"}),
        html.Span(value, style={"color": color, "fontFamily": FONT, "fontSize": "12px", "fontWeight": "bold"}),
    ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "5px"})

def prog_bar(label, used, cap, danger=False):
    pct = min(100, (used / cap * 100)) if cap else 0
    bar_color = RED if pct >= 100 else GOLD if pct >= 50 else TEAL
    return html.Div([
        html.Div([
            html.Span(label, style={"color": DIM, "fontFamily": FONT, "fontSize": "10px"}),
            html.Span(f"{int(used)}/{int(cap)}", style={"color": TEXT, "fontFamily": FONT, "fontSize": "10px"}),
        ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "3px"}),
        html.Div(
            html.Div(style={
                "width": f"{pct}%", "height": "5px",
                "background": bar_color, "borderRadius": "2px",
            }),
            style={"background": "#0a1020", "borderRadius": "2px", "height": "5px"}
        )
    ], style={"marginBottom": "10px"})

def fmt_pnl(v):
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    color = GREEN if v >= 0 else RED
    sign = "+" if v >= 0 else ""
    return html.Span(f"{sign}${v:.2f}", style={"color": color, "fontFamily": FONT, "fontWeight": "bold"})

def pnl_color(v):
    try:
        return GREEN if float(v) >= 0 else RED
    except Exception:
        return TEXT

# ── Equity curve ──────────────────────────────────────────────────────────────
def make_equity_fig(df_bal, df_trades):
    fig = go.Figure()

    if not df_bal.empty and "balance_usdt" in df_bal.columns:
        x = pd.to_datetime(df_bal["recorded_at"])
        y = df_bal["balance_usdt"].astype(float)
        start = y.iloc[0]
        end   = y.iloc[-1]
        line_color = GREEN if end >= start else RED
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="lines",
            line=dict(color=GOLD, width=2),
            fill="tozeroy", fillcolor="rgba(247,164,53,0.06)",
            name="Balance",
            hovertemplate="<b>%{x|%d %b %H:%M}</b><br>$%{y:.2f}<extra></extra>",
        ))
    else:
        fig.add_annotation(
            text="AWAITING DATA — BOT RUNNING", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(color=DIM, size=13, family=FONT),
        )

    # Entry markers from closed trades
    if not df_trades.empty and "entry_time" in df_trades.columns:
        try:
            wins  = df_trades[df_trades["pnl_usdt"] >= 0]
            losses = df_trades[df_trades["pnl_usdt"] < 0]
            for subset, symbol, color in [(wins, "triangle-up", GREEN), (losses, "triangle-down", RED)]:
                if not subset.empty and "entry_price" in subset.columns:
                    fig.add_trace(go.Scatter(
                        x=pd.to_datetime(subset["entry_time"]),
                        y=subset["entry_price"].astype(float),
                        mode="markers",
                        marker=dict(symbol=symbol, size=8, color=color, opacity=0.7),
                        name="Win" if color == GREEN else "Loss",
                        hovertemplate="<b>%{x|%H:%M}</b><br>Entry $%{y:.4f}<extra></extra>",
                        yaxis="y2",
                    ))
        except Exception:
            pass

    fig.update_layout(
        plot_bgcolor=PANEL, paper_bgcolor=BG,
        font=dict(color=TEXT, family=FONT, size=10),
        margin=dict(l=55, r=10, t=10, b=35),
        xaxis=dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=9)),
        yaxis=dict(gridcolor=BORDER, zeroline=False, tickformat="$,.0f", tickfont=dict(size=9), title=dict(text="Balance USD", font=dict(size=9, color=DIM))),
        yaxis2=dict(overlaying="y", side="right", showgrid=False, tickformat="$,.0f", tickfont=dict(size=8, color=DIM)),
        showlegend=False, height=240,
        hovermode="x unified",
    )
    return fig

# ── P&L per-trade bar chart ───────────────────────────────────────────────────
def make_pnl_bar(df):
    fig = go.Figure()
    if df.empty or "pnl_usdt" not in df.columns:
        fig.add_annotation(text="NO TRADES YET", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(color=DIM, size=12, family=FONT))
    else:
        recent = df.iloc[::-1].tail(30)  # chronological order, last 30
        pnls   = recent["pnl_usdt"].astype(float)
        colors = [GREEN if v >= 0 else RED for v in pnls]
        labels = recent.get("symbol", pd.Series(["?"] * len(recent)))
        fig.add_trace(go.Bar(
            x=list(range(len(pnls))), y=pnls,
            marker_color=colors,
            customdata=labels,
            hovertemplate="<b>%{customdata}</b><br>P&L: $%{y:.2f}<extra></extra>",
        ))
        fig.add_hline(y=0, line_color=DIM, line_width=1)

    fig.update_layout(
        plot_bgcolor=PANEL, paper_bgcolor=BG,
        font=dict(color=TEXT, family=FONT, size=10),
        margin=dict(l=45, r=10, t=10, b=25),
        xaxis=dict(showticklabels=False, gridcolor=BORDER, zeroline=False),
        yaxis=dict(gridcolor=BORDER, zeroline=False, tickformat="$,.2f", tickfont=dict(size=9)),
        showlegend=False, height=160,
        bargap=0.15,
    )
    return fig

# ── Win-rate donut ────────────────────────────────────────────────────────────
def make_donut(wins, total):
    losses = total - wins
    wr = wins / total * 100 if total else 0
    fig = go.Figure(go.Pie(
        values=[wins, losses] if total else [1, 1],
        labels=["Wins", "Losses"],
        hole=0.68,
        marker=dict(colors=[GREEN, RED]),
        textinfo="none",
        hovertemplate="%{label}: %{value}<extra></extra>",
    ))
    fig.add_annotation(
        text=f"{wr:.0f}%", xref="paper", yref="paper",
        x=0.5, y=0.5, showarrow=False,
        font=dict(color=BRIGHT if total else DIM, size=20, family=FONT, weight="bold"),
    )
    fig.update_layout(
        paper_bgcolor=BG, plot_bgcolor=BG,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False, height=120,
    )
    return fig

# ── App ───────────────────────────────────────────────────────────────────────
app = Dash(__name__, title="BinanceBot Edge")
app.layout = html.Div([
    dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),

    # ── Header ────────────────────────────────────────────────────────────────
    html.Div([
        html.Span("// BINANCEBOT EDGE", style={
            "color": GOLD, "fontFamily": FONT, "fontSize": "14px",
            "fontWeight": "bold", "letterSpacing": "3px",
        }),
        html.Div(id="hdr-balance", style={"color": BRIGHT, "fontFamily": FONT, "fontSize": "22px", "fontWeight": "bold"}),
        html.Div([
            html.Span(id="hdr-mode"),
            html.Span("  ·  ", style={"color": DIM}),
            html.Span(id="hdr-time", style={"color": TEXT, "fontFamily": FONT, "fontSize": "12px"}),
            html.Span("  ·  ", style={"color": DIM}),
            html.Span(id="hdr-session", style={"color": TEAL, "fontFamily": FONT, "fontSize": "12px"}),
        ]),
    ], style={
        "background": "#080d18", "border": f"1px solid {BORDER}",
        "borderRadius": "4px", "padding": "14px 20px",
        "marginBottom": "10px", "display": "flex",
        "alignItems": "center", "gap": "30px",
    }),

    # ── 3-column body ─────────────────────────────────────────────────────────
    html.Div([

        # ── LEFT COLUMN ───────────────────────────────────────────────────────
        html.Div([

            html.Div([
                section_title("Equity Targets"),
                html.Div(id="eq-targets"),
            ], style=panel_style()),

            html.Div([
                section_title("Session Slots"),
                html.Div(id="session-slots"),
            ], style=panel_style()),

            html.Div([
                section_title("Open Positions"),
                html.Div(id="open-positions"),
            ], style=panel_style()),

            html.Div([
                section_title("Pair Stats"),
                html.Div(id="pair-stats"),
            ], style=panel_style()),

        ], style={"width": "22%", "flexShrink": "0"}),

        # ── CENTER COLUMN ─────────────────────────────────────────────────────
        html.Div([

            html.Div([
                section_title("Equity Curve"),
                dcc.Graph(id="equity-chart", config={"displayModeBar": False}),
            ], style=panel_style()),

            html.Div([
                section_title("P&L Per Trade  (last 30)"),
                dcc.Graph(id="pnl-bar", config={"displayModeBar": False}),
            ], style=panel_style()),

            html.Div([
                section_title("Trade Log"),
                html.Div(id="trade-log"),
            ], style=panel_style()),

        ], style={"flex": "1", "minWidth": "0"}),

        # ── RIGHT COLUMN ──────────────────────────────────────────────────────
        html.Div([

            html.Div([
                section_title("Performance"),
                dcc.Graph(id="donut", config={"displayModeBar": False}),
                html.Div(id="perf-stats"),
            ], style=panel_style()),

            html.Div([
                section_title("Risk Matrix"),
                html.Div(id="risk-matrix"),
            ], style=panel_style()),

            html.Div([
                section_title("Disposition"),
                html.Div(id="disposition"),
            ], style=panel_style()),

            html.Div([
                section_title("Signal Log"),
                html.Div(id="signal-log"),
            ], style=panel_style()),

        ], style={"width": "25%", "flexShrink": "0"}),

    ], style={"display": "flex", "gap": "10px", "alignItems": "flex-start"}),

], style={"background": BG, "minHeight": "100vh", "padding": "12px", "boxSizing": "border-box"})


# ── Callbacks ─────────────────────────────────────────────────────────────────
@app.callback(
    Output("hdr-balance", "children"),
    Output("hdr-mode", "children"),
    Output("hdr-mode", "style"),
    Output("hdr-time", "children"),
    Output("hdr-session", "children"),
    Output("eq-targets", "children"),
    Output("session-slots", "children"),
    Output("open-positions", "children"),
    Output("pair-stats", "children"),
    Output("equity-chart", "figure"),
    Output("pnl-bar", "figure"),
    Output("trade-log", "children"),
    Output("donut", "figure"),
    Output("perf-stats", "children"),
    Output("risk-matrix", "children"),
    Output("disposition", "children"),
    Output("signal-log", "children"),
    Input("tick", "n_intervals"),
)
def refresh(_):
    trades   = load_closed_trades()
    open_pos = load_open_trades()
    bal_hist = load_balance_history()
    signals  = load_signals()

    # ── Derived stats ──────────────────────────────────────────────────────────
    total       = len(trades)
    wins        = int((trades["pnl_usdt"] > 0).sum()) if not trades.empty else 0
    losses      = total - wins
    total_pnl   = float(trades["pnl_usdt"].sum())      if not trades.empty else 0.0
    avg_pnl     = float(trades["pnl_usdt"].mean())     if not trades.empty else 0.0
    best_trade  = float(trades["pnl_usdt"].max())      if not trades.empty else 0.0
    worst_trade = float(trades["pnl_usdt"].min())      if not trades.empty else 0.0
    wr          = wins / total * 100 if total else 0

    # Today's P&L
    today_str = datetime.utcnow().date().isoformat()
    if not trades.empty and "exit_time" in trades.columns:
        today_trades = trades[trades["exit_time"].str.startswith(today_str)]
        daily_pnl    = float(today_trades["pnl_usdt"].sum()) if not today_trades.empty else 0.0
        daily_wins   = int((today_trades["pnl_usdt"] > 0).sum()) if not today_trades.empty else 0
        daily_count  = len(today_trades)
    else:
        daily_pnl = 0.0
        daily_wins = 0
        daily_count = 0

    # Current balance
    if not bal_hist.empty and "balance_usdt" in bal_hist.columns:
        current_bal = float(bal_hist["balance_usdt"].iloc[-1])
        bal_str     = f"${current_bal:,.2f}"
    else:
        current_bal = 0.0
        bal_str     = "$—"

    # ── Header ────────────────────────────────────────────────────────────────
    mode_text  = "● LIVE"
    mode_style = {"color": RED, "fontFamily": FONT, "fontSize": "12px", "fontWeight": "bold"}

    # ── Equity targets ────────────────────────────────────────────────────────
    daily_profit = max(0, daily_pnl)
    daily_loss   = abs(min(0, daily_pnl))
    eq_targets = html.Div([
        prog_bar("Daily profit", daily_profit, DAILY_TARGET),
        prog_bar("Loss used",   daily_loss,   DAILY_LOSS_LIM, danger=True),
        kv_row("Today P&L", f"{'+'if daily_pnl>=0 else ''}${daily_pnl:.2f}",
               GREEN if daily_pnl >= 0 else RED),
        kv_row("Today trades", str(daily_count)),
    ])

    # ── Session slots ─────────────────────────────────────────────────────────
    sess = current_session()
    session_slots = html.Div([
        kv_row("Session", sess, TEAL),
        prog_bar("EU slots", 0, EU_CAP),        # counters come from bot state, show 0 as placeholder
        prog_bar("US slots", 0, US_CAP_BASE),
        html.Div("Live slot counts visible in bot console", style={
            "color": DIM, "fontFamily": FONT, "fontSize": "9px", "marginTop": "4px"
        }),
    ])

    # ── Open positions ────────────────────────────────────────────────────────
    if open_pos.empty:
        open_panel = html.Div("No open positions", style={"color": DIM, "fontFamily": FONT, "fontSize": "11px"})
    else:
        rows = []
        for _, r in open_pos.iterrows():
            sym = r.get("symbol", "?")
            ep  = r.get("entry_price", 0)
            rows.append(html.Div([
                html.Span(sym, style={"color": GOLD, "fontFamily": FONT, "fontSize": "11px", "flex": "1"}),
                html.Span(f"@${float(ep):.2f}", style={"color": TEXT, "fontFamily": FONT, "fontSize": "10px"}),
            ], style={"display": "flex", "justifyContent": "space-between", "marginBottom": "4px"}))
        open_panel = html.Div(rows)

    # ── Pair stats ────────────────────────────────────────────────────────────
    if trades.empty or "symbol" not in trades.columns:
        pair_panel = html.Div("No trade data", style={"color": DIM, "fontFamily": FONT, "fontSize": "11px"})
    else:
        grp = trades.groupby("symbol")["pnl_usdt"].agg(["sum", "count"]).reset_index()
        rows = []
        for _, r in grp.iterrows():
            pnl = float(r["sum"])
            rows.append(html.Div([
                html.Span(r["symbol"], style={"color": TEXT, "fontFamily": FONT, "fontSize": "11px", "flex": "1"}),
                html.Span(f"{int(r['count'])}t", style={"color": DIM, "fontFamily": FONT, "fontSize": "10px", "marginRight": "8px"}),
                html.Span(f"{'+'if pnl>=0 else ''}${pnl:.2f}",
                          style={"color": GREEN if pnl >= 0 else RED, "fontFamily": FONT, "fontSize": "11px"}),
            ], style={"display": "flex", "marginBottom": "5px"}))
        pair_panel = html.Div(rows)

    # ── Trade log table ───────────────────────────────────────────────────────
    if trades.empty:
        trade_log = html.Div("No closed trades yet — bot is running", style={
            "color": DIM, "fontFamily": FONT, "fontSize": "12px", "textAlign": "center", "padding": "20px"
        })
    else:
        disp = trades.head(15).copy()
        disp["pnl_usdt"] = disp["pnl_usdt"].apply(lambda v: f"{'+'if float(v)>=0 else ''}${float(v):.2f}" if v is not None else "—")
        disp["entry_price"] = disp["entry_price"].apply(lambda v: f"${float(v):.4f}" if v is not None else "—")
        disp["exit_price"]  = disp["exit_price"].apply(lambda v: f"${float(v):.4f}" if v is not None else "—")
        disp["entry_time"]  = disp["entry_time"].apply(lambda v: str(v)[:16] if v else "—")

        cols = ["symbol", "entry_price", "exit_price", "pnl_usdt", "exit_reason", "entry_time"]
        cols = [c for c in cols if c in disp.columns]
        trade_log = dash_table.DataTable(
            data=disp[cols].to_dict("records"),
            columns=[{"name": c.replace("_", " ").title(), "id": c} for c in cols],
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": PANEL, "color": TEXT,
                "fontFamily": FONT, "fontSize": "11px",
                "border": f"1px solid {BORDER}", "padding": "6px 10px",
                "textAlign": "left",
            },
            style_header={
                "backgroundColor": "#0a1020", "color": GOLD,
                "fontFamily": FONT, "fontSize": "10px",
                "border": f"1px solid {BORDER}",
                "textTransform": "uppercase", "letterSpacing": "1px",
            },
            style_data_conditional=[
                {"if": {"filter_query": "{pnl_usdt} contains '+'"},  "color": GREEN},
                {"if": {"filter_query": "{pnl_usdt} contains '-'"},  "color": RED},
            ],
            page_size=15,
        )

    # ── Donut & perf stats ────────────────────────────────────────────────────
    donut = make_donut(wins, total)
    perf_stats = html.Div([
        kv_row("Win rate",    f"{wr:.1f}%",    GREEN if wr >= 50 else GOLD if wr >= 40 else RED),
        kv_row("Total trades", str(total),      BRIGHT),
        kv_row("Total P&L",  f"{'+'if total_pnl>=0 else ''}${total_pnl:.2f}", GREEN if total_pnl >= 0 else RED),
        kv_row("Avg / trade", f"{'+'if avg_pnl>=0 else ''}${avg_pnl:.2f}",   GREEN if avg_pnl >= 0 else RED),
        kv_row("Best trade",  f"+${best_trade:.2f}",   GREEN),
        kv_row("Worst trade", f"-${abs(worst_trade):.2f}", RED),
    ])

    # ── Risk matrix ───────────────────────────────────────────────────────────
    drawdown_pct = (daily_loss / current_bal * 100) if current_bal > 0 else 0
    risk_color   = RED if drawdown_pct >= 4 else GOLD if drawdown_pct >= 2 else GREEN
    risk_matrix = html.Div([
        kv_row("Daily P&L",     f"{'+'if daily_pnl>=0 else ''}${daily_pnl:.2f}", GREEN if daily_pnl >= 0 else RED),
        kv_row("Daily drawdown", f"{drawdown_pct:.1f}%",   risk_color),
        kv_row("Daily target",   f"${DAILY_TARGET:.2f}",   DIM),
        kv_row("Loss limit",     f"${DAILY_LOSS_LIM:.2f}", DIM),
        kv_row("Wins today",     str(daily_wins),           GREEN),
    ])

    # ── Disposition bar ───────────────────────────────────────────────────────
    disp_children = [
        prog_bar("Win rate", wins, total if total else 1),
        html.Div([
            html.Span(f"{wins} W", style={"color": GREEN, "fontFamily": FONT, "fontSize": "12px", "fontWeight": "bold"}),
            html.Span("  /  ", style={"color": DIM}),
            html.Span(f"{losses} L", style={"color": RED, "fontFamily": FONT, "fontSize": "12px", "fontWeight": "bold"}),
        ], style={"textAlign": "center", "marginTop": "6px"}),
    ]
    if total >= 5:
        consec = 0
        for _, r in trades.iterrows():
            if float(r.get("pnl_usdt", 0)) < 0:
                consec += 1
            else:
                break
        disp_children.append(kv_row("Consec. losses", str(consec), RED if consec >= 3 else TEXT))
    disposition = html.Div(disp_children)

    # ── Signal log ────────────────────────────────────────────────────────────
    if signals.empty:
        sig_panel = html.Div("No signals recorded", style={"color": DIM, "fontFamily": FONT, "fontSize": "11px"})
    else:
        rows = []
        for _, r in signals.iterrows():
            sym  = r.get("symbol", "?")
            stype = str(r.get("signal_type", "")).upper()[:6]
            conf  = r.get("confidence", 0)
            acted = r.get("acted_on", 0)
            color = GREEN if acted else DIM
            rows.append(html.Div([
                html.Span(sym,   style={"color": color, "fontFamily": FONT, "fontSize": "10px", "width": "80px"}),
                html.Span(stype, style={"color": TEAL, "fontFamily": FONT, "fontSize": "10px", "width": "50px"}),
                html.Span(f"{float(conf):.0f}", style={"color": GOLD, "fontFamily": FONT, "fontSize": "10px"}),
            ], style={"display": "flex", "gap": "6px", "marginBottom": "4px"}))
        sig_panel = html.Div(rows)

    return (
        bal_str,
        mode_text, mode_style,
        nzst_now(),
        f"SESSION: {current_session()}",
        eq_targets,
        session_slots,
        open_panel,
        pair_panel,
        make_equity_fig(bal_hist, trades),
        make_pnl_bar(trades),
        trade_log,
        donut,
        perf_stats,
        risk_matrix,
        disposition,
        sig_panel,
    )


if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("   BINANCEBOT EDGE  —  TRADING DASHBOARD")
    print("=" * 55)
    print(f"   DB path:  {DB_PATH}")
    print(f"   Refresh:  every {REFRESH_MS // 1000}s")
    print(f"   Open:     http://localhost:8050")
    print("=" * 55 + "\n")
    app.run(debug=False, host="0.0.0.0", port=8050)
