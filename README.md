# AI Stock Bot

> 项目分类：个人实验项目 / Python 自动交易工具 / 主要用于模拟盘与策略测试

这是一个可扩展的自动交易实验项目。它把 **行情获取、信号生成、风险控制、下单执行、状态保存和回测** 拆成相对独立的模块，既可以使用本地规则，也可以接入 AI 生成交易信号。

> 这不是投资建议。真实交易可能亏损；建议先使用本地模拟盘或券商仿真环境。

## 最快开始方式

如果只是想看看项目怎么运行，推荐直接使用本地模拟盘。

### 1. 安装

```powershell
git clone https://github.com/Yummn/ai-stock-bot.git
cd ai-stock-bot

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env
```

### 2. 运行一次模拟交易

```powershell
python -m ai_stock_bot --once
```

也可以直接运行：

```text
run_paper.bat
```

默认配置不会连接真实券商账户。

### 3. 打开网页面板

```powershell
.\run_web.bat
```

浏览器打开：

```text
http://127.0.0.1:8765
```

## 目前支持

### 行情

- Yahoo
- AKShare
- Tushare
- 多数据源备用链，例如 `akshare,yahoo`

### 信号

- 本地启发式策略
- Codex CLI
- OpenAI API
- 项目中的机器学习实验模块

### 下单 / 模拟

- 本地 Paper Trading
- Alpaca
- QMT / MiniQMT
- PTrade 适配

### 风控

- 单笔最大金额
- 单笔最大股数
- 单只股票最大仓位
- 当日最大亏损
- 止损
- 止盈

信号不会直接变成订单，而是先经过统一的风险控制。

## 本地与远程访问

本机网页面板：

```powershell
.\run_web.bat
```

同一局域网的手机访问：

```powershell
.\run_web_lan.bat
```

临时公网演示：

```powershell
.\run_web_public.bat
```

公网地址只适合演示；任何知道地址的人都可能访问页面，因此不要把它用于真实账户。

停止公开访问：

```powershell
.\stop_public_demo.bat
```

## 数据保存

本地模拟盘运行记录保存在 SQLite：

```text
data/paper_trading.sqlite3
```

保存内容包括：

- 账户状态
- 行情快照
- 信号
- 模拟委托
- 持仓
- 历史行情

关闭页面或重启服务后可以恢复最近一次模拟状态。

## 回测

网页面板支持回测与策略比较，包括：

- 保守 / 均衡 / 激进策略档位
- 本地规则 / AI 模式
- 随机 14 天
- 固定历史区间
- 快速日线与较慢时间粒度测试
- 资产曲线
- 收益率
- 交易次数
- 最大回撤
- 最终持仓

回测不会修改当前模拟账户。

## 项目结构

```text
src/ai_stock_bot/
├─ market_data.py   # 行情源
├─ ai.py            # 信号提供器
├─ risk.py          # 风险控制
├─ broker.py        # Paper / Alpaca / QMT / PTrade
├─ storage.py       # SQLite 持久化
├─ engine.py        # 主交易流程
└─ web.py           # Web 面板与回测
```

主流程可以简单理解为：

```text
行情
  ↓
策略 / AI 信号
  ↓
风险控制
  ↓
Broker
  ↓
保存状态 / 页面展示
```

## 常用配置

默认推荐先使用：

```env
MARKET_DATA=akshare,yahoo
BROKER=paper
DRY_RUN=true
SYMBOLS=510300.SH,510500.SH,159915.SZ
```

使用 Tushare：

```env
MARKET_DATA=tushare
TUSHARE_TOKEN=你的 tushare token
```

使用 OpenAI：

```env
AI_PROVIDER=openai
OPENAI_API_KEY=你的 key
OPENAI_MODEL=gpt-4.1-mini
```

使用 Codex CLI：

```env
AI_PROVIDER=codex
CODEX_BIN=path\to\codex.exe
CODEX_TIMEOUT_SECONDS=90
```

## QMT / MiniQMT

项目提供 QMT / MiniQMT Broker 适配。使用前需要：

- 已安装并登录 MiniQMT
- 券商账户已开通 QMT 权限
- Python 环境可导入 `xtquant`
- 配置 `QMT_PATH` 和账户信息

环境预检：

```powershell
.\run_qmt_preflight.bat
```

## PTrade

PTrade 一般运行在券商提供的策略环境中。项目提供模板：

[examples/ptrade_strategy_template.py](examples/ptrade_strategy_template.py)

## 使用真实券商前

如果要接入 QMT、PTrade 或 Alpaca，请先：

1. 使用 `BROKER=paper` 跑通本地模拟盘。
2. 优先使用券商仿真账户。
3. 将单笔金额和股数限制设置得足够小。
4. 先用 `--once` 单次运行。
5. 核对券商客户端实际委托、成交和持仓。

项目对真实下单保留了额外确认条件，但仍建议把它当作实验项目使用。
