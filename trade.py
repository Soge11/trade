import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
import pandas as pd
import numpy as np
import requests
import time
import os
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from ta.trend import EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator

# --- PREVENT STREAMLIT SIDEBAR COLLAPSE BUGS ---
st.set_page_config(page_title="TradingView Pro Platform", layout="wide")

try:
    from telegram_bot import send_alert
except ImportError:
    def send_alert(msg):
        pass

JOURNAL_FILE = "trading_journal.csv"

if "states" not in st.session_state:
    st.session_state.states = {}

# -------------------- TRADINGVIEW LAYOUT SIDEBAR --------------------
st.sidebar.header("🛠️ Trading Controls")

symbol = st.sidebar.selectbox(
    "Select Trading Asset", 
    ["BTCUSDT", "XAUUSDT"], 
    index=0,
    key="asset_select_dropdown"
)

timeframe = st.sidebar.selectbox(
    "Select Display Timeframe", 
    ["1m", "5m", "15m", "1h", "4h"], 
    index=2,
    key="timeframe_select_dropdown"
)

if symbol not in st.session_state.states:
    st.session_state.states[symbol] = {"last_signal": "HOLD", "last_candle_time": None, "last_alert_time": 0.0}

refresh_map = {"1m": 5000, "5m": 10000, "15m": 15000, "1h": 30000, "4h": 60000}
st_autorefresh(interval=refresh_map[timeframe], key=f"refresh_loop_{symbol}")

# -------------------- MULTI-TIMEFRAME BACKGROUND FEEDS --------------------
def fetch_binance_data(target_symbol, target_tf, limit=300):
    is_futures = (target_symbol == "XAUUSDT")
    base_url = "https://api1.binance.com/fapi/v1/klines" if is_futures else "https://api.binance.com/api/v3/klines"
    url = f"{base_url}?symbol={target_symbol}&interval={target_tf}&limit={limit}"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        raw_data = res.json()
        tdf = pd.DataFrame(raw_data, columns=["time", "open", "high", "low", "close", "volume", "close_time", "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"])
        for col in ["open", "high", "low", "close", "volume"]:
            tdf[col] = pd.to_numeric(tdf[col], errors="coerce")
        tdf["time"] = pd.to_datetime(tdf["time"], unit="ms")
        return tdf.dropna(subset=["open", "high", "low", "close"])
    except Exception as e:
        st.sidebar.error(f"Data Fetch Failure ({target_tf}): {e}")
        return pd.DataFrame()

df = fetch_binance_data(symbol, timeframe, limit=300)
df_30m = fetch_binance_data(symbol, "30m", limit=100)
df_4h = fetch_binance_data(symbol, "4h", limit=50)
df_1d = fetch_binance_data(symbol, "1d", limit=30)

if df.empty or df_30m.empty or df_4h.empty or df_1d.empty:
    st.error("Error synchronizing cross-timeframe structures. Re-syncing...")
    st.stop()

# -------------------- MATH: STABLE TRENDLINE ENGINE --------------------
def calculate_trendlines(data, window=5):
    high_pivots = []
    low_pivots = []
    
    for i in range(window, len(data) - window):
        if data['high'].iloc[i] == data['high'].iloc[i-window:i+window+1].max():
            high_pivots.append((i, data['high'].iloc[i], data['time'].iloc[i]))
        if data['low'].iloc[i] == data['low'].iloc[i-window:i+window+1].min():
            low_pivots.append((i, data['low'].iloc[i], data['time'].iloc[i]))
            
    res_line = None
    if len(high_pivots) >= 2:
        p1, p2 = high_pivots[-2], high_pivots[-1]
        slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
        x_indices = np.arange(p1[0], len(data))
        y_values = p1[1] + slope * (x_indices - p1[0])
        res_line = {"x": [p1[2], data['time'].iloc[-1]], "y": [p1[1], y_values[-1]]}
        
    sup_line = None
    if len(low_pivots) >= 2:
        p1, p2 = low_pivots[-2], low_pivots[-1]
        slope = (p2[1] - p1[1]) / (p2[0] - p1[0])
        x_indices = np.arange(p1[0], len(data))
        y_values = p1[1] + slope * (x_indices - p1[0])
        sup_line = {"x": [p1[2], data['time'].iloc[-1]], "y": [p1[1], y_values[-1]]}
        
    return sup_line, res_line

trendlines_enabled = timeframe not in ["1m", "5m"]
sup_tl, res_tl = calculate_trendlines(df, window=4) if trendlines_enabled else (None, None)

# -------------------- MATH: S&R AND ORDER BLOCKS --------------------
macro_1d_resistance = float(df_1d["high"].max())
macro_1d_support = float(df_1d["low"].min())
macro_4h_resistance = float(df_4h["high"].iloc[-15:].max())
macro_4h_support = float(df_4h["low"].iloc[-15:].min())

