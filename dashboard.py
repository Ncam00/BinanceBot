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
DB_PATH    = Path(__file__).parent / "data" / "trades.db"
REFRESH_MS = 5_000

# ── Palette ───────────────────────────────────────────────────────────────────
BG     = "#0a0a0a"
CARD   = "#141414"
BORDER = "#242424"
YELLOW = "#F5C518"
RED    = "#FF4444"
TEXT   = "#FFFFFF"
MUTED  = "#888888"
FONT   = "'Inter', 'Segoe UI', Arial, sans-serif"

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
        return pd.read_sql(query, conn, params=params)
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
