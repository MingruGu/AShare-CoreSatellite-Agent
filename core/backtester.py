import os
import yaml
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.data_engine import SinaDataEngine
from core.alpha_factors import compute_alpha_factors
from core.risk_manager import RiskManager

plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def run_backtest(config_path: str = "config.yaml"):
    if not os.path.exists(config_path):
        config_path = "config.example.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    st_cfg = cfg["strategy"]
    uni_cfg = cfg["universe"]
    engine = SinaDataEngine(cache_dir=cfg["storage"]["cache_dir"])
    risk_mgr = RiskManager(
        target_vol=st_cfg["target_volatility"],
        atr_stop_mult=st_cfg["atr_stop_multiplier"],
    )

    print(">>> 1/3 正在加载历史数据与本地缓存...")
    raw_offense = {}
    for code, name in uni_cfg["backtest_offense_pool"].items():
        df = engine.get_daily_history(code, start_date=st_cfg["start_date"])
        if not df.empty:
            df["symbol"] = code
            df["name"] = name
            raw_offense[code] = df

    raw_defense = {}
    for code, name in uni_cfg["defense_pool"].items():
        df = engine.get_daily_history(code, start_date=st_cfg["start_date"])
        if not df.empty:
            df["symbol"] = code
            df["name"] = name
            raw_defense[code] = df

    # 计算基准20日动量
    all_mom = pd.concat([
        df[["date"]].assign(m20=df["close"] / df["close"].shift(20) - 1.0)
        for df in raw_offense.values()
    ])
    bench_ret_20d = all_mom.groupby("date")["m20"].mean()

    print(">>> 2/3 正在计算相对强弱(RS)与多因子共振信号...")
    offense_dict = {c: compute_alpha_factors(df, bench_ret_20d) for c, df in raw_offense.items()}
    defense_dict = {c: compute_alpha_factors(df, bench_ret_20d) for c, df in raw_defense.items()}

    all_symbols = {**offense_dict, **defense_dict}
    all_dates = sorted(list(set.union(*[set(df["date"]) for df in all_symbols.values()])))

    aligned = {}
    for sym, df in all_symbols.items():
        temp = pd.DataFrame({"date": all_dates}).merge(df, on="date", how="left")
        temp["close"] = temp["close"].ffill().bfill()
        temp["open"] = temp["open"].fillna(temp["close"])
        temp["high"] = temp["high"].fillna(temp["close"])
        temp["low"] = temp["low"].fillna(temp["close"])
        temp["alpha_score"] = temp["alpha_score"].fillna(0.0)
        temp["above_ma60"] = temp["above_ma60"].fillna(0)
        temp["atr14"] = temp["atr14"].ffill().bfill()
        temp["atr_pct"] = temp["atr_pct"].ffill().bfill()
        temp["cannot_buy"] = temp["cannot_buy"].fillna(True)
        temp["cannot_sell"] = temp["cannot_sell"].fillna(True)
        aligned[sym] = temp

    print(">>> 3/3 正在运行严格 T+1 / 涨跌停限制组合撮合回测...")
    n_days = len(all_dates)
    cash = 1.0
    positions = {}
    defense_shares = {sym: 0.0 for sym in defense_dict}
    equity_curve = np.ones(n_days)
    daily_rets = np.zeros(n_days)
    trade_history = []
    fee_rate = st_cfg["fee_rate"]
    rf_daily = st_cfg["rf_annual_rate"] / 252.0

    for i in range(1, n_days):
        cash *= (1.0 + rf_daily)
        vol_scalar = risk_mgr.compute_vol_scalar(daily_rets, i)

        breadth = np.mean([aligned[s].loc[i - 1, "above_ma60"] for s in offense_dict])
        if breadth < 0.30:
            dynamic_max_pos, score_gate = 1, 58.0
        elif breadth < 0.50:
            dynamic_max_pos, score_gate = 4, 32.0
        else:
            dynamic_max_pos, score_gate = st_cfg["max_offense_positions"], 28.0

        # 持仓管理
        to_sell = []
        for sym, pos in positions.items():
            row = aligned[sym].loc[i]
            pos["hold_days"] += 1
            pos["highest_price"] = max(pos["highest_price"], row["high"])

            if pos["hold_days"] < 1 or row["cannot_sell"]:
                continue

            peak_ret = pos["highest_price"] / pos["entry_price"] - 1.0
            if peak_ret >= st_cfg["hard_take_profit_pct"]:
                sell_p = min(row["high"], max(row["open"], pos["entry_price"] * (1 + st_cfg["hard_take_profit_pct"])))
                to_sell.append((sym, sell_p))
            elif peak_ret >= st_cfg["trailing_trigger_pct"]:
                trail_stop = max(
                    pos["entry_price"] * 1.028,
                    pos["highest_price"] - st_cfg["trailing_atr_multiplier"] * pos["entry_atr"],
                )
                if row["close"] < trail_stop:
                    to_sell.append((sym, row["close"]))
            else:
                hard_stop = pos["entry_price"] - st_cfg["atr_stop_multiplier"] * pos["entry_atr"]
                if row["close"] < hard_stop:
                    to_sell.append((sym, row["close"]))
                elif pos["hold_days"] >= 18:
                    to_sell.append((sym, row["open"]))

        for sym, sell_price in to_sell:
            pos = positions.pop(sym)
            cash += pos["shares"] * sell_price * (1 - fee_rate)
            trade_history.append((sell_price * (1 - fee_rate)) / (pos["entry_price"] * (1 + fee_rate)) - 1.0)

        # 扫描开仓
        candidates = []
        if len(positions) < dynamic_max_pos:
            for sym in offense_dict:
                if sym not in positions:
                    prev_r = aligned[sym].loc[i - 1]
                    curr_r = aligned[sym].loc[i]
                    if prev_r["alpha_score"] > score_gate and not curr_r["cannot_buy"]:
                        candidates.append((prev_r["alpha_score"], sym))
            candidates.sort(reverse=True, key=lambda x: x[0])

        for _, sym in candidates:
            if len(positions) >= dynamic_max_pos:
                break
            prev_r = aligned[sym].loc[i - 1]
            curr_r = aligned[sym].loc[i]

            off_val = sum(p["shares"] * aligned[s].loc[i, "open"] for s, p in positions.items())
            def_val = sum(defense_shares[s] * aligned[s].loc[i, "open"] for s in defense_dict)
            total_eq = cash + off_val + def_val

            weight = risk_mgr.compute_position_weight(trade_history, prev_r["atr_pct"], vol_scalar)
            need_cash = total_eq * weight

            if cash < need_cash and def_val > 0:
                shortfall = need_cash - cash
                sell_ratio = min(1.0, shortfall / (def_val * (1 - fee_rate)))
                for d_sym in defense_dict:
                    if defense_shares[d_sym] > 0 and not aligned[d_sym].loc[i, "cannot_sell"]:
                        sell_sh = defense_shares[d_sym] * sell_ratio
                        cash += sell_sh * aligned[d_sym].loc[i, "open"] * (1 - fee_rate)
                        defense_shares[d_sym] -= sell_sh

            alloc_cash = min(cash, need_cash)
            if alloc_cash > 0.02 * total_eq:
                buy_p = curr_r["open"]
                cash -= alloc_cash
                positions[sym] = {
                    "shares": (alloc_cash * (1 - fee_rate)) / buy_p,
                    "entry_price": buy_p,
                    "entry_atr": prev_r["atr14"],
                    "hold_days": 0,
                    "highest_price": curr_r["high"],
                }

        # 防御压舱石配置
        off_val_now = sum(p["shares"] * aligned[s].loc[i, "open"] for s, p in positions.items())
        def_val_now = sum(defense_shares[s] * aligned[s].loc[i, "open"] for s in defense_dict)
        total_eq_now = cash + off_val_now + def_val_now

        active_defs = [
            s for s in defense_dict
            if aligned[s].loc[i - 1, "above_ma60"] == 1 and not aligned[s].loc[i, "cannot_buy"]
        ]
        max_def_budget = total_eq_now * st_cfg["max_defense_ratio"] * vol_scalar
        if def_val_now < max_def_budget * 0.85 and cash > 0.08 * total_eq_now and active_defs:
            add_def = min(cash * 0.6, max_def_budget - def_val_now)
            if add_def > 0.02 * total_eq_now:
                per_def = add_def / len(active_defs)
                for d_sym in active_defs:
                    p_open = aligned[d_sym].loc[i, "open"]
                    defense_shares[d_sym] += (per_def * (1 - fee_rate)) / p_open
                    cash -= per_def
        elif (not active_defs or def_val_now > max_def_budget * 1.15) and def_val_now > 0:
            reduce_ratio = 1.0 if not active_defs else (def_val_now - max_def_budget) / def_val_now
            for d_sym in defense_dict:
                if defense_shares[d_sym] > 0 and not aligned[d_sym].loc[i, "cannot_sell"]:
                    sell_sh = defense_shares[d_sym] * reduce_ratio
                    cash += sell_sh * aligned[d_sym].loc[i, "open"] * (1 - fee_rate)
                    defense_shares[d_sym] -= sell_sh

        close_off = sum(p["shares"] * aligned[s].loc[i, "close"] for s, p in positions.items())
        close_def = sum(defense_shares[s] * aligned[s].loc[i, "close"] for s in defense_dict)
        equity_curve[i] = cash + close_off + close_def
        daily_rets[i] = equity_curve[i] / (equity_curve[i - 1] + 1e-9) - 1.0

    # 绩效统计
    eq_s = pd.Series(equity_curve)
    total_ret = equity_curve[-1] - 1.0
    ann_ret = (1 + total_ret) ** (252 / n_days) - 1.0
    excess_daily = daily_rets[1:] - rf_daily
    sharpe = float(np.sqrt(252) * np.nanmean(excess_daily) / (np.nanstd(excess_daily) + 1e-9))
    max_dd = float((eq_s / eq_s.cummax() - 1.0).min())
    calmar = ann_ret / (abs(max_dd) + 1e-9)
    win_rate = np.mean([t > 0 for t in trade_history]) if trade_history else 0.0

    print("\n================ V10.0 量化系统回测绩效报告 ================")
    print(f"累计净收益 : {total_ret:.2%}")
    print(f"年化收益率 : {ann_ret:.2%}")
    print(f"夏普比率   : {sharpe:.2f}")
    print(f"卡玛比率   : {calmar:.2f}")
    print(f"最大回撤   : {max_dd:.2%}")
    print(f"进攻胜率   : {win_rate:.1%} (总交易 {len(trade_history)} 笔)")
    print("============================================================")

    # 绘制对比沪深300基准图
    os.makedirs("backtest_results", exist_ok=True)
    out_path = "backtest_results/v10_vs_hs300.png"
    plt.figure(figsize=(12, 5.5))
    plt.plot(
        all_dates,
        equity_curve,
        color="#d62728",
        linewidth=2.2,
        label=f"V10.0 核心卫星双引擎 (累计:{total_ret:.2%}, 年化:{ann_ret:.2%}, 夏普:{sharpe:.2f}, 回撤:{max_dd:.2%})",
    )

    hs300_df = engine.get_hs300_benchmark(start_date=st_cfg["start_date"])
    if not hs300_df.empty:
        bench_aligned = pd.DataFrame({"date": all_dates}).merge(hs300_df[["date", "close"]], on="date", how="left")
        bench_aligned["close"] = bench_aligned["close"].ffill().bfill()
        bench_curve = bench_aligned["close"] / bench_aligned["close"].iloc[0]
        plt.plot(
            all_dates,
            bench_curve,
            color="#7f7f7f",
            linestyle="--",
            linewidth=1.5,
            alpha=0.8,
            label="沪深300指数基准 (CSI 300)",
        )

    plt.axhline(1.0, color="black", linestyle=":", alpha=0.4)
    plt.title("AShare-CoreSatellite-Agent (V10.0) vs 沪深300基准净值曲线")
    plt.ylabel("组合净值")
    plt.legend(loc="upper left")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f"📊 回测对比图已保存至: {out_path}")


if __name__ == "__main__":
    run_backtest()