bullish_ob, bearish_ob = None, None
for i in range(len(df_30m) - 3, 1, -1):
    c1, c2, c3 = df_30m.iloc[i], df_30m.iloc[i+1], df_30m.iloc[i+2]
    if c1["close"] < c1["open"] and c2["close"] > c2["open"] and c3["close"] > c3["open"]:
        if c3["close"] > max(c1["high"], c2["high"]):
            bullish_ob = {"low": float(c1["low"]), "high": float(c1["high"])}
            break

for i in range(len(df_30m) - 3, 1, -1):
    c1, c2, c3 = df_30m.iloc[i], df_30m.iloc[i+1], df_30m.iloc[i+2]
    if c1["close"] > c1["open"] and c2["close"] < c2["open"] and c3["close"] < c3["open"]:
        if c3["close"] < min(c1["low"], c2["low"]):
            bearish_ob = {"low": float(c1["low"]), "high": float(c1["high"])}
            break

# -------------------- MAIN STRATEGY COMPILATION --------------------
df["EMA15"] = EMAIndicator(df["close"], window=15).ema_indicator()
df["EMA50"] = EMAIndicator(df["close"], window=50).ema_indicator()
df["EMA200"] = EMAIndicator(df["close"], window=200).ema_indicator()
df["RSI"] = RSIIndicator(df["close"], window=14).rsi()
adx_provider = ADXIndicator(df["high"], df["low"], df["close"], window=14)
df["ADX"] = adx_provider.adx()
df["Vol_SMA20"] = df["volume"].rolling(window=20).mean()

latest_valid_rows = df.dropna(subset=["EMA15", "EMA50", "EMA200", "RSI", "ADX", "Vol_SMA20"])
live_candle = latest_valid_rows.iloc[-1]
live_price = float(live_candle["close"])

closed_candle = latest_valid_rows.iloc[-2]
c_close, ema15, ema50, rsi, adx = closed_candle["close"], closed_candle["EMA15"], closed_candle["EMA50"], closed_candle["RSI"], closed_candle["ADX"]
volume, vol_sma, closed_candle_time = closed_candle["volume"], closed_candle["Vol_SMA20"], closed_candle["time"]

signal = "HOLD"
if (adx > 22) and (volume > (vol_sma * 1.1)):
    if (ema15 > ema50) and (c_close > live_candle["EMA200"]) and (rsi > 53): signal = "BUY"
    elif (ema15 < ema50) and (c_close < live_candle["EMA200"]) and (rsi < 47): signal = "SELL"

trend = "Strong Institutional Bullish 🐂" if live_price > live_candle["EMA200"] else "Strong Institutional Bearish 🐻"

risk_pct = 0.002 if symbol == "XAUUSDT" else 0.004
entry = live_price
sl = entry * (1 - risk_pct) if signal == "BUY" else (entry * (1 + risk_pct) if signal == "SELL" else entry)
tp = entry * (1 + (risk_pct * 3)) if signal == "BUY" else (entry * (1 - (risk_pct * 3)) if signal == "SELL" else entry)

# -------------------- HEADLESS APP METRICS DISPLAY --------------------
st.title(f"📈 {symbol} TradingView Pro Terminal")
c1, c2, c3, c4 = st.columns(4)
c1.metric(f"Live Price", f"${live_price:,.2f}")
c2.metric("Market Vector", trend)
c3.metric("ADX Strength", f"{adx:.1f}")
if signal == "BUY": c4.success("INSTITUTIONAL BUY SIGNAL 🟢")
elif signal == "SELL": c4.error("INSTITUTIONAL SELL SIGNAL 🔴")
else: c4.warning("WAITING FOR LIQUIDITY 🟡")

ob_col1, ob_col2 = st.columns(2)
with ob_col1:
    if bullish_ob: st.info(f"🟢 30M Bullish Order Block Zone active: **${bullish_ob['low']:,.2f} - ${bullish_ob['high']:,.2f}**")
with ob_col2:
    if bearish_ob: st.error(f"🔴 30M Bearish Order Block Zone active: **${bearish_ob['low']:,.2f} - ${bearish_ob['high']:,.2f}**")

# -------------------- JOURNAL ENGINE WRITER --------------------
now = time.time()
asset_state = st.session_state.states[symbol]
if signal in ["BUY", "SELL"] and (signal != asset_state["last_signal"] or closed_candle_time != asset_state["last_candle_time"]) and (now - asset_state["last_alert_time"] > 300):
    message = f"🔮 PRO SIGNAL MATCHED\n\n📊 Asset: {symbol}\n📌 Signal: {signal}\n💰 Entry: {round(entry,2)}\n🛑 SL: {round(sl,2)}\n🎯 TP: {round(tp,2)}"
    send_alert(message)
    new_trade = {"DateTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Symbol": symbol, "Signal": signal, "Entry": round(entry, 2), "SL": round(sl, 2), "TP": round(tp, 2), "ExitPrice": "PENDING", "Result": "PENDING", "PnL": "PENDING"}
    pd.DataFrame([new_trade]).to_csv(JOURNAL_FILE, mode='a', header=not os.path.isfile(JOURNAL_FILE), index=False)
    st.session_state.states[symbol]["last_signal"], st.session_state.states[symbol]["last_candle_time"], st.session_state.states[symbol]["last_alert_time"] = signal, closed_candle_time, now

