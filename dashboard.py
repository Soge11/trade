import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
import pandas as pd
import requests
import time
import os
import plotly.graph_objects as go
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

# -------------------- TELEGRAM BOT IMPORT CHECK --------------------
try:
    from telegram_bot import send_alert
except ImportError as e:
    st.sidebar.error(f"⚠️ Telegram Import Failed: {e}")
    st.sidebar.warning("Using dummy alert system. Messages will NOT be sent.")
    def send_alert(msg):
        pass

# -------------------- PERSISTENT STORAGE SETUP --------------------
CSV_FILE_PATH = "trading_journal.csv"
JOURNAL_COLUMNS = ["DateTime", "Symbol", "Signal", "Entry", "SL", "TP", "ExitPrice", "PnL (%)", "Result"]

def load_journal():
    """Load journal from local CSV file if it exists, otherwise return empty DataFrame."""
    if os.path.exists(CSV_FILE_PATH):
        try:
            df_loaded = pd.read_csv(CSV_FILE_PATH)
            for col in JOURNAL_COLUMNS:
                if col not in df_loaded.columns:
                    df_loaded[col] = None
            
            # Ensure proper numeric data types on load so comparisons don't break
            df_loaded["Entry"] = pd.to_numeric(df_loaded["Entry"], errors="coerce")
            df_loaded["SL"] = pd.to_numeric(df_loaded["SL"], errors="coerce")
            df_loaded["TP"] = pd.to_numeric(df_loaded["TP"], errors="coerce")
            df_loaded["ExitPrice"] = pd.to_numeric(df_loaded["ExitPrice"], errors="coerce")
            df_loaded["PnL (%)"] = pd.to_numeric(df_loaded["PnL (%)"], errors="coerce")
            
            return df_loaded[JOURNAL_COLUMNS]
        except Exception as e:
            st.error(f"Error loading CSV journal, starting fresh: {e}")
            
    return pd.DataFrame(columns=JOURNAL_COLUMNS)

def save_journal(df_to_save):
    """Save the updated dataframe to local disk storage."""
    try:
        df_to_save.to_csv(CSV_FILE_PATH, index=False)
    except Exception as e:
        st.error(f"Error saving data to local CSV: {e}")

# -------------------- SESSION STATE INIT --------------------
if "last_signal" not in st.session_state:
    st.session_state.last_signal = None

if "last_candle_time" not in st.session_state:
    st.session_state.last_candle_time = None

if "last_alert_time" not in st.session_state:
    st.session_state.last_alert_time = 0.0

# Always pull a fresh copy from disk on every cycle to stay synchronized
st.session_state.journal = load_journal()

# -------------------- UI SETUP --------------------
st.set_page_config(page_title="Live Multi-Asset Dashboard", layout="wide")
st.title("🚀 Live Trading Signal Dashboard")

# Global 5-minute Auto Refresh
st_autorefresh(interval=300000, key="global_5min_refresh")

# 1. NEW FEATURE: Asset Selector Option added to sidebar
selected_asset = st.sidebar.selectbox(
    "Select Trading Symbol",
    ["BTC / USDT", "XAU / USD (Gold)"],
    index=0
)

# Map human selection to exact Binance system strings
if selected_asset == "BTC / USDT":
    symbol = "BTCUSDT"
    is_futures = False
else:
    symbol = "XAUUSDT"
    is_futures = True  # Gold is listed under Binance Futures endpoints

timeframe = st.sidebar.selectbox(
    "Select Timeframe",
    ["1m", "5m", "15m", "30m", "1h", "4h"],
    index=2
)

refresh_map = {
    "1m": 10000,
    "5m": 10000,
    "15m": 15000,
    "30m": 20000,
    "1h": 30000,
    "4h": 60000
}
st_autorefresh(interval=refresh_map[timeframe], key="asset_refresh")

st.sidebar.success(f"Last Refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# -------------------- DYNAMIC FETCH DATA --------------------
# Route to Spot or Futures API depending on the chosen asset symbol
if is_futures:
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={timeframe}&limit=1000"
else:
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=1000"

try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    data = response.json()
except Exception as e:
    st.error(f"Error fetching data from Binance ({selected_asset}): {e}")
    st.stop()

df = pd.DataFrame(
    data,
    columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ]
)

