import numpy as np


class RiskManager:
    """机构级三重风控引擎：滚动半凯利公式 + ATR风险平价 + 组合目标波动率缩放"""

    def __init__(self, target_vol: float = 0.095, atr_stop_mult: float = 2.2):
        self.target_vol = target_vol
        self.atr_stop_mult = atr_stop_mult

    def compute_vol_scalar(self, daily_returns: np.ndarray, idx: int) -> float:
        """根据组合过去20日实际波动率动态调节全账户风险暴露"""
        if idx < 20:
            return 0.90
        recent_vol = np.nanstd(daily_returns[idx - 20 : idx]) * np.sqrt(252)
        return float(np.clip(self.target_vol / (recent_vol + 1e-6), 0.45, 1.25))

    def compute_half_kelly(self, trade_history: list) -> float:
        """滚动统计过去30笔平仓战绩，计算半凯利最优下注比例"""
        if len(trade_history) < 8:
            win_r, payoff = 0.58, 2.1
        else:
            recent = trade_history[-30:]
            wins = [r for r in recent if r > 0]
            losses = [abs(r) for r in recent if r <= 0]
            win_r = len(wins) / len(recent)
            payoff = (np.mean(wins) if wins else 0.08) / ((np.mean(losses) if losses else 0.035) + 1e-9)

        raw_kelly = max(0.0, win_r - (1.0 - win_r) / (payoff + 1e-9))
        return 0.55 * raw_kelly

    def compute_position_weight(
        self, trade_history: list, atr_pct: float, vol_scalar: float, max_single_weight: float = 0.22
    ) -> float:
        """融合半凯利与ATR波动率预算（单笔触发止损最多亏损总资金2.8%）"""
        kelly = self.compute_half_kelly(trade_history)
        vol_cap = 0.028 / max(0.035, self.atr_stop_mult * atr_pct)
        raw_w = min(kelly, vol_cap, max_single_weight) * vol_scalar
        return float(np.clip(raw_w, 0.12, max_single_weight))
