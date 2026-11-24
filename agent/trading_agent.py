import os
import re
from datetime import datetime
import yaml
import numpy as np
import pandas as pd

from core.data_engine import SinaDataEngine
from core.risk_manager import RiskManager
from agent.ledger import PortfolioLedger
from agent.notifier import WeChatNotifier


class QuantTradingAgent:
    """具备长期持仓记忆、动态龙头海选、半凯利仓位计算与自动卖出预警的量化智能体"""

    def __init__(self, config_path: str = "config.yaml"):
        if not os.path.exists(config_path):
            config_path = "config.example.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

        st_cfg = self.cfg["strategy"]
        self.uni_cfg = self.cfg["universe"]
        self.data_engine = SinaDataEngine(cache_dir=self.cfg["storage"]["cache_dir"])
        self.risk_mgr = RiskManager(
            target_vol=st_cfg["target_volatility"],
            atr_stop_mult=st_cfg["atr_stop_multiplier"],
        )
        self.ledger = PortfolioLedger(path=self.cfg["storage"]["ledger_path"])
        self.notifier = WeChatNotifier(sendkey=self.cfg["notification"].get("serverchan_sendkey", ""))

    def scan_market_and_leaders(self):
        print("📡 正在通过新浪抗限流通道扫描 A 股全市场实时行情...")
        spot_df = self.data_engine.get_realtime_snapshot()

        spot_map = {}
        for _, r in spot_df.iterrows():
            p_code = r["代码"][2:] if len(r["代码"]) > 6 else r["代码"]
            spot_map[p_code] = {
                "name": r["名称"],
                "price": float(r["最新价"]),
                "pct_chg": float(r["涨跌幅"]),
                "amount": float(r["成交额"]),
            }

        top_liquid = spot_df.sort_values("成交额", ascending=False).head(80)
        up_ratio = float((top_liquid["涨跌幅"] > 0).mean())

        candidates = spot_df[
            (spot_df["成交额"] >= self.uni_cfg["min_daily_amount"])
            & (spot_df["涨跌幅"] >= self.uni_cfg["min_daily_chg"])
            & (spot_df["涨跌幅"] <= self.uni_cfg["max_daily_chg"])
        ].sort_values("成交额", ascending=False).head(20)

        defense_pool = self.uni_cfg["defense_pool"]
        qualified_leaders = []
        total_c = len(candidates)

        for idx, (_, row) in enumerate(candidates.iterrows(), 1):
            print(f"  ⏳ [{idx}/{total_c}] 正在精算 {row['名称']} ({row['代码']}) 历史趋势与ATR...", flush=True)
            sina_code = row["代码"]
            pure_code = sina_code[2:] if len(sina_code) > 6 else sina_code
            if pure_code in defense_pool:
                continue

            df = self.data_engine.get_daily_history(sina_code, start_date="20240601")
            if len(df) < 65:
                continue

            curr_price = float(row["最新价"])
            curr_amt = float(row["成交额"])
            close_60d_ago = float(df["close"].iloc[-60])
            close_20d_ago = float(df["close"].iloc[-20])
            ma60 = float(df["close"].iloc[-59:].mean())
            avg_amt_20 = float(df["amount"].iloc[-20:].mean())

            prev_c = df["close"].shift(1)
            tr = np.maximum(
                df["high"] - df["low"],
                np.maximum(abs(df["high"] - prev_c), abs(df["low"] - prev_c)),
            )
            atr14 = float(tr.rolling(14).mean().iloc[-1])
            if np.isnan(atr14) or atr14 <= 0:
                atr14 = curr_price * 0.03

            mom_60d = (curr_price / close_60d_ago - 1.0) * 100
            mom_20d = (curr_price / close_20d_ago - 1.0) * 100
            vol_ratio = curr_amt / (avg_amt_20 + 1e-9)

            if mom_60d < self.uni_cfg["min_mom_60d"] or mom_20d <= 0.0 or curr_price < ma60:
                continue

            qualified_leaders.append({
                "symbol": pure_code,
                "name": row["名称"],
                "price": curr_price,
                "pct_chg": float(row["涨跌幅"]),
                "mom_60d": mom_60d,
                "vol_ratio": vol_ratio,
                "amount_yi": curr_amt / 1e8,
                "atr14": atr14,
                "atr_pct": atr14 / curr_price,
            })

        if qualified_leaders:
            q_df = pd.DataFrame(qualified_leaders)
            s_mom = q_df["mom_60d"].rank(pct=True) * 40.0
            s_amt = q_df["amount_yi"].rank(pct=True) * 25.0
            s_pb = np.clip((-q_df["pct_chg"] + 0.5) / 4.5, 0, 1) * 20.0
            s_vol = np.clip(1.3 - q_df["vol_ratio"], 0, 1) * 15.0
            q_df["score"] = (s_mom + s_amt + s_pb + s_vol).round(1)
            qualified_leaders = q_df.sort_values("score", ascending=False).to_dict("records")

        active_defense = []
        for d_sym, d_name in defense_pool.items():
            if d_sym in spot_map:
                df_d = self.data_engine.get_daily_history(d_sym, start_date="20240601")
                curr_p = spot_map[d_sym]["price"]
                ma60_d = float(df_d["close"].iloc[-59:].mean()) if len(df_d) >= 60 else curr_p * 0.99
                if curr_p >= ma60_d * 0.99:
                    active_defense.append({"symbol": d_sym, "name": d_name, "price": curr_p})

        return spot_map, qualified_leaders, active_defense, up_ratio

    def generate_buy_plan(self, send_wechat: bool = False) -> str:
        spot_map, leaders, active_defense, up_ratio = self.scan_market_and_leaders()
        cash = self.ledger.state["cash"]
        positions = self.ledger.state["positions"]
        st_cfg = self.cfg["strategy"]

        pos_val = sum(
            p["shares"] * (spot_map[s]["price"] if s in spot_map else p["entry_price"])
            for s, p in positions.items()
        )
        total_eq = cash + pos_val
        if total_eq <= 0:
            return "⚠️ 账户当前总资金为 0。请先输入初始资金，例如：【我有100万现金，今天怎么买】"

        if up_ratio < 0.28:
            max_pos, regime = 1, "弱势防守期（核心资金偏弱，严格控仓至最多1只进攻股）"
        elif up_ratio < 0.50:
            max_pos, regime = 4, "震荡轮动期（精选强势主升回调真龙头）"
        else:
            max_pos, regime = st_cfg["max_offense_positions"], "多头活跃期（可积极配置优质回调龙头）"

        curr_off_cnt = sum(1 for p in positions.values() if p.get("type") == "offense")
        slots = max(0, max_pos - curr_off_cnt)
        candidates = [x for x in leaders if x["symbol"] not in positions][:slots]

        lines = [
            f"\n🚀 【AShare-CoreSatellite-Agent · 实战配置指令单】({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            f"• 账户总资产: ¥{total_eq:,.2f} | 可用现金: ¥{cash:,.2f} | 当前持仓: {len(positions)} 只",
            f"• 市场温度诊断: {regime} (主战场红盘率: {up_ratio:.1%})",
            "=" * 72,
            "【一、 卫星进攻引擎：全市场动态交易龙头买入建议】",
        ]

        plan_items = []
        rem_cash = cash
        for item in candidates:
            weight = self.risk_mgr.compute_position_weight(
                self.ledger.state["trade_history"], item["atr_pct"], vol_scalar=1.0
            )
            budget = min(rem_cash, total_eq * weight)
            lots = int(budget // (item["price"] * 100))
            shares = lots * 100
            if shares >= 100:
                cost = shares * item["price"]
                rem_cash -= cost
                hard_stop = round(item["price"] - st_cfg["atr_stop_multiplier"] * item["atr14"], 2)
                target_low = round(item["price"] * (1 + st_cfg["trailing_trigger_pct"]), 2)
                target_high = round(item["price"] * (1 + st_cfg["hard_take_profit_pct"]), 2)

                plan_items.append({
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "type": "offense",
                    "shares": shares,
                    "price": item["price"],
                    "atr14": item["atr14"],
                    "hard_stop": hard_stop,
                    "target_low": target_low,
                    "target_high": target_high,
                })
                lines.append(
                    f"🟢 建议买入: {item['name']} ({item['symbol']}) | 共振分: {item['score']} | 60日涨幅: +{item['mom_60d']:.1f}%\n"
                    f"   ▶ 凯利风控仓位: {weight:.1%} -> 买入 {shares} 股 ({lots}手)，预计动用 ¥{cost:,.2f}\n"
                    f"   ▶ 现价: ¥{item['price']:.2f} | 🛑 硬止损线: ¥{hard_stop:.2f} | 🎯 移动锁利区: ¥{target_low:.2f} ~ ¥{target_high:.2f}"
                )

        if not plan_items:
            lines.append("🟡 今日暂无满足【大成交额 + 中期主升 + 日内健康回调】的新增买点，或进攻仓位已满，切勿盲目追高。")

        lines.append("-" * 72)
        lines.append("【二、 核心防御压舱石：低波高股息红利配置建议】")
        curr_def_val = sum(
            p["shares"] * (spot_map[s]["price"] if s in spot_map else p["entry_price"])
            for s, p in positions.items()
            if p.get("type") == "defense"
        )
        max_def_budget = total_eq * st_cfg["max_defense_ratio"]
        if rem_cash > 40000 and curr_def_val < max_def_budget * 0.85 and active_defense:
            def_alloc = min(rem_cash * 0.5, max_def_budget - curr_def_val)
            pick_defs = [d for d in active_defense if d["symbol"] not in positions][:2]
            if pick_defs and def_alloc > 20000:
                per_budget = def_alloc / len(pick_defs)
                for d in pick_defs:
                    d_lots = int(per_budget // (d["price"] * 100))
                    d_shares = d_lots * 100
                    if d_shares >= 100:
                        d_cost = d_shares * d["price"]
                        rem_cash -= d_cost
                        plan_items.append({
                            "symbol": d["symbol"],
                            "name": d["name"],
                            "type": "defense",
                            "shares": d_shares,
                            "price": d["price"],
                            "atr14": d["price"] * 0.018,
                            "hard_stop": round(d["price"] * 0.93, 2),
                            "target_low": round(d["price"] * 1.08, 2),
                            "target_high": round(d["price"] * 1.20, 2),
                        })
                        lines.append(
                            f"🛡️ 防御压舱石: 买入 {d['name']} ({d['symbol']}) {d_shares} 股 ({d_lots}手)，动用 ¥{d_cost:,.2f} (现价 ¥{d['price']:.2f}，止损 ¥{d['price']*0.93:.2f})"
                        )

        lines.append(f"💰 建议保留无风险现金/货币基金缓冲垫: ¥{rem_cash:,.2f} (占总资产 {rem_cash/total_eq:.1%})")
        lines.append("=" * 72)
        lines.append("💡 提示: 实盘成交后请输入【确认买入】写入持久化记忆账本。")

        self.ledger.state["pending_buy_plan"] = plan_items
        self.ledger.save()

        report_text = "\n".join(lines)
        if send_wechat:
            self.notifier.send("🟢【量化智能体】今日龙头选股与仓位配置方案", report_text)
        return report_text

    def confirm_buy(self) -> str:
        plan = self.ledger.state.get("pending_buy_plan", [])
        if not plan:
            return "📭 当前没有待确认的买入方案。"
        today_str = datetime.now().strftime("%Y-%m-%d")
        bought = []
        for item in plan:
            cost = item["shares"] * item["price"] * 1.0003
            if self.ledger.state["cash"] >= cost:
                self.ledger.state["cash"] -= cost
                self.ledger.state["positions"][item["symbol"]] = {
                    "name": item["name"],
                    "type": item["type"],
                    "shares": item["shares"],
                    "entry_price": item["price"],
                    "entry_atr": item["atr14"],
                    "highest_price": item["price"],
                    "entry_date": today_str,
                    "hard_stop": item["hard_stop"],
                    "target_low": item["target_low"],
                    "target_high": item["target_high"],
                }
                bought.append(f"{item['name']}({item['shares']}股 @ ¥{item['price']:.2f})")
        self.ledger.state["pending_buy_plan"] = []
        self.ledger.save()
        return "✅ 已成功写入持仓账本：\n" + "\n".join([f"  • {x}" for x in bought]) + f"\n💵 剩余现金: ¥{self.ledger.state['cash']:,.2f}"

    def check_positions(self, send_wechat: bool = False) -> str:
        positions = self.ledger.state["positions"]
        if not positions:
            return "📭 当前持仓账本为空。"

        spot_df = self.data_engine.get_realtime_snapshot()
        spot_map = {
            (r["代码"][2:] if len(r["代码"]) > 6 else r["代码"]): float(r["最新价"])
            for _, r in spot_df.iterrows()
        }

        today_str = datetime.now().strftime("%Y-%m-%d")
        st_cfg = self.cfg["strategy"]
        lines = [f"\n🔍 【持仓止盈止损监控雷达】({datetime.now().strftime('%Y-%m-%d %H:%M')})", "=" * 72]
        urgent = False

        for sym, pos in list(positions.items()):
            curr_p = spot_map.get(sym, pos["entry_price"])
            pos["highest_price"] = max(pos["highest_price"], curr_p)
            pnl_pct = curr_p / pos["entry_price"] - 1.0
            peak_pct = pos["highest_price"] / pos["entry_price"] - 1.0
            pnl_money = (curr_p - pos["entry_price"]) * pos["shares"]

            if pos["entry_date"] == today_str:
                msg = "🔒 【T+1 锁仓中】今日买入，明日开盘后可卖出"
            elif peak_pct >= st_cfg["hard_take_profit_pct"] or curr_p >= pos["target_high"]:
                urgent = True
                msg = "🚨 【立即止盈卖出！】已达目标收益上限，落袋为安！"
            elif peak_pct >= st_cfg["trailing_trigger_pct"]:
                trail = max(pos["entry_price"] * 1.028, pos["highest_price"] - st_cfg["trailing_atr_multiplier"] * pos["entry_atr"])
                if curr_p < trail:
                    urgent = True
                    msg = f"🚨 【移动追踪止盈卖出！】跌破动态保护线 (¥{trail:.2f})，锁定波段利润！"
                else:
                    msg = f"🔥 【盈利保护区】移动止盈线已上移至 ¥{trail:.2f}，跌破前继续持有"
            elif curr_p < pos["hard_stop"]:
                urgent = True
                msg = f"🛑 【触发硬止损卖出！】跌破风控止损线 (¥{pos['hard_stop']:.2f})，无条件砍仓！"
            else:
                msg = "🟢 【继续持有】未触及止盈/止损线"

            lines.append(
                f"• {pos['name']} ({sym}) | {pos['shares']}股 | 成本 ¥{pos['entry_price']:.2f} -> 现价 ¥{curr_p:.2f} ({pnl_pct:+.2%}, ¥{pnl_money:+,.2f})\n"
                f"  止损线: ¥{pos['hard_stop']:.2f} | 状态: {msg}"
            )

        self.ledger.save()
        report_text = "\n".join(lines)
        if send_wechat:
            title = "🚨【紧急卖出预警】持仓触发止盈/止损线！" if urgent else "📊【每日持仓复盘】持仓安全，继续持有"
            self.notifier.send(title, report_text)
        return report_text

    def record_sell(self, symbol: str, sell_price: float) -> str:
        positions = self.ledger.state["positions"]
        if symbol not in positions:
            return f"⚠️ 账本中未找到代码 {symbol} 的持仓记录。"
        pos = positions.pop(symbol)
        proceeds = pos["shares"] * sell_price * 0.9992
        self.ledger.state["cash"] += proceeds
        net_ret = (sell_price * 0.9992) / (pos["entry_price"] * 1.0003) - 1.0
        self.ledger.state["trade_history"].append(net_ret)
        self.ledger.save()
        return f"✅ 已记录卖出 {pos['name']}({symbol}) @ ¥{sell_price:.2f}，净收益 {net_ret:+.2%}，可用现金 ¥{self.ledger.state['cash']:,.2f}"

    def chat(self, user_text: str) -> str:
        text = user_text.strip()
        m_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
        m_num = re.search(r"(\d{5,})", text)
        if any(k in text for k in ("现金", "资金", "本金", "初始化")) and (m_wan or m_num):
            amt = float(m_wan.group(1)) * 10000 if m_wan else float(m_num.group(1))
            self.ledger.reset_capital(amt)
            print(f"✅ 已初始化本金 ¥{amt:,.2f}，正在海选全市场龙头（首次联网缓存约需10秒）...", flush=True)
            return self.generate_buy_plan(send_wechat=False)
        if any(k in text for k in ("确认买入", "确认下单", "已买入", "成交")):
            return self.confirm_buy()
        m_sell = re.search(r"卖出\s*(\d{6})\s*(\d+(?:\.\d+)?)", text)
        if m_sell:
            return self.record_sell(m_sell.group(1), float(m_sell.group(2)))
        if any(k in text for k in ("持仓", "检查", "监控", "该卖", "止盈", "止损")):
            return self.check_positions(send_wechat=False)
        if any(k in text for k in ("买哪个", "怎么买", "选股", "方案", "推荐", "龙头")):
            return self.generate_buy_plan(send_wechat=False)

        return (
            "🤖 指令菜单：\n"
            "  1. 我有100万现金，今天怎么买\n"
            "  2. 确认买入\n"
            "  3. 检查持仓 / 该卖了吗\n"
            "  4. 已卖出 300413 33.5"
        )