for col in ["open", "high", "low", "close", "volume"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df["time"] = pd.to_datetime(df["time"], unit="ms")
df = df.dropna(subset=["open", "high", "low", "close"])

if df.empty:
    st.warning(f"No candle data available for {symbol}.")
    st.stop()

# -------------------- INDICATORS --------------------
df["EMA15"] = EMAIndicator(df["close"], window=15).ema_indicator()
df["EMA50"] = EMAIndicator(df["close"], window=50).ema_indicator()
df["RSI"] = RSIIndicator(df["close"], window=14).rsi()

df = df.dropna(subset=["EMA15", "EMA50", "RSI"])
if df.empty:
    st.warning("Not enough data to calculate indicators.")
    st.stop()

latest = df.iloc[-1]
price = latest["close"]
ema15 = latest["EMA15"]
ema50 = latest["EMA50"]
rsi = latest["RSI"]
current_candle_time = latest["time"]

# -------------------- STATE ENGINE: TRACK ACTIVE TRADES & PNL --------------------
has_open_trade = False
open_trade_idx = None
live_pnl_pct = 0.0
live_pnl_cash = 0.0

if not st.session_state.journal.empty:
    # Filter open positions tracking ONLY the currently selected symbol
    open_trades = st.session_state.journal[
        (st.session_state.journal["Result"] == "OPEN") & 
        (st.session_state.journal["Symbol"] == symbol)
    ]
    if not open_trades.empty:
        has_open_trade = True
        open_trade_idx = open_trades.index[-1]

# Evaluate open position against current price actions
if has_open_trade:
    trade_row = st.session_state.journal.loc[open_trade_idx]
    trade_time = pd.to_datetime(trade_row["DateTime"])
    trade_type = trade_row["Signal"]
    
    active_entry = float(trade_row["Entry"])
    active_sl = float(trade_row["SL"])
    active_tp = float(trade_row["TP"])
    
    if trade_type == "BUY":
        live_pnl_pct = ((price - active_entry) / active_entry) * 100
        live_pnl_cash = price - active_entry
    else:
        live_pnl_pct = ((active_entry - price) / active_entry) * 100
        live_pnl_cash = active_entry - price

    sub_df = df[df["time"] >= (trade_time - pd.Timedelta(minutes=15))]
    triggered_close = False
    exit_p = None
    res_status = "OPEN"
    
    for idx, row in sub_df.iterrows():
        high_p = float(row["high"])
        low_p = float(row["low"])
        
        if trade_type == "BUY":
            if low_p <= active_sl:
                exit_p = active_sl
                res_status = "SL HIT"
                triggered_close = True
                break
            elif high_p >= active_tp:
                exit_p = active_tp
                res_status = "TP HIT"
                triggered_close = True
                break
        elif trade_type == "SELL":
            if high_p >= active_sl:
                exit_p = active_sl
                res_status = "SL HIT"
                triggered_close = True
                break
            elif low_p <= active_tp:
                exit_p = active_tp
                res_status = "TP HIT"
                triggered_close = True
                break

    if not triggered_close:
        if trade_type == "BUY":
            if price <= active_sl:
                exit_p = active_sl
                res_status = "SL HIT"
                triggered_close = True
            elif price >= active_tp:
                exit_p = active_tp
                res_status = "TP HIT"
                triggered_close = True
        elif trade_type == "SELL":
            if price >= active_sl:
                exit_p = active_sl
                res_status = "SL HIT"
                triggered_close = True
            elif price <= active_tp:
                exit_p = active_tp
                res_status = "TP HIT"
                triggered_close = True

    if triggered_close:
        if trade_type == "BUY":
            final_pnl_pct = ((exit_p - active_entry) / active_entry) * 100
        else:
            final_pnl_pct = ((active_entry - exit_p) / active_entry) * 100
            
        st.session_state.journal.at[open_trade_idx, "ExitPrice"] = round(exit_p, 2)
        st.session_state.journal.at[open_trade_idx, "PnL (%)"] = round(final_pnl_pct, 2)
        st.session_state.journal.at[open_trade_idx, "Result"] = res_status
        save_journal(st.session_state.journal)
        has_open_trade = False

# -------------------- SIGNAL ENGINE --------------------
signal = "HOLD"
if ema15 > ema50 and rsi > 55:
    signal = "BUY"
elif ema15 < ema50 and rsi < 45:
    signal = "SELL"

trend = "Bullish" if ema15 > ema50 else "Bearish"

# -------------------- RISK MANAGEMENT --------------------
risk_pct = 0.005  # 0.5%
tp_rr = 2
entry = price

if signal == "BUY":
    sl = entry * (1 - risk_pct)
    tp = entry * (1 + risk_pct * tp_rr)
elif signal == "SELL":
    sl = entry * (1 + risk_pct)
    tp = entry * (1 - risk_pct * tp_rr)
else:
    sl = entry
    tp = entry

# -------------------- AUTOMATIC JOURNALING & ALERTS --------------------
cooldown_sec = 0  
now = time.time()

# Combined state tracking to check if this exact asset is locked or free to trigger
should_trigger = (
    signal in ["BUY", "SELL"]
    and not has_open_trade  
    and (signal != st.session_state.last_signal or current_candle_time != st.session_state.last_candle_time)
    and (now - st.session_state.last_alert_time > cooldown_sec)
)

if should_trigger:
    new_log = pd.DataFrame([{
        "DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Symbol": symbol,
        "Signal": signal,
        "Entry": round(entry, 2),
        "SL": round(sl, 2),
        "TP": round(tp, 2),
        "ExitPrice": None,
        "PnL (%)": None,
        "Result": "OPEN"
    }])
    
    st.session_state.journal = pd.concat([st.session_state.journal, new_log], ignore_index=True)
    save_journal(st.session_state.journal)

    message = (
        f"🚀 {symbol} SIGNAL ALERT\n\n"
        f"📊 Symbol: {symbol}\n"
        f"⏱ Timeframe: {timeframe}\n"
        f"📌 Signal: {signal}\n\n"
        f"💰 Entry: {round(entry, 2)}\n"
        f"🛑 SL: {round(sl, 2)}\n"
        f"🎯 TP: {round(tp, 2)}\n\n"
        f"📈 Trend: {trend}\n"
        f"📊 RSI: {round(rsi, 2)}\n\n"
        f"🕒 Candle Time: {current_candle_time}\n"
    )
    send_alert(message)
    st.toast(f"New {symbol} {signal} signal auto-logged & sent to Telegram! 🚀")

    st.session_state.last_signal = signal
    st.session_state.last_candle_time = current_candle_time
    st.session_state.last_alert_time = now

# -------------------- DISPLAY CURRENT METRICS --------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"{symbol} Price", f"${price:,.2f}")
c2.metric("Trend Track", trend)
c3.metric("RSI (14)", f"{rsi:.2f}")

if signal == "BUY":
    c4.success("BUY 🟢")
elif signal == "SELL":
    c4.error("SELL 🔴")
else:
    c4.warning("HOLD 🟡")

# -------------------- LIVE RUNNING POSITION DISPLAY --------------------
if has_open_trade:
    st.markdown("---")
    st.subheader(f"📡 Live Active Tracker: {symbol}")
    
    trade_row = st.session_state.journal.loc[open_trade_idx]
    lc1, lc2, lc3, lc4 = st.columns(4)
    
    lc1.metric("Position Type", f"{trade_row['Signal']} ⚡", delta=f"Entry: ${trade_row['Entry']}")
    
    pnl_label = f"+${live_pnl_cash:,.2f} ({live_pnl_pct:+.2f}%)" if live_pnl_cash >= 0 else f"-${abs(live_pnl_cash):,.2f} ({live_pnl_pct:.2f}%)"
    lc2.metric("Floating Live PnL", pnl_label, delta=f"{live_pnl_pct:.2f}%", delta_color="normal" if live_pnl_cash >= 0 else "inverse")
    
    lc3.metric("Stop Loss Level", f"${trade_row['SL']}", delta=f"Distance: ${abs(price - float(trade_row['SL'])):,.2f}", delta_color="inverse")
    lc4.metric("Take Profit Level", f"${trade_row['TP']}", delta=f"Distance: ${abs(float(trade_row['TP']) - price):,.2f}", delta_color="normal")

# -------------------- TRADING JOURNAL DISPLAY & UI METRICS --------------------
st.markdown("---")
st.subheader("📈 Automated Trading Journal & Historical Performance")

if not st.session_state.journal.empty:
    # Filter metrics to track global history
    closed_trades = st.session_state.journal[st.session_state.journal["Result"].isin(["TP HIT", "SL HIT"])]
    total_trades = len(closed_trades)
    wins = len(closed_trades[closed_trades["Result"] == "TP HIT"])
    losses = len(closed_trades[closed_trades["Result"] == "SL HIT"])
    
    if not closed_trades.empty:
        total_pnl = closed_trades["PnL (%)"].sum()
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
    else:
        total_pnl = 0.0
        win_rate = 0.0

    m1, m2, m3, m4 = st.columns(4)
    if total_pnl > 0:
        m1.metric("Total Closed Net PnL", f"🟢 +{total_pnl:.2f}%")
    elif total_pnl < 0:
        m1.metric("Total Closed Net PnL", f"🔴 {total_pnl:.2f}%")
    else:
        m1.metric("Total Closed Net PnL", f"🟡 {total_pnl:.2f}%")
        
    m2.metric("Historical Win Rate", f"{win_rate:.1f}%")
    m3.metric("Closed Wins (TP)", f"{wins} ✅")
    m4.metric("Closed Losses (SL)", f"{losses} ❌")
    
    st.markdown("#### Complete Log Registry")
    
    def style_journal(row):
        if row["Result"] == "TP HIT":
            return ["background-color: rgba(46, 204, 113, 0.12)"] * len(row)
        elif row["Result"] == "SL HIT":
            return ["background-color: rgba(231, 76, 60, 0.12)"] * len(row)
        elif row["Result"] == "OPEN":
            return ["background-color: rgba(241, 196, 15, 0.08)"] * len(row)
        return [""] * len(row)

    styled_df = st.session_state.journal.style.apply(style_journal, axis=1)
    st.dataframe(styled_df, use_container_width=True)
    
    csv_data = st.session_state.journal.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Export Journal History to CSV",
        data=csv_data,
        file_name=f"auto_trading_journal_{datetime.now().strftime('%Y%m%d%H%M')}.csv",
        mime="text/csv",
    )
