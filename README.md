# AI Stock Bot

这是我用 Python 写的一个本地模拟交易项目。最初只是想把“拉行情—算指标—生成信号—风控—记录结果”这一套流程跑通，后来陆续加上了网页面板、历史回测、机器学习模型和几个券商接口适配。

项目默认使用模拟账户，适合拿来观察不同策略在同一段行情里的表现。它不是荐股软件，也不保证任何收益；如果只是想看看效果，请一直保持 `BROKER=paper` 和 `DRY_RUN=true`。

## 现在能做什么

- 从 AKShare、Yahoo 或 Tushare 获取行情，支持按顺序自动切换备用数据源。
- 使用本地规则、Codex、OpenAI API 或机器学习模型生成买入、卖出和观望信号。
- 内置随机森林、HistGradientBoosting、XGBoost、MLP 和 1D-CNN，可在网页中切换训练方式和参数。
- 提供保守、均衡、激进三档规则，并限制每日买入数量。
- 对所有信号统一执行仓位、单笔金额、单笔股数、当日亏损、止盈和止损检查。
- 运行本地模拟盘，保存账户、持仓、委托、信号和行情快照。
- 在网页中查看资产曲线、交易记录，并运行随机 14 天或固定历史区间回测。
- 预留 Alpaca、QMT / MiniQMT 和 PTrade 适配；真实报单需要显式关闭模拟模式并填写确认字符串。

## 我是怎么组织它的

一次完整运行大致会经过下面几步：

```text
行情源
  ↓
指标与行情快照
  ↓
规则 / 机器学习 / 大模型信号
  ↓
硬性风控过滤
  ↓
模拟账户或券商适配器
  ↓
SQLite 记录与网页展示
```

我把 AI 信号和下单逻辑分开了。即使模型给出“买入”，订单仍要通过代码里的风控检查；模型输出格式不对、数据不足或调用失败时，也会回退到本地规则，而不是直接报单。

主要代码在 `src/ai_stock_bot`：

| 文件 | 作用 |
| --- | --- |
| `market_data.py` | 行情获取、K 线整理和指标计算 |
| `ai.py` | 本地规则、机器学习模型和大模型信号 |
| `risk.py` | 仓位、亏损、止盈止损等硬性限制 |
| `broker.py` | 模拟盘及券商接口适配 |
| `storage.py` | SQLite 状态和交易记录 |
| `engine.py` | 命令行单次运行流程 |
| `web.py` | 网页面板、回测和参数管理 |

## 环境要求

- Python 3.10 及以上
- 当前主要在 Windows 上开发和测试
- 机器学习模型依赖 scikit-learn、XGBoost 和 PyTorch
- QMT 功能需要本机已安装并登录 MiniQMT，且 Python 能导入 `xtquant`

