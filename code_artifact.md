# 📐 系统量化模型与核心数学原理 (Quant Math & Models)

> 本文档系统梳理了 **AShare-CoreSatellite-Agent** 在市场择时、多因子选股、资金管理（仓位控制）、动态止盈止损以及回测评估中所采用的底层数学公式与量化金融模型。

---

## 1. 核心-卫星资产配置框架 (Core-Satellite Allocation)

系统将总资金 $V_{\text{total}}$ 划分为**低波红利防守底盘（Core）**与**高弹性动量进攻端（Satellite）**，同时保留动态现金缓冲垫 $C_{\text{buffer}}$：

$$V_{\text{total}} = V_{\text{core}} + V_{\text{satellite}} + C_{\text{buffer}}$$

* **核心仓（Core）**：配置高股息、低 Beta 宽基或红利资产，提供稳定的股息率 $D_y$ 与组合下行保护，降低整体组合的年化波动率 $\sigma_p$。
* **卫星仓（Satellite）**：在高流动性股票池中捕捉短期非对称赔率机会，通过严格的仓位上限与止损边界获取超额收益（Alpha）。

---

## 2. 市场广度择时门控 (Market Breadth Regime Gate)

为避免在系统性下跌或情绪冰点期盲目开仓，系统引入全市场红盘率 $B_t$ 作为宏观环境过滤器：

$$B_t = \frac{N_{\text{up}, t}}{N_{\text{total}, t}} \times 100\%$$

其中：
* $N_{\text{up}, t}$ 为 $t$ 日全市场上涨家数（$\text{PctChange}_t > 0$）；
* $N_{\text{total}, t}$ 为 $t$ 日有效交易标的總数（剔除停牌股）。

**门控规则**：
* 当 $B_t < B_{\text{min}}$（如 $35\%$）时，判定为空头弱势区间，**强制关闭进攻买入通道**；
* 当 $B_t \in [B_{\text{min}}, B_{\text{max}}]$ 时，允许卫星仓按信号入场；
* 当 $B_t > B_{\text{max}}$（情绪极度过热）时，收紧新开仓阈值，防范冲高回落风险。

---

## 3. 趋势纯度与量价异动因子 (Alpha Factors)

### 3.1 考夫曼效率系数 (Kaufman Efficiency Ratio, ER)
用于衡量价格在 $N$ 个交易日内“有效趋势位移”与“累计震荡路径”的比值，过滤无序宽幅震荡的噪音标的：

$$\text{ER}_N = \frac{\left| P_t - P_{t-N} \right|}{\sum_{i=0}^{N-1} \left| P_{t-i} - P_{t-i-1} \right|}$$

* **值域**：$\text{ER}_N \in [0, 1]$。
* **量化含义**：$\text{ER}_N$ 越接近 $1$，说明价格沿单边方向推进的效率越高、毛刺越少；越接近 $0$，说明价格在原地反复拉锯。系统仅保留趋势纯度达标的候选标的。

### 3.2 成交量标准化得分 (Volume Z-Score)
用于识别主升趋势中的“缩量健康回踩”或量能异动特征，消除不同市值股票绝对成交量量纲的差异：

$$Z_{\text{vol}, t} = \frac{\text{Vol}_t - \mu_{\text{vol}, M}}{\sigma_{\text{vol}, M}}$$

其中：
* $\text{Vol}_t$ 为当日成交量；
* $\mu_{\text{vol}, M}$ 与 $\sigma_{\text{vol}, M}$ 分别为过去 $M$ 个交易日（如 $M=20$）成交量的滚动均值与滚动样本标准差：

$$\mu_{\text{vol}, M} = \frac{1}{M}\sum_{k=0}^{M-1} \text{Vol}_{t-k}, \qquad \sigma_{\text{vol}, M} = \sqrt{\frac{1}{M-1}\sum_{k=0}^{M-1} \left(\text{Vol}_{t-k} - \mu_{\text{vol}, M}\right)^2}$$

* **量化含义**：当标的处于中期多头排列且 $Z_{\text{vol}, t} \le -1.0$ 时，表明抛压枯竭、筹码锁定良好，属于典型的缩量回踩买点。

---

## 4. 动态资金管理与风险定损 (Position Sizing & Risk)

### 4.1 真实波幅均值 (Average True Range, ATR)
衡量单只标的的真实日内及跳空波动水平，用于替代固定百分比止损，适应不同股票的股性差异：

$$\text{TR}_t = \max\left( H_t - L_t,\ \left| H_t - C_{t-1} \right|,\ \left| L_t - C_{t-1} \right| \right)$$

$$\text{ATR}_{14} = \frac{1}{14} \sum_{k=0}^{13} \text{TR}_{t-k}$$

