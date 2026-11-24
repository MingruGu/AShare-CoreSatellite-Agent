import os
import json


class PortfolioLedger:
    """持久化持仓与资金记忆账本 (JSON Storage)"""

    def __init__(self, path: str = "./data_cache/portfolio_ledger.json"):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.state = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "cash": 0.0,
            "positions": {},
            "trade_history": [],
            "pending_buy_plan": [],
        }

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def reset_capital(self, amount: float):
        self.state["cash"] = float(amount)
        self.state["positions"] = {}
        self.state["pending_buy_plan"] = []
        self.save()
