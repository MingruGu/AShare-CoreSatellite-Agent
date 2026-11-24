import numpy as np
import pandas as pd


def compute_alpha_factors(df: pd.DataFrame, bench_ret_20d: pd.Series) -> pd.DataFrame:
    """
    计算 V10.0 核心多因子矩阵：
    1. 中期趋势过滤 (60日涨幅 + 站稳60/120日均线)
    2. 相对强弱超额收益 (RS Excess Return)
    3. 短期缩量回调乖离 (Short-Term Mean Reversion)
    4. 量价吸收与尾盘重心 (Volume-Price Absorption & CLV)
    5. 真实波幅 ATR14
    """
    df = df.copy()
    symbol = str(df["symbol"].iloc[0])

    # A股涨跌停板限制 (科创/创业板20%，主板10%)
    limit_pct = 0.198 if symbol.startswith(("300", "301", "688")) else 0.098
    df["prev_close"] = df["close"].shift(1)
    df["ret"] = (df["close"] / df["prev_close"] - 1.0).fillna(0.0)
    df["cannot_buy"] = (df["open"] >= df["prev_close"] * (1 + limit_pct)) & (df["low"] == df["high"])
    df["cannot_sell"] = (df["open"] <= df["prev_close"] * (1 - limit_pct)) & (df["low"] == df["high"])

    ma20 = df["close"].rolling(20).mean()
    ma60 = df["close"].rolling(60).mean()
    ma120 = df["close"].rolling(120).mean()
    std20 = df["close"].rolling(20).std()

    df["above_ma60"] = (df["close"] > ma60).fillna(False).astype(int)
    df["mom_20d"] = df["close"] / df["close"].shift(20) - 1.0
    df["mom_60d"] = df["close"] / df["close"].shift(60) - 1.0

    # 1) 相对强弱超额收益 (RS Alpha)
    df = df.merge(bench_ret_20d.rename("bench_20d"), on="date", how="left")
    df["rs_excess_20d"] = df["mom_20d"] - df["bench_20d"].fillna(0.0)

    # 2) 短期缩量回调乖离
    df["ret_3d"] = df["close"] / df["close"].shift(3) - 1.0
    df["ret_5d"] = df["close"] / df["close"].shift(5) - 1.0
    df["bb_z"] = (df["close"] - ma20) / (std20 + 1e-9)

    # 3) 量价吸收与尾盘重心 CLV
    vol_ratio_5_20 = df["amount"].rolling(5).mean() / (df["amount"].rolling(20).mean() + 1e-9)
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"] + 1e-9)

    mature_ok = ma120.notna() & (df["close"] > ma120 * 0.96)
    cond_trend = mature_ok & (df["mom_60d"] > 0.03) & (df["close"] > ma60 * 0.985) & (df["rs_excess_20d"] > 0.0)
    cond_pullback = ((df["ret_3d"] < -0.01) | (df["ret_5d"] < -0.015)) & (df["bb_z"] < 0.35)
    cond_absorb = (vol_ratio_5_20 < 1.05) & (clv > -0.1)

    score_rs = np.clip((df["rs_excess_20d"] + 0.02) / 0.22, 0, 1) * 35.0
    score_mom = np.clip(df["mom_60d"] / 0.35, 0, 1) * 25.0
    score_pb = np.clip((-np.minimum(df["ret_3d"], df["ret_5d"])) / 0.09, 0, 1) * 20.0
    score_clv = np.clip((clv + 0.5) / 1.5, 0, 1) * 20.0

    df["alpha_score"] = np.where(
        cond_trend & cond_pullback & cond_absorb,
        score_rs + score_mom + score_pb + score_clv,
        0.0,
    )

    # 4) ATR14 波动率
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum(abs(df["high"] - df["prev_close"]), abs(df["low"] - df["prev_close"])),
    )
    df["atr14"] = tr.rolling(14).mean().bfill()
    df["atr_pct"] = (df["atr14"] / df["close"]).fillna(0.03)
    return df
