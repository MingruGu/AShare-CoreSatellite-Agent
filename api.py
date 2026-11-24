from fastapi.responses import FileResponse
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from agent.trading_agent import QuantTradingAgent

app = FastAPI(
    title="AShare-CoreSatellite-Agent API",
    description="A股核心-卫星量化交易智能体移动端后端接口",
    version="1.0.0",
)

# 允许移动端 App / PWA 前端跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局复用单个 Agent 实例（共享内存缓存与本地 JSON 账本）
agent = QuantTradingAgent()


class CapitalRequest(BaseModel):
    amount: float


class ChatRequest(BaseModel):
    command: str


class SellRequest(BaseModel):
    code: str
    price: float


@app.get("/api/health")
def health_check():
    """健康检查接口"""
    return {"status": "ok", "service": "AShare-CoreSatellite-Agent"}


@app.get("/api/state")
def get_ledger_state():
    """获取当前账本状态：可用现金、持仓明细、待确认买单、历史交割记录"""
    agent.ledger.state = agent.ledger._load()
    state = agent.ledger.state
    return {
        "cash": state.get("cash", 0.0),
        "positions": state.get("positions", {}),
        "pending_buy_plan": state.get("pending_buy_plan", []),
        "trade_history": state.get("trade_history", []),
    }


@app.post("/api/capital")
def set_capital(req: CapitalRequest):
    """重置/初始化账户总资金"""
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="资金金额必须大于 0")
    agent.ledger.reset_capital(req.amount)
    return {"message": f"资金已初始化为 {req.amount:,.2f} 元", "state": agent.ledger.state}


@app.get("/api/signals")
def get_buy_signals(push_wechat: bool = False):
    """扫描全市场实时行情，生成今日核心-卫星买入方案"""
    try:
        report = agent.generate_buy_plan(send_wechat=push_wechat)
        agent.ledger.state = agent.ledger._load()
        return {
            "report": report,
            "pending_buy_plan": agent.ledger.state.get("pending_buy_plan", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/check")
def check_positions(push_wechat: bool = False):
    """扫描当前持仓实时盈亏与止盈/止损触发雷达"""
    try:
        report = agent.check_positions(send_wechat=push_wechat)
        agent.ledger.state = agent.ledger._load()
        return {
            "report": report,
            "positions": agent.ledger.state.get("positions", {}),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/confirm-buy")
def confirm_buy():
    """一键确认执行当前待买入方案并记入账本"""
    reply = agent.chat("确认买入")
    agent.ledger.state = agent.ledger._load()
    return {"reply": reply, "state": agent.ledger.state}


@app.post("/api/sell")
def record_sell(req: SellRequest):
    """记录单只股票卖出成交价，更新凯利胜率统计"""
    reply = agent.chat(f"已卖出 {req.code} {req.price}")
    agent.ledger.state = agent.ledger._load()
    return {"reply": reply, "state": agent.ledger.state}


@app.post("/api/chat")
def chat_with_agent(req: ChatRequest):
    """通用自然语言指令入口"""
    reply = agent.chat(req.command)
    agent.ledger.state = agent.ledger._load()
    return {"reply": reply, "state": agent.ledger.state}



@app.get("/")
def serve_app():
    return FileResponse("frontend/index.html")

if __name__ == "__main__":
    # 绑定 0.0.0.0 允许同一 Wi-Fi 下的 iPhone/iPad 直接访问
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
