# AI Stock Bot

这是一个可扩展的 AI 自动交易骨架，当前支持：

- 行情：Yahoo、AKShare、Tushare；支持逗号分隔的备用链，例如 `akshare,yahoo`
- 下单：本地模拟盘、Alpaca、QMT / MiniQMT、PTrade 策略环境
- AI：Codex 登录态、本地启发式策略或 OpenAI API
- 风控：单笔金额、单笔股数、单股仓位、当日最大亏损、止损、止盈

这不是投资建议。真实交易可能亏损；实盘前请先用本地模拟盘、券商仿真盘和最小金额验证。

## 安装

```powershell
cd path\to\ai-stock-bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env
```

## 先跑 A 股本地模拟盘

`.env` 默认已经是：

```env
MARKET_DATA=akshare,yahoo
BROKER=paper
DRY_RUN=true
SYMBOLS=510300.SH,510500.SH,159915.SZ
MAX_TRADE_CASH=3000
MAX_ORDER_SHARES=500
```

运行：

```powershell
python -m ai_stock_bot --once
```

也可以直接双击：

```text
run_paper.bat
```

代码会优先用 AKShare 拉 A 股行情；如果当前网络访问 AKShare 底层数据源失败，会自动用 Yahoo 兜底，然后在本地模拟账户里执行风控后的买卖，不会连接券商。

## 本地网页面板

启动：

```powershell
.\run_web.bat
```

然后打开：

```text
http://127.0.0.1:8765
```

网页面板只使用本地模拟盘，不会真实下单。

手机访问：

```powershell
.\run_web_lan.bat
```

手机和电脑连接同一个 WiFi 后，在手机浏览器打开电脑的局域网地址，例如：

```text
http://192.168.0.103:8765
```

如果打不开，通常是 Windows 防火墙拦截了 Python/Codex 的局域网访问，需要允许专用网络访问。

数据网络或异地公开演示：

```powershell
.\run_web_public.bat
```

脚本会自动启动本地网页服务，并用 Cloudflare 临时隧道生成一个 `https://*.trycloudflare.com` 公网地址。手机使用数据网络、别人异地访问时，直接打开这个公网地址即可。

这个公开地址只适合演示。窗口关闭后公网地址会失效；重新运行脚本会生成新的地址。任何知道该地址的人都能打开页面并点击模拟盘按钮，所以不要把它用于真实账户。

停止公开访问：

```powershell
.\stop_public_demo.bat
```

网页面板支持后台自动运行：

- 点击“开始自动”后，服务端会按设定秒数循环运行模拟交易
- 即使不一直盯着页面，只要 `run_web.bat` 的服务还在运行，自动循环就会继续
- 点击“停止自动”会停止循环
- 当前使用 Codex 登录态生成信号，一轮通常需要十几秒，所以自动间隔不要设太短；界面最低限制为 30 秒

运行记录会保存到本地 SQLite 数据库：

```text
data/paper_trading.sqlite3
```

保存内容包括账户、AI 信号、模拟委托、持仓、行情快照和走势图历史数据。关闭网页不会丢；关闭后端服务或重启电脑后，再启动服务会恢复最近一次模拟状态。

网页面板支持两种回测模式：

- 选择“策略档位”：保守、均衡、激进
- 选择“运行模式”：本地或 AI
- 选择“回测模式”：随机14天或固定过去一年
- 选择“时间粒度”：快速测试按日线一天测一次；慢速测试按“自动间隔 秒”的实际粒度测，例如默认 1800 秒就是 30 分钟一次
- 点击“开始回测”会按所选组合模拟
- 随机14天：从历史行情里随机抽取一段交易日模拟，所以每次结果不一定一样
- 固定过去一年：固定使用最近约 252 个交易日，适合公平对比保守、均衡、激进三档策略
- 慢速测试会尝试拉分钟级行情；数据源通常只提供最近一段分钟线，所以固定一年慢速可能只能覆盖数据源实际可用的区间，页面会显示提示
- 当运行模式是 AI 且 `.env` 里是 `AI_PROVIDER=codex` 时，回测会逐日调用 Codex 生成买入、卖出或观望信号，所以会比本地规则慢
- 建议：固定过去一年和慢速测试主要用本地模式对比；AI 固定一年已禁用，AI 慢速会自动限制最多 3 个时间点，避免长时间调用 Codex
- 回测结果不会改变当前模拟账户
- 页面会显示起始资产、结束资产、收益率、交易次数、策略档位、运行模式、回测模式、兜底次数、测试区间、资产曲线、最终持仓和最近交易

策略档位：

- 保守：当前默认策略；少交易、弱信号观望、每天最多买一个标的、已有持仓不重复加仓
- 均衡：适度放宽趋势和动量条件，允许每天最多两个买入信号
- 激进：更重视短线动量和突破，允许更频繁交易和加仓，波动会更大

