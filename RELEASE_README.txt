AI 模拟炒股面板发布版

使用方法：
1. 解压整个文件夹，不要只解压其中某几个文件。
2. 双击 run_web.bat。
3. 浏览器打开 http://127.0.0.1:8765/。

手机同一个 Wi-Fi 访问：
1. 双击 run_web_lan.bat。
2. 在手机浏览器打开电脑的局域网 IP，例如 http://192.168.0.103:8765。

说明：
- 这是本地模拟炒股面板，默认不会真实下单。
- data 目录首次运行会自动生成本地模拟账户数据。
- 发布包不包含个人 .env、Cloudflare token、隧道日志和旧备份压缩包。
- 如果换电脑后 .venv 无法运行，请在项目目录重新安装依赖：
  py -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