`requirements.txt` 默认安装 CUDA 12.8 版本的 PyTorch。如果机器没有对应的 NVIDIA 环境，可以按 [PyTorch 官方安装说明](https://pytorch.org/get-started/locally/) 换成 CPU 或其他 CUDA 版本。

## 快速开始

```powershell
git clone https://github.com/Yummn/ai-stock-bot.git
cd ai-stock-bot

py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
Copy-Item .env.example .env
```

第一次建议不要改交易相关配置，直接启动网页：

```powershell
.\run_web.bat
```

然后打开 <http://127.0.0.1:8765>。

也可以只运行一轮模拟交易：

```powershell
.\run_paper.bat
```

默认配置会优先尝试 AKShare，失败后再使用 Yahoo。模拟账户数据保存在 `data/paper_trading.sqlite3`，关闭网页或重启电脑后仍会保留。

## 信号模式

### 本地规则

不需要密钥，按趋势、5 日/20 日动量、RSI、波动率等指标打分。信号不够强时会保持观望，适合先检查完整流程是否能正常运行。

### Codex

使用本机已登录的 Codex 生成结构化信号，不需要额外填写 OpenAI API Key：

```env
AI_PROVIDER=codex
CODEX_BIN=path\to\codex.exe
CODEX_MODEL=
CODEX_TIMEOUT_SECONDS=90
```

程序只把当前行情快照交给模型，并要求返回固定 JSON。返回结果还会经过趋势、动量和波动条件的二次过滤。

### OpenAI API

```env
AI_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1-mini
```

密钥只应写在本地 `.env` 中。仓库已忽略 `.env`、数据库和常见密钥文件，不要把真实密钥提交到 Git。

### 机器学习

网页面板可以选择随机森林、HistGradientBoosting、XGBoost、MLP 或 1D-CNN，并设置训练范围、样本下限和部分超参数。样本不足、训练报错或 GPU 不可用时，页面会显示原因，并按配置回退到 CPU 或本地规则。

这里的模型主要用于比较和实验，不代表模型越复杂，回测结果就一定越好。

## 回测

网页提供两种常用方式：

- **随机 14 天**：从可用历史行情中抽取一段交易日，适合快速观察策略行为。
- **固定历史区间**：使用相同区间比较不同策略和模型，结果更方便横向对照。

回测会展示起止资产、收益率、交易次数、最大回撤、资产曲线、最终持仓和最近交易。回测账户与当前模拟账户分开，不会改动现有持仓。

需要注意：分钟线的可用历史通常比日线短；大模型逐个时间点生成信号也很慢，所以 AI 回测会限制部分高成本组合。

## 常用配置

复制 `.env.example` 后，通常只需要关注这些项目：

```env
# 行情源按顺序尝试
MARKET_DATA=akshare,yahoo

# 本地模拟盘
BROKER=paper
DRY_RUN=true

# 观察标的
SYMBOLS=510300.SH,510500.SH,159915.SZ

# 初始资金与风控
STARTING_CASH=100000
MAX_TRADE_CASH=10000
MAX_ORDER_SHARES=2500
MAX_POSITION_PCT=0.50
MAX_DAILY_LOSS_PCT=0.015
STOP_LOSS_PCT=0.20
TAKE_PROFIT_PCT=0.50
```

`SYMBOLS` 支持 `600519`、`600519.SH`、`600519.SS`、`sh600519` 等常见写法。示例默认以宽基和行业 ETF 为主，主要是为了降低调试时单只股票波动带来的干扰。

## 手机和临时公网访问

同一 Wi-Fi 下可以运行：

```powershell
.\run_web_lan.bat
```

再用手机打开电脑的局域网地址，例如 `http://192.168.0.103:8765`。如果无法访问，通常需要在 Windows 防火墙中允许 Python 使用专用网络。

`run_web_public.bat` 会通过 Cloudflare 临时隧道生成公网地址，只适合演示模拟盘。任何拿到地址的人都可以操作页面，不要在连接真实账户时使用它。停止脚本：

```powershell
.\stop_public_demo.bat
```

## 券商接口

### Alpaca

填写 `ALPACA_API_KEY`、`ALPACA_SECRET_KEY` 和 `ALPACA_PAPER`。建议先使用 Alpaca Paper，确认代码、时区和订单类型都符合预期。

### QMT / MiniQMT

```env
MARKET_DATA=akshare
BROKER=qmt
QMT_PATH=path\to\userdata_mini
QMT_ACCOUNT_ID=your-account-id
QMT_ACCOUNT_TYPE=STOCK
QMT_SESSION_ID=123456
```

先运行 `run_qmt_preflight.bat` 检查路径、登录状态和 `xtquant` 环境。QMT 账户是实盘还是仿真盘，由 MiniQMT 当前登录账户决定。

### PTrade

PTrade 通常在券商提供的策略环境中运行。仓库里的 [PTrade 示例](examples/ptrade_strategy_template.py) 展示了适配方式，具体可用 API 仍要以所在券商版本为准。

## 目前的限制

- 项目主要在个人 Windows 环境中验证，跨平台脚本还不完整。
- 不同数据源对代码格式、复权方式和分钟线历史范围的支持并不完全一致。
- 回测暂未完整模拟滑点、手续费、停牌、涨跌停和成交量约束。
- 大模型信号受模型版本、提示词和调用延迟影响，结果并非完全可重复。
- 券商适配代码不等于已经通过真实资金环境验证。

这些问题也是后续准备继续整理的部分。

## 风险说明

本项目仅用于编程、策略研究和模拟交易，不构成投资建议。历史回测和模拟盘结果不能代表未来表现。若自行启用真实报单，请先阅读代码、使用仿真账户测试，并从最小金额开始核对委托、成交、持仓和撤单行为；由此产生的交易风险需要使用者自行承担。
