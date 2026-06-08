# -------------------- SESSION STATE INIT --------------------
# (Keep your session state code here...)

# Always pull a fresh copy from disk on every cycle to stay synchronized
st.session_state.journal = load_journal()

# -------------------- UI SETUP (MUST BE ABOVE FETCHING) --------------------
st.set_page_config(page_title="Live Multi-Asset Dashboard", layout="wide")
st.title("🚀 Live Trading Signal Dashboard")

# Global 5-minute Auto Refresh
st_autorefresh(interval=300000, key="global_5min_refresh")

# Asset Selector Option
selected_asset = st.sidebar.selectbox(
    "Select Trading Symbol",
    ["BTC / USDT", "XAU / USD (Gold)"],
    index=0
)

# CRITICAL: This initialization MUST happen before you try to use it below!
if selected_asset == "BTC / USDT":
    symbol = "BTCUSDT"
    is_futures = False
else:
    symbol = "XAUUSDT"
    is_futures = True  

timeframe = st.sidebar.selectbox(
    "Select Timeframe",
    ["1m", "5m", "15m", "30m", "1h", "4h"],
    index=2
)

# -------------------- DYNAMIC FETCH DATA (USES IS_FUTURES) --------------------
if is_futures:
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={timeframe}&limit=1000"
else:
    url = f"https://data.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=1000"

try:
    response = requests.get(url, timeout=10)
    
    if response.status_code == 451:
        if is_futures:
            st.sidebar.warning("🔄 Global Futures blocked by cloud IP region. Routing to Testnet Gateway...")
            url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={symbol}&interval={timeframe}&limit=1000"
            response = requests.get(url, timeout=10)
        else:
            url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=1000"
            response = requests.get(url, timeout=10)

    response.raise_for_status()
    data = response.json()

except Exception as e:
    st.error(f"Error fetching data from Binance ({selected_asset}): {e}")
    st.stop()
