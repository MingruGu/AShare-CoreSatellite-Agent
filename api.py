from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

from agent.trading_agent import QuantTradingAgent

app = FastAPI(title="AShare-CoreSatellite-Agent API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = QuantTradingAgent()


class CapitalRequest(BaseModel):
    amount: float


class ChatRequest(BaseModel):
    command: str


class SellRequest(BaseModel):
    code: str
    price: float


def _ensure_equity_history():
    state = agent.ledger.state
    cash = float(state.get("cash", 0.0))
    positions = state.get("positions", {})
    pos_val = sum(
        float(p.get("shares", 0)) * float(p.get("current_price", p.get("cost_price", p.get("entry_price", 0))))
        for p in positions.values()
    )
    total_equity = round(cash + pos_val, 2)
    today_str = datetime.now().strftime("%Y-%m-%d")

    history = state.get("equity_history", [])
    initial_cap = float(state.get("initial_capital", total_equity if total_equity > 0 else 100000.0))
    if "initial_capital" not in state and total_equity > 0:
        state["initial_capital"] = total_equity

    if not history and total_equity > 0:
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        history = [
            {"date": yesterday_str, "equity": initial_cap, "return_pct": 0.0},
            {"date": today_str, "equity": total_equity, "return_pct": round((total_equity / initial_cap - 1) * 100, 2)},
        ]
    elif history:
        ret_pct = round((total_equity / initial_cap - 1) * 100, 2) if initial_cap > 0 else 0.0
        if history[-1]["date"] == today_str:
            history[-1]["equity"] = total_equity
            history[-1]["return_pct"] = ret_pct
        else:
            history.append({"date": today_str, "equity": total_equity, "return_pct": ret_pct})

    state["equity_history"] = history
    agent.ledger.save()
    return total_equity, pos_val, initial_cap, history


@app.get("/")
def serve_app():
    return FileResponse("frontend/index.html")


@app.get("/api/state")
def get_ledger_state(period: str = "ALL"):
    agent.ledger.state = agent.ledger._load()
    total_equity, pos_val, initial_cap, history = _ensure_equity_history()
    state = agent.ledger.state

    days_map = {"1W": 7, "1M": 30, "1Q": 90, "1Y": 365}
    if period in days_map and len(history) > 1:
        cutoff = (datetime.now() - timedelta(days=days_map[period])).strftime("%Y-%m-%d")
        filtered = [h for h in history if h["date"] >= cutoff]
        if len(filtered) < 2:
            filtered = history[-2:]
    else:
        filtered = history

    if len(filtered) >= 2 and filtered[0]["equity"] > 0:
        period_return_pct = round((filtered[-1]["equity"] / filtered[0]["equity"] - 1) * 100, 2)
        period_pnl = round(filtered[-1]["equity"] - filtered[0]["equity"], 2)
    else:
        period_return_pct = 0.0
        period_pnl = 0.0

    trades = state.get("trade_history", [])
    wins = [t for t in trades if float(t.get("pnl_pct", 0)) > 0]
    win_rate = round(len(wins) / len(trades) * 100, 1) if trades else 0.0

    return {
        "cash": round(float(state.get("cash", 0.0)), 2),
        "position_value": round(pos_val, 2),
        "total_equity": total_equity,
        "initial_capital": initial_cap,
        "period_return_pct": period_return_pct,
        "period_pnl": period_pnl,
        "win_rate": win_rate,
        "positions": state.get("positions", {}),
        "pending_buy_plan": state.get("pending_buy_plan", []),
        "trade_history": trades,
        "equity_curve": filtered,
    }


@app.post("/api/capital")
def set_capital(req: CapitalRequest):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="资金金额必须大于 0")
    agent.ledger.reset_capital(req.amount)
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    agent.ledger.state["initial_capital"] = float(req.amount)
    agent.ledger.state["equity_history"] = [
        {"date": yesterday_str, "equity": float(req.amount), "return_pct": 0.0},
        {"date": today_str, "equity": float(req.amount), "return_pct": 0.0},
    ]
    agent.ledger.save()
    return {"message": f"资金已重置为 {req.amount:,.2f} 元"}


@app.get("/api/signals")
def get_buy_signals(push_wechat: bool = False):
    try:
        report = agent.generate_buy_plan(send_wechat=push_wechat)
        agent.ledger.state = agent.ledger._load()
        return {"report": report, "pending_buy_plan": agent.ledger.state.get("pending_buy_plan", [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/check")
def check_positions(push_wechat: bool = False):
    try:
        report = agent.check_positions(send_wechat=push_wechat)
        agent.ledger.state = agent.ledger._load()
        _ensure_equity_history()
        return {"report": report, "positions": agent.ledger.state.get("positions", {})}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/confirm-buy")
def confirm_buy():
    reply = agent.chat("确认买入")
    agent.ledger.state = agent.ledger._load()
    _ensure_equity_history()
    return {"reply": reply}


@app.post("/api/sell")
def record_sell(req: SellRequest):
    reply = agent.chat(f"已卖出 {req.code} {req.price}")
    agent.ledger.state = agent.ledger._load()
    _ensure_equity_history()
    return {"reply": reply}


@app.post("/api/chat")
def chat_with_agent(req: ChatRequest):
    reply = agent.chat(req.command)
    agent.ledger.state = agent.ledger._load()
    _ensure_equity_history()
    return {"reply": reply}


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)