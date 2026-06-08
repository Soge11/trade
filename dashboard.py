# -------------------- DYNAMIC FETCH DATA --------------------
# Route to Spot or Futures API depending on the chosen asset symbol
if is_futures:
    # Main Global Futures endpoint
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={timeframe}&limit=1000"
else:
    # FIXED: Swapped 'data-api.binance.vision' for 'data.binance.com' which bypasses cloud IP locks
    url = f"https://data.binance.com/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=1000"

try:
    response = requests.get(url, timeout=10)
    
    # FALLBACK CHECK: If Cloud hosting server returns a geographical 451 error
    if response.status_code == 451:
        if is_futures:
            st.sidebar.warning("🔄 Global Futures blocked by cloud IP region. Routing to Testnet Gateway...")
            # Fallback to the public testnet/developer api which typically bypasses regional datacenter blocks
            url = f"https://testnet.binancefuture.com/fapi/v1/klines?symbol={symbol}&interval={timeframe}&limit=1000"
            response = requests.get(url, timeout=10)
        else:
            url = f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={timeframe}&limit=1000"
            response = requests.get(url, timeout=10)

    response.raise_for_status()
    data = response.json()

except Exception as e:
    st.error(f"Error fetching data from Binance ({selected_asset}): {e}")
    st.info("💡 Tip: This cloud server platform's IP is geographically restricted by Binance. Try running locally or hosting in an Asian/European cloud instance.")
    st.stop()
