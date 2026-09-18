# 桌面版安装与使用

桌面版是一个本机运行的 Windows 网页应用，默认只监听 `127.0.0.1:8765`。它不需要打开 Codex、配置 MCP 客户端或手动输入 JSON，下载的文件默认保存到项目根目录的 `downloads` 文件夹。

## 安装

1. 安装 Python 3.10 或更高版本，并在安装时勾选 **Add Python to PATH**。
2. 下载 GitHub 的 `desktop` 分支 ZIP 并解压，或使用 Git 获取桌面版：

   ```powershell
   git clone -b desktop https://github.com/wozhendemeiyou/social-image-mcp-crawler.git
   cd social-image-mcp-crawler
   ```

3. 双击 `安装桌面版.bat`。它会自动创建 `.venv`、安装依赖并生成 `.env`；也可以执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install_desktop.ps1
   ```

4. 只使用“其他平台”时可以直接启动。需要抖音、微博或小红书时，再运行 `powershell -ExecutionPolicy Bypass -File .\scripts\install_sources.ps1 -UseGit` 并按平台登录。

## 启动

双击 `启动应用.bat`。第一次启动也会自动检查运行环境；浏览器打开 `http://127.0.0.1:8765/` 后即可使用。关闭启动它的 PowerShell 窗口即可停止应用。

## 使用

- 直接填写关键词、昵称、账号或完整网址，程序自动识别入口。
- 选择抖音、微博、X，或选择“其他平台”并粘贴网页地址；其他平台会提取网页主图和图片链接。
- 图片模式不要求填写视频数量；只有视频或全部模式才填写视频数量。
- 点击开始采集后，图片会下载到 `downloads`，页面会显示采集数量、下载状态和缩略图。微博图片通过本地预览代理加载，避免浏览器跨域或 Referer 导致破图。

桌面版与 MCP 使用同一套采集、筛选、下载和断点逻辑，但桌面版日常使用不要求安装或注册 MCP 客户端。
