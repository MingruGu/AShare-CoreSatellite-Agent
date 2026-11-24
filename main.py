import sys
from core.backtester import run_backtest
from agent.trading_agent import QuantTradingAgent


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        run_backtest()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "cron":
        agent = QuantTradingAgent()
        print(agent.check_positions(send_wechat=True))
        print(agent.generate_buy_plan(send_wechat=True))
        return

    agent = QuantTradingAgent()
    print("=" * 72)
    print("🚀 AShare-CoreSatellite-Agent 量化交易智能体已启动")
    print("💡 支持指令：【我有100万现金，今天怎么买】 / 【确认买入】 / 【检查持仓】")
    print("💡 运行历史回测请执行: python main.py backtest")
    print("=" * 72)
    while True:
        try:
            cmd = input("\n你: ")
            if cmd.strip().lower() in ("exit", "quit", "退出"):
                break
            print(agent.chat(cmd))
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
