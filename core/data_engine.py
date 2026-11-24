import os
import time
import akshare as ak
import pandas as pd
import numpy as np


class SinaDataEngine:
    """纯新浪财经抗封锁双通道数据引擎（实时快照 + 前复权日线 + 本地CSV缓存）"""

    def __init__(self, cache_dir: str = "./data_cache"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _to_sina_symbol(symbol: str) -> str:
        if symbol.startswith(("sh", "sz")):
            return symbol
        prefix = "sh" if symbol.startswith(("60", "68", "51", "58")) else "sz"
        return f"{prefix}{symbol}"

    def get_realtime_snapshot(self) -> pd.DataFrame:
        """拉取全市场最新实时快照（适配 akshare 1.20.46+）"""
        spot_df = ak.stock_zh_a_spot_sina()
        col_map = {
            "symbol": "代码",
            "code": "代码",
            "name": "名称",
            "trade": "最新价",
            "price": "最新价",
            "changepercent": "涨跌幅",
            "pct_chg": "涨跌幅",
            "amount": "成交额",
            "volume": "成交量",
        }
        spot_df = spot_df.rename(columns=col_map)
        for col in ["最新价", "涨跌幅", "成交额"]:
            if col in spot_df.columns:
                spot_df[col] = pd.to_numeric(spot_df[col], errors="coerce")

        spot_df["代码"] = spot_df["代码"].astype(str)
        spot_df = spot_df[~spot_df["名称"].astype(str).str.contains("ST|退", na=False)]
        spot_df = spot_df[spot_df["代码"].str.startswith(("sh60", "sh68", "sz00", "sz30"))]
        spot_df = spot_df[spot_df["最新价"].notna() & (spot_df["成交额"] > 0)]
        return spot_df

    def get_daily_history(
        self, symbol: str, start_date: str = "20220101", force_refresh: bool = False
    ) -> pd.DataFrame:
        """获取个股前复权日线（优先读取本地缓存，防IP限流）"""
        sina_sym = self._to_sina_symbol(symbol)
        pure_code = sina_sym[2:]
        cache_path = os.path.join(self.cache_dir, f"{pure_code}_sina_daily.csv")

        if force_refresh or not os.path.exists(cache_path):
            try:
                time.sleep(0.45)
                df = ak.stock_zh_a_hist_sina(symbol=sina_sym, start_date=start_date, adjust="qfq")
                hist_map = {
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                }
                df = df.rename(columns=hist_map)
                if "amount" not in df.columns:
                    df["amount"] = df["close"] * df["volume"]
                df.to_csv(cache_path, index=False)
            except Exception as e:
                if not os.path.exists(cache_path):
                    print(f"⚠️ 拉取 {sina_sym} 历史数据失败: {e}")
                    return pd.DataFrame()

        df = pd.read_csv(cache_path)
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] >= pd.to_datetime(start_date)].sort_values("date").reset_index(drop=True)
        return df

    def get_hs300_benchmark(self, start_date: str = "20220101") -> pd.DataFrame:
        """获取沪深300指数历史行情作为回测基准"""
        cache_path = os.path.join(self.cache_dir, "sh000300_bench.csv")
        if not os.path.exists(cache_path):
            try:
                df = ak.stock_zh_index_daily(symbol="sh000300")
                df.to_csv(cache_path, index=False)
            except Exception:
                return pd.DataFrame()

        df = pd.read_csv(cache_path)
        df["date"] = pd.to_datetime(df["date"])
        df = df[df["date"] >= pd.to_datetime(start_date)].sort_values("date").reset_index(drop=True)
        return df