## 使用 Tushare 行情

先注册 Tushare Pro 并获取 token，然后改 `.env`：

```env
MARKET_DATA=tushare
TUSHARE_TOKEN=你的 tushare token
SYMBOLS=510300.SH,510500.SH,159915.SZ
```

运行：

```powershell
python -m ai_stock_bot --once
```

## 使用 OpenAI 生成信号

```env
AI_PROVIDER=openai
OPENAI_API_KEY=你的 key
OPENAI_MODEL=gpt-4.1-mini
```

没有配置 OpenAI 时，默认使用本地启发式策略。

## 使用 Codex 登录态生成信号

不需要 OpenAI API key。先确认 `.env`：

```env
AI_PROVIDER=codex
CODEX_BIN=path\to\codex.exe
CODEX_TIMEOUT_SECONDS=90
```

网页点击“运行一次”时，会调用本机已登录的 Codex CLI 生成买入、卖出或观望信号。Codex 启动一次会比本地启发式策略慢，通常需要十几秒到几十秒。

## 接入 QMT / MiniQMT 实盘或仿真

准备条件：

- 已安装并登录 MiniQMT
- 券商账户已开通 QMT 权限
- Python 环境能 import `xtquant`
- `QMT_PATH` 指向 MiniQMT 的 `userdata_mini` 目录

示例 `.env`：

```env
MARKET_DATA=akshare
BROKER=qmt
DRY_RUN=false
LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK

QMT_PATH=C:\你的MiniQMT目录\userdata_mini
QMT_ACCOUNT_ID=你的资金账号
QMT_ACCOUNT_TYPE=STOCK
QMT_SESSION_ID=123456

SYMBOLS=510300.SH,510500.SH,159915.SZ
ORDER_TYPE=limit
MAX_TRADE_CASH=3000
MAX_ORDER_SHARES=500
MAX_POSITION_PCT=0.20
STOP_LOSS_PCT=0.04
TAKE_PROFIT_PCT=0.08
```

运行：

```powershell
python -m ai_stock_bot --once
```

注意：QMT 账户是实盘还是仿真，由你登录的 MiniQMT 账户决定。代码层面只要 `BROKER=qmt` 就会调用 QMT 报单接口，所以必须显式设置 `DRY_RUN=false` 和确认字符串。

QMT 环境预检：

```powershell
.\run_qmt_preflight.bat
```

## 接入 PTrade

PTrade 通常不是普通命令行 API，而是在券商 PTrade 平台内运行策略。项目提供了模板：

[examples/ptrade_strategy_template.py](examples/ptrade_strategy_template.py)

思路：

1. 让 PTrade 的 Python 环境能 import `ai_stock_bot`
2. 新建 PTrade 策略，复制模板内容
3. `.env` 设置：

```env
MARKET_DATA=akshare
BROKER=ptrade
DRY_RUN=false
LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_RISK
SYMBOLS=510300.SH,510500.SH,159915.SZ
ORDER_TYPE=market
MAX_TRADE_CASH=3000
MAX_ORDER_SHARES=500
```

如果你的 PTrade 环境不允许访问外部包或 `.env`，需要把配置写死到策略环境里，或把行情和 AI 信号先在外部生成，再由 PTrade 读取信号文件执行。

## 配置说明

- `MARKET_DATA`: `yahoo`、`akshare`、`tushare`，也可以写成 `akshare,yahoo`
- `BROKER`: `paper`、`alpaca`、`qmt`、`ptrade`
- `SYMBOLS`: 股票代码，支持 `600519`、`600519.SH`、`600519.SS`、`sh600519`
- `MAX_TRADE_CASH`: 单笔买入最大金额
- `MAX_ORDER_SHARES`: 单笔最大股数，买入和卖出都限制
- `MAX_POSITION_PCT`: 单只股票最大仓位占总资产比例
- `MAX_DAILY_LOSS_PCT`: 当日最大亏损比例，触发后停止新开仓
- `STOP_LOSS_PCT`: 持仓浮亏达到该比例时卖出
- `TAKE_PROFIT_PCT`: 持仓浮盈达到该比例时卖出
- `ORDER_TYPE`: `limit` 或 `market`
- `LIVE_TRADING_ACK`: 实盘或券商报单必须设置为 `I_UNDERSTAND_REAL_MONEY_RISK`

## 真实下单前检查

1. 先 `BROKER=paper` 跑通至少几天
2. QMT/PTrade 先用仿真账户
3. 把 `MAX_TRADE_CASH` 和 `MAX_ORDER_SHARES` 设到很小
4. 用 `--once` 单次运行，不要一开始就循环运行
5. 核对券商客户端里的委托、成交、持仓和撤单行为