# -------------------- TRADINGVIEW CHART ENGINE --------------------
fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.02, row_heights=[0.65, 0.18, 0.17])

# Trace 1: Candlesticks
fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=symbol), row=1, col=1)
fig.add_trace(go.Scatter(x=df["time"], y=df["EMA15"], name="EMA15", line=dict(color='orange', width=1.2)), row=1, col=1)
fig.add_trace(go.Scatter(x=df["time"], y=df["EMA50"], name="EMA50", line=dict(color='cyan', width=1.2)), row=1, col=1)
fig.add_trace(go.Scatter(x=df["time"], y=df["EMA200"], name="Institutional 200", line=dict(color='white', width=1.8, dash='dash')), row=1, col=1)

# Trace 2: Macro S&R Lines
start_t, end_t = df["time"].min(), df["time"].max()
fig.add_trace(go.Scatter(x=[start_t, end_t], y=[macro_1d_resistance, macro_1d_resistance], name="1D Resistance", line=dict(color="red", width=2.5)), row=1, col=1)
fig.add_trace(go.Scatter(x=[start_t, end_t], y=[macro_1d_support, macro_1d_support], name="1D Support", line=dict(color="green", width=2.5)), row=1, col=1)
fig.add_trace(go.Scatter(x=[start_t, end_t], y=[macro_4h_resistance, macro_4h_resistance], name="4H Resistance", line=dict(color="#FF66CC", width=1.5, dash="dot")), row=1, col=1)
fig.add_trace(go.Scatter(x=[start_t, end_t], y=[macro_4h_support, macro_4h_support], name="4H Support", line=dict(color="#66FF66", width=1.5, dash="dot")), row=1, col=1)

# Trace 3: Trendlines
if trendlines_enabled:
    if res_tl: fig.add_trace(go.Scatter(x=res_tl["x"], y=res_tl["y"], name="Descending Resistance TL", line=dict(color="#FF0055", width=2, dash="dash")), row=1, col=1)
    if sup_tl: fig.add_trace(go.Scatter(x=sup_tl["x"], y=sup_tl["y"], name="Ascending Support TL", line=dict(color="#00FFAA", width=2, dash="dash")), row=1, col=1)

# Trace 4: Order Blocks
if bullish_ob: fig.add_shape(type="rect", x0=start_t, y0=bullish_ob["low"], x1=end_t, y1=bullish_ob["high"], fillcolor="green", opacity=0.15, line_width=0, row=1, col=1)
if bearish_ob: fig.add_shape(type="rect", x0=start_t, y0=bearish_ob["low"], x1=end_t, y1=bearish_ob["high"], fillcolor="red", opacity=0.15, line_width=0, row=1, col=1)

# Subplots for oscillators
fig.add_trace(go.Scatter(x=df["time"], y=df["RSI"], name="RSI Momentum", line=dict(color='purple')), row=2, col=1)
fig.add_trace(go.Scatter(x=df["time"], y=df["ADX"], name="ADX Trend Power", line=dict(color='magenta')), row=3, col=1)
fig.add_hline(y=22, line_dash="dot", line_color="yellow", row=3, col=1)

# -------------------- CONFIGURING RIGHT AXIS & REVISION SYSTEM --------------------
fig.update_layout(
    height=800,
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    dragmode="pan",
    hovermode="x unified",
    
    # TRADINGVIEW CRITICAL FIXED LAYOUT STABILITY
    uirevision=f"{symbol}_{timeframe}", # Locks and remembers zoom position across ticks!
    
    # Flip layout scales directly to the right margin panel
    yaxis1=dict(side="right", title="Price ($)"),
    yaxis2=dict(side="right", title="RSI"),
    yaxis3=dict(side="right", title="ADX Power")
)

# Active user-input scroll handling configuration attributes
config_pro = {
    'scrollZoom': True,          
    'displayModeBar': True,       
    'modeBarButtonsToRemove': ['select2d', 'lasso2d'],
    'editable': False
}

st.plotly_chart(fig, use_container_width=True, config=config_pro)

# -------------------- JOURNAL TABLE DISPLAY --------------------
if os.path.isfile(JOURNAL_FILE):
    st.markdown("---")
    st.subheader("📝 Live Automated Journal Log")
    st.dataframe(pd.read_csv(JOURNAL_FILE).iloc[::-1], use_container_width=True)