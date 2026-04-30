import pandas as pd


def get_klines(client, symbol, interval="5m", limit=100):
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)

    df = pd.DataFrame(klines, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore"
    ])

    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df


def analyze_market(df):
    close = df["close"]

    ema_fast = close.ewm(span=9).mean()
    ema_slow = close.ewm(span=21).mean()

    trend = ema_fast.iloc[-1] > ema_slow.iloc[-1]
    momentum = close.iloc[-1] > close.iloc[-3]
    volume_spike = df["volume"].iloc[-1] > df["volume"].rolling(20).mean().iloc[-1] * 1.2

    return trend, momentum, volume_spike


def detect_range(df):
    recent = df[-20:]

    high = recent["high"].max()
    low = recent["low"].min()

    range_size = (high - low) / low

    # tight range = consolidation
    is_range = range_size < 0.01  # 1%

    return is_range, high, low


def detect_breakout(df, range_high):
    close = df["close"].iloc[-1]
    volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].rolling(20).mean().iloc[-1]

    breakout = close > range_high * 1.001  # avoid fakeouts
    volume_confirm = volume > avg_volume * 1.2

    return breakout and volume_confirm


def calc_atr(df, period=14):
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]
