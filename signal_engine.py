import ccxt
import pandas as pd
from ta.trend import EMAIndicator
from ta.momentum import RSIIndicator

exchange = ccxt.binance()

def get_signal():

    bars = exchange.fetch_ohlcv(
        'BTC/USDT',
        timeframe='15m',
        limit=200
    )

    df = pd.DataFrame(
        bars,
        columns=[
            'timestamp',
            'open',
            'high',
            'low',
            'close',
            'volume'
        ]
    )

    df['ema20'] = EMAIndicator(
        df['close'],
        window=20
    ).ema_indicator()

    df['ema50'] = EMAIndicator(
        df['close'],
        window=50
    ).ema_indicator()

    df['rsi'] = RSIIndicator(
        df['close'],
        window=14
    ).rsi()

    latest = df.iloc[-1]

    if latest['ema20'] > latest['ema50'] and latest['rsi'] > 55:
        return "BUY"

    elif latest['ema20'] < latest['ema50'] and latest['rsi'] < 45:
        return "SELL"

    return "HOLD"