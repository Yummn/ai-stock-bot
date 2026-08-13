AI Stock Bot 本地模拟盘

快速启动
1. 解压完整文件夹，不要只复制其中几个脚本。
2. 双击 run_web.bat。
3. 在浏览器打开 http://127.0.0.1:8765/。

手机访问
1. 让手机和电脑连接同一个 Wi-Fi。
2. 双击 run_web_lan.bat。
3. 在手机浏览器打开电脑显示的局域网地址。

几点说明
- 默认使用本地模拟账户，不会连接券商下单。
- 账户、持仓和运行记录保存在 data 目录。
- 发布包不包含个人 .env、API Key、数据库、隧道令牌和日志。
- 如果换电脑后原有 .venv 无法使用，请在项目目录重新创建环境：

  py -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  .venv\Scripts\python.exe -m pip install -e .

本项目只用于学习、回测和模拟交易，不构成投资建议。