其中 $H_t, L_t, C_{t-1}$ 分别为当日最高价、当日最低价与前一日收盘价。

### 4.2 半凯利仓位公式 (Half-Kelly Criterion)
结合历史交割账本的真实胜率 $p$ 与平均盈亏比 $b$，在追求长期几何复利最大化的同时，引入 $0.5$ 衰减系数抑制估计误差带来的净值剧烈波动：

$$f^* = \frac{1}{2} \left( p - \frac{1 - p}{b} \right) = \frac{1}{2} \left( \frac{p \cdot b - (1 - p)}{b} \right)$$

其中：
* $p = \frac{N_{\text{win}}}{N_{\text{total}}}$ 为策略实盘滚动胜率；
* $b = \frac{\overline{R}_{\text{win}}}{\left|\overline{R}_{\text{loss}}\right|}$ 为平均盈利幅度与平均亏损幅度之比。

**最终头寸约束（Risk-Parity & Cap Constraint）**：
单只标的实际分配资金 $W_i$ 同时受凯利上限、单票最大仓位上限 $W_{\max}$ 以及单笔最大风险敞口（如总净值的 $1\% \sim 1.5\%$）三重约束，并按 A 股最小交易单位（$100$ 股 / $1$ 手）向下取整：

$$Q_i = \left\lfloor \frac{\min\left( V_{\text{total}} \cdot f^*,\ V_{\text{total}} \cdot W_{\max},\ \frac{V_{\text{total}} \cdot r_{\text{risk}}}{2.2 \times \text{ATR}_{14}} \right)}{100 \cdot P_{\text{entry}}} \right\rfloor \times 100$$

---

## 5. 三段式动态止盈止损机制 (Dynamic Exit & Trailing Stop)

为解决“小赚就跑、大亏死扛”或“浮盈大幅回吐”的问题，系统为每笔持仓建立状态机，设入场成本价为 $P_0$，入场后历史最高价为 $P_{\text{high}}$，当前浮盈率为 $R_t = \frac{P_t - P_0}{P_0}$：

1. **阶段一：宽幅防洗盘硬止损 ($R_t < +7.5\%$)**
   $$P_{\text{stop}} = P_0 - 2.2 \times \text{ATR}_{14}$$
   给予主力正常洗盘波动空间，一旦收盘或盘中有效跌破 $P_{\text{stop}}$，无条件触发止损离场。

2. **阶段二：激活保本与移动追踪锁利 ($+7.5\% \le R_t < +24\%$)**
   当浮盈达到 $+7.5\%$ 后，止损线上移至成本保护区之上，并随新高动态抬升：
   $$P_{\text{trail}} = \max\left( P_0 \times 1.028,\ P_{\text{high}} - 1.3 \times \text{ATR}_{14} \right)$$
   保证该笔交易至少锁定 $+2.8\%$ 的保底正收益，同时让利润随主升浪继续奔跑。

3. **阶段三：目标上限止盈 ($R_t \ge +24\%$)**
   当波段收益触及 $+24\%$ 目标上限时，主动兑现超额利润，避免后期流动性退潮带来的回撤。

---

## 6. 实盘撮合摩擦与绩效评估指标 (Execution & Metrics)

### 6.1 A 股实盘摩擦成本建模
在回测与影子账户推演中，严格计入双边交易成本与制度约束：
* **交易费用**：买入佣金 $c_{\text{buy}}$，卖出佣金 $c_{\text{sell}}$ + 印花税 $\tau_{\text{stamp}}$，以及双向滑点损耗 $\delta_{\text{slip}}$；
* **制度约束**：严格执行 **$T+1$ 交收制度**（当日买入不可当日卖出）以及主板/双创板**涨跌停板撮合限制**（涨停封板无法买入、跌停封板无法卖出）。

### 6.2 核心策略评估指标
* **年化复合收益率 (CAGR)**：
  $$\text{CAGR} = \left( \frac{V_{\text{end}}}{V_{\text{start}}} \right)^{\frac{252}{T_{\text{days}}}} - 1$$
* **最大回撤 (Maximum Drawdown, MDD)**：
  $$\text{MDD} = \max_{t \in [0, T]} \left( \frac{\max_{\tau \in [0, t]} V_\tau - V_t}{\max_{\tau \in [0, t]} V_\tau} \right)$$
* **夏普比率 (Sharpe Ratio)**（以无风险利率 $R_f$ 为基准）：
  $$\text{Sharpe} = \frac{\mathbb{E}[R_p - R_f]}{\sigma_p} \times \sqrt{252}$$
* **卡玛比率 (Calmar Ratio)**：
  $$\text{Calmar} = \frac{\text{CAGR}}{\text{MDD}}$$