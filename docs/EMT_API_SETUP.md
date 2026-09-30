# EMT API test SDK setup

## Verified status (2026-09-30)

The personal EMT trading and EMQ quote API simulation test application was
submitted in the vendor portal. The issued ordinary simulation API account
successfully passed `scripts/emt_python_readonly_probe.py probe`: SDK 2.27.0.0
loaded, simulation login succeeded, and the asset query returned successfully.
Credentials were held only in memory for the check. The one-shot loopback
handoff (`scripts/emt_one_shot_check.py`) has exited, and its temporary SDK
logs were removed. This is **not** an order-placement test.

## Local web account view

The v4 web app now has a **券商** page with a separate EMT simulation account
section. From `http://127.0.0.1:<port>/#live`, enter the **API ordinary
simulation** account and password issued by the broker and press the read-only
query button. The page displays total assets, buying power, and positions.
The account and password are cleared after the request. The snapshot stays only in that
browser page's memory until refresh; no credentials or broker balances are
written to the app database. The route rejects requests from public tunnels
and non-loopback clients. Simulation data never populates the real-account
fields or enables orders, automatic orders, or leverage.

On 2026-09-30 the ordinary simulation API account was queried successfully
through this page: assets and paginated positions were returned, with no
order API calls.

The standalone `scripts/emt_snapshot_worker.py` uses only EMT login,
`queryAsset`, paginated `queryPositionByPage`, and logout. A short-lived
Python 3.10 process isolates the native SDK from the web server. SDK logs go
to a temporary directory that is removed after each query. If the broker
changes its simulation server address, update the reviewed simulation-only
constants in `src/ai_stock_bot/v4/broker_emt.py`; do not point this connector
at a live server.

## Python SDK (preferred for this project)

The official Python v2.27.0 archive is extracted to
`.tools/emt_api_python/emt_api_python/`. Its MD5 is
`ec40d2189aede70c315b0ed38cd61cb3`, matching the vendor's
[Python download page](https://emt.18.cn/down/other-language/1). The supplied
`vnemttrader.pyd` depends on `python310.dll`, so an isolated official Python
3.10.11 embeddable runtime is installed at `.tools/python310/`; it does not
change the web app's Python. The runtime download's MD5 is
`f1c0538b060e03cbb697ab3581cb73bc`, matching
[Python.org](https://www.python.org/downloads/release/python-31011/).

The vendor's `test/tradertest.py` includes order placement and fund transfers.
Do not run it. Use the separate read-only probe:

```powershell
& .\.tools\python310\python.exe scripts\emt_python_readonly_probe.py check
```

This confirms both the trader and quote extensions load, without a network
connection or credentials. Simulation connection instructions are below.

The probe does not submit orders, cancel orders, transfer funds, or print
account balances. The Python 3.10.11 runtime is isolated but old; use it only
for this vendor SDK until the broker provides a newer build.

## C++ SDK (alternative)

The official C++ v2.27.0 archive is extracted to `.tools/emt_api_cpp/emt_api/`.
Its MD5 is `24371781c4b2bc2d54567f260f7bcb91`, matching the vendor's
[download page](https://emt.18.cn/down). `.tools/` is local and git-ignored.

The vendor's `demo/trade_demo.cpp` calls `InsertOrder` and `CancelOrder` as soon
as it runs. Do not execute that sample with a trading account. The separate
`scripts/emt_readonly_probe.cpp` uses only SDK version, simulation API login,
asset query, and logout. It never submits or cancels an order and does not
display the account or balance.

### C++ local check

From the repository root, run the compiled executable:

```powershell
& .\.tools\emt_api_cpp\emt_api\lib\windows\x64\emt_readonly_probe.exe --version
```

This checks DLL loading without a network connection or credentials.

## Simulation connection check

The institutional terminal login is not proof of EMT API entitlement. The
[vendor's API test guide](https://emt.18.cn/article/article-info/87436563)
requires a separate API test application and the broker-provided API server IP,
port, and account. In a local PowerShell session, set these variables using the
values supplied for the **simulation API**, then run the Python read-only probe:

```powershell
$env:EMT_TEST_ENV = 'simulation'
$env:EMT_TEST_IP = '<broker-provided simulation API IP>'
$env:EMT_TEST_PORT = '<broker-provided simulation API port>'
$env:EMT_TEST_ACCOUNT = '<broker-provided API test account>'
& .\.tools\python310\python.exe scripts\emt_python_readonly_probe.py probe
```

If the broker requires an API password, set `EMT_TEST_PASSWORD` in this same
PowerShell session before running the probe. The program cannot independently
verify that an IP is a simulation server; use only the test endpoint supplied
by the broker. The alternative C++ probe can be run with
`.\.tools\emt_api_cpp\emt_api\lib\windows\x64\emt_readonly_probe.exe --probe`
after setting the same variables.

Do not paste passwords into chat, commit them, or put them in `.env`. Close the
PowerShell window after the check; the variables exist only for that session.
The broker's example IP and `user1`/`user2` placeholders are not valid account
configuration. A successful terminal login does not connect the website's live
trading page; live order execution remains disabled.
