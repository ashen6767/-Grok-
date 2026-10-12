# 老七的 Grok 视频助手

当前版本：**正式版 1.2.35**

这是一个面向 Windows 的 Grok Imagine 批量提交工具，由 PySide6 桌面控制台和 Chrome/Edge 浏览器助手组成。桌面端负责图片、参数、队列和状态管理；浏览器助手只在用户已经登录的 `grok.com` 页面中执行上传与提交。

> 本项目是非官方工具，与 xAI 或 Grok 没有关联。请遵守相关服务条款，并仅处理你有权使用的内容。

## 主要功能

- 批量添加图片或文件夹，并按文件名自然排序。
- 为整批任务或单个任务设置图片用途、分辨率、时长、比例、音频和提示词。
- 自动连接 Chrome/Edge 中已经登录的 Grok Imagine 页面。
- 显示等待、上传、设置、提交、失败等任务状态，并支持失败重试。
- 支持浅色、深色和跟随 Windows 主题。
- 浏览器助手随桌面版本释放和更新，并具有单实例、端口独占及版本降级保护。
- 桌面软件退出时集中释放定时器、子窗口和本机通信服务，减少后台残留。

## 使用顺序

1. 运行桌面软件。
2. 点击右上角“安装 / 检查浏览器助手”，按照向导在 Chrome 或 Edge 中加载扩展目录。
3. 在浏览器中登录 Grok，并打开 Grok Imagine 页面。
4. 在桌面软件中拖入图片或选择文件夹。
5. 设置默认参数；选中任务后可以应用单独设置。
6. 点击“开始批量提交”。

自动运行期间不要手动上传、提交、删除或刷新目标 Grok 标签页。遇到验证码、登录失效或页面结构变化时，软件会停止自动化并报告错误，不会尝试绕过安全验证。

## 从源码运行

建议使用 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install PySide6 Pillow pillow-heif pyinstaller
python app.py
```

运行测试：

```powershell
python -m unittest -v test_app.py test_core.py
```

构建 Windows 单文件程序：

```powershell
pyinstaller --noconfirm --clean GrokBatch.spec
```

## 目录结构

```text
app.py             桌面界面与应用入口
core.py            任务、默认参数与持久化
server.py          桌面端本机通信服务
extension/         Chrome/Edge Manifest V3 浏览器助手
assets/            应用图标、品牌图片与安装教程
test_app.py        界面、扩展和打包测试
test_core.py       队列与通信服务测试
GrokBatch.spec     PyInstaller 构建配置
```

浏览器助手已经包含在桌面软件中。桌面软件关闭后，助手会进入休眠并解除调试连接；再次打开软件后会自动恢复连接。