else:
    st.info("No processing signals captured yet. Waiting for structural indicator validation...")

# -------------------- ADVANCED TRADINGVIEW-LIKE CHART --------------------
st.markdown("---")
fig = go.Figure()

fig.add_trace(
    go.Candlestick(
        x=df["time"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name=symbol
    )
)

fig.add_trace(go.Scatter(x=df["time"], y=df["EMA15"], name="EMA15", line=dict(color='orange', width=1.5)))
fig.add_trace(go.Scatter(x=df["time"], y=df["EMA50"], name="EMA50", line=dict(color='cyan', width=1.5)))

# Ensure lines only overlay on the chart if they match the selected asset
if not st.session_state.journal.empty:
    active_row = st.session_state.journal[
        (st.session_state.journal["Result"] == "OPEN") & 
        (st.session_state.journal["Symbol"] == symbol)
    ]
    if not active_row.empty:
        chart_entry = float(active_row.iloc[-1]["Entry"])
        chart_sl = float(active_row.iloc[-1]["SL"])
        chart_tp = float(active_row.iloc[-1]["TP"])
        
        fig.add_hline(y=chart_entry, line_dash="dash", line_color="gold", annotation_text=f"Entry: {chart_entry}", annotation_position="top right")
        fig.add_hline(y=chart_sl, line_dash="dash", line_color="crimson", annotation_text=f"SL: {chart_sl}", annotation_position="bottom right")
        fig.add_hline(y=chart_tp, line_dash="dash", line_color="springgreen", annotation_text=f"TP: {chart_tp}", annotation_position="top right")

fig.update_layout(
    title=f"{symbol} ({timeframe}) Live Engine Feed",
    height=650,
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    yaxis=dict(
        side="right", 
        title="Price (USDT)",
        gridcolor="rgba(128, 128, 128, 0.1)",
        showgrid=True
    ),
    xaxis=dict(
        gridcolor="rgba(128, 128, 128, 0.1)",
        showgrid=True,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikethickness=1,
        spikedash="dash"
    ),
    dragmode="pan", 
    hovermode="x unified"
)

st.plotly_chart(
    fig, 
    use_container_width=True, 
    config={
        'scrollZoom': True,
        'displayModeBar': True,
        'modeBarButtonsToRemove': ['select2d', 'lasso2d']
    }
)
