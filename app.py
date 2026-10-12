from __future__ import annotations

import json
import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QParallelAnimationGroup, Property,
    QPropertyAnimation, QRect, QRectF, QSize, QTimer, Qt, QUrl, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont, QIcon, QLinearGradient,
    QFontMetrics, QImageReader, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame,
    QGraphicsBlurEffect, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QScrollBar, QSizePolicy, QSlider, QTextEdit, QVBoxLayout, QWidget, QDialog,
)

from core import APP_DIR, Defaults, TaskStore
from server import Bridge, BridgePortInUseError


ACCENT = "#347FDC"
GOLD = "#D8A83E"
BG = "#F3F7FC"
PANEL = "#FBFDFF"
SUBTLE = "#EDF4FA"
TEXT = "#16365F"
MUTED = "#6F849D"
APP_NAME = "老七的Grok视频助手"
APP_VERSION = "1.2.35"
DISPLAY_VERSION = f"正式版{APP_VERSION}"
SINGLE_INSTANCE_MUTEX_NAME = "Local\\GrokBatchAssistant.Desktop.SingleInstance"


THEMES = {
    "light": {
        "window": "#F3F7FC", "panel": "#FBFDFF", "panel_alt": "#EDF4FA",
        "surface": "#F7FAFD", "surface_hover": "#E8F3FD", "text": "#16365F",
        "muted": "#6F849D", "border": "#DCE7F1", "border_strong": "#AFC7E1",
        "accent": "#347FDC", "accent_hover": "#428DE5", "track": "#D7E3F0",
        "card": "#FBFDFF", "card_hover": "#E7F2FC", "selected": "#EAF4FE",
        "active": "#EEF7FF", "failed": "#FFF8F8", "input": "#F7FAFD",
    },
    "dark": {
        "window": "#0D1621", "panel": "#131F2B", "panel_alt": "#101B26",
        "surface": "#182737", "surface_hover": "#223B55", "text": "#E8F1FA",
        "muted": "#91A7BB", "border": "#2B4053", "border_strong": "#45647E",
        "accent": "#4C9AFF", "accent_hover": "#65A9FF", "track": "#2A3C4E",
        "card": "#172431", "card_hover": "#203449", "selected": "#183A5A",
        "active": "#16344E", "failed": "#3A2029", "input": "#101B26",
    },
}


def system_theme_mode() -> str:
    """Read the Windows application color preference, falling back safely to light."""
    if sys.platform == "win32":
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if int(value) else "dark"
        except (OSError, TypeError, ValueError):
            pass
    return "light"


def resolved_theme_mode(preference: str) -> str:
    return system_theme_mode() if preference == "system" else preference if preference in THEMES else "light"


def theme_color(name: str, mode: str | None = None) -> str:
    return THEMES[mode or application_theme_mode()].get(name, THEMES["light"].get(name, "#000000"))


WINDOW_TITLEBAR_THEMES = {
    "light": {"caption": "#EDF5FC", "text": TEXT, "border": "#C7DAEB", "dark_mode": False},
    "dark": {"caption": "#101923", "text": "#EDF5FC", "border": "#27405A", "dark_mode": True},
}


def application_theme_mode(widget: QWidget | None = None) -> str:
    """Return the software theme, independent from the Windows system theme."""
    app = QApplication.instance()
    if app is not None:
        for property_name in ("themeMode", "colorTheme"):
            explicit = str(app.property(property_name) or "").lower()
            if explicit in WINDOW_TITLEBAR_THEMES:
                return explicit
        preference = str(app.property("themePreference") or "light").lower()
        return resolved_theme_mode(preference)
    return "light"


def apply_windows_titlebar_theme(target, theme: str = "light"):
    """Apply a native DWM caption without replacing Windows' title-bar controls."""
    if sys.platform != "win32":
        return
    mode = application_theme_mode(target if isinstance(target, QWidget) else None) if theme == "auto" else theme
    colors = WINDOW_TITLEBAR_THEMES.get(mode, WINDOW_TITLEBAR_THEMES["light"])
    try:
        if isinstance(target, ctypes.c_void_p):
            hwnd = target
        elif isinstance(target, QWidget):
            hwnd = ctypes.c_void_p(int(target.winId()))
        else:
            hwnd = ctypes.c_void_p(int(target))

        def colorref(value: str) -> int:
            color = QColor(value)
            return color.red() | (color.green() << 8) | (color.blue() << 16)

        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        immersive_mode = ctypes.c_int(1 if colors["dark_mode"] else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new and legacy)
            dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(immersive_mode), ctypes.sizeof(immersive_mode))
        for attribute, color in (
            (34, colors["border"]),   # DWMWA_BORDER_COLOR
            (35, colors["caption"]),  # DWMWA_CAPTION_COLOR
            (36, colors["text"]),     # DWMWA_TEXT_COLOR
        ):
            value = ctypes.c_uint(colorref(color))
            dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError, TypeError, ValueError):
        # Older Windows versions simply keep their system-managed title bar.
        pass

CHANGE_LOG_HTML = """
<h2>正式版本</h2>
<h3>正式版 1.2.35</h3>
<ul><li>退出流程改为集中清理：停止界面定时器和自动化、保存状态、关闭子窗口并释放本机通信服务。</li><li>主窗口关闭、Qt 应用退出及事件循环结束均设置清理兜底，单个步骤异常不再阻断后续资源释放。</li><li>新增轻量退出日志，便于区分 PyInstaller 正常双进程与真正的后台残留。</li></ul>
<h3>正式版 1.2.34</h3>
<ul><li>桌面软件新增 Windows 单实例锁，阻止不同版本同时运行并争用浏览器助手。</li><li>本机通信端口改为独占绑定；检测到旧版仍在运行时明确提示，不再让多个进程同时返回不同版本。</li><li>浏览器助手只允许向更高版本自动重载，发现旧版桌面端时保持黄色警告但绝不降级。</li><li>网页端发现版本异常会立即唤醒后台执行版本协调，不再只依赖可能休眠的 MV3 定时器。</li></ul>
<h3>正式版 1.2.33</h3>
<ul><li>任务卡片加入标准桌面列表选择方式：普通左键单选，Ctrl+左键切换单项，Shift+左键连续范围选择。</li><li>支持 Ctrl+Shift+左键将连续范围追加到已有选择，并用稳定的任务 ID 保存范围起点。</li><li>点击列表空白处可清空选择；双击预览、全选、任务刷新及删除后的选择状态同步完成适配。</li></ul>
<h3>正式版 1.2.32</h3>
<ul><li>任务列表移除每行复选框和勾选图形，释放更多横向空间。</li><li>保留原有多选能力，单击任务卡片即可选中或取消；左侧蓝色竖线成为唯一选中标记。</li><li>处理中与失败任务不再占用选中竖线，继续通过卡片底色和状态徽章区分；全选及全部批量操作保持不变。</li></ul>
<h3>正式版 1.2.31</h3>
<ul><li>修复上一任务提交后 Grok 页面仍在切换到作品页时，下一任务过早点击 Imagine 而被后续跳转覆盖的问题。</li><li>进入 Imagine 改为两阶段恢复：首次导航失败会重新定位侧栏，并使用真实鼠标事件再次进入视频制作页。</li><li>成功任务后的快速调度增加 0.4 秒页面稳定窗口，仍允许下一张提前上传和设置，不恢复旧版的长时间空转。</li></ul>
<h3>正式版 1.2.30</h3>
<ul><li>恢复浏览器助手自动更新：桌面端释放新版文件后，旧版扩展会执行一次受控重载。</li><li>每个目标版本最多自动重载一次；若 Chrome 仍未加载新版则停止重试并保持黄色提示，避免循环重载导致扩展被停用。</li><li>升级成功后自动清除重载记录并刷新已打开的 Grok 页面，使新内容脚本立即生效；从旧机制升级到本版需最后手动重新加载一次。</li></ul>
<h3>正式版 1.2.29</h3>
<ul><li>提交间隔改为约束相邻两次点击发送的最短时间，下一张图片可在间隔期间提前上传和设置。</li><li>移除成功提交后重复进入 Imagine 的流程，任务确认成功后立即领取下一项。</li><li>成功任务之间的队列调度等待由 1.2 秒缩短为 0.1 秒，同时保留提交确认失败即暂停的安全逻辑。</li></ul>
<h3>正式版 1.2.28</h3>
<ul><li>“全选任务”改为应用自绘复选框，浅色和深色主题下均保持清晰边框与选中标记。</li><li>修复深色模式中提交间隔滑块继承窗口底色而出现黑色矩形边缘的问题。</li><li>深色滑块轨道适当提亮，保持与设置面板背景的层次。</li></ul>
<h3>正式版 1.2.27</h3>
<ul><li>新增浅色、深色和跟随 Windows 三档主题，可从顶部主题菜单即时切换并自动记忆。</li><li>深色主题统一适配主界面、任务卡片、参数栏、菜单、滚动条、状态提示、图片预览、更新日志、安装向导和消息框。</li><li>自绘复选框、开关、拖拽遮罩、空列表提示及 Windows 原生标题栏会同步刷新，避免局部亮白。</li></ul>
<h3>正式版 1.2.26</h3>
<ul><li>任务卡片的参数标注改为根据实际可用宽度自适应显示，窄屏不再裁掉末尾的比例、音频或单独设置状态。</li><li>宽度不足时按照参数分隔符智能拆成两行，并自动选择更均衡的断点；宽度充足时保持原有单行展示。</li><li>完整参数继续保留在悬停提示中。</li></ul>
<h3>正式版 1.2.25</h3>
<ul><li>“批量默认设置”在宽屏和窄屏下均改为居中加粗标题，强化右侧设置栏的层级。</li><li>窄屏底部信息行取消整组收拢居中，改由整体进度区域自适应吸收剩余宽度，使统计、进度和状态铺满整行。</li><li>宽屏底部命令栏保持原有布局。</li></ul>
<h3>正式版 1.2.24</h3>
<ul><li>窄屏任务工具栏第一行改为自然宽度控件组居中，与第二行移除操作保持统一。</li><li>窄屏底部统计、进度与当前状态改为整体居中，不再沿用宽屏的单侧拉伸布局。</li><li>两行均采用对称留白和自适应间距，宽屏布局保持不变。</li></ul>
<h3>正式版 1.2.23</h3>
<ul><li>窄屏顶部第一行改为整体居中，并将“打开 Grok”和浏览器助手教程按钮移入第一行。</li><li>浏览器连接状态独占第二行居中显示，两行左右留白保持严格对称。</li><li>宽屏顶部布局保持不变。</li></ul>
<h3>正式版 1.2.22</h3>
<ul><li>窄屏换行控件由分散的等宽槽位改为自然宽度控件组，增强同组操作之间的关联感。</li><li>换行后的控件组整体水平居中，左右留白严格对称，组内间距随窗口宽度在 8～24 像素间自适应。</li><li>宽屏布局保持不变。</li></ul>
<h3>正式版 1.2.21</h3>
<ul><li>优化窄屏换行布局，顶部连接区、任务移除区和底部操作区改用等宽槽位铺满整行。</li><li>换行后的每个控件在各自槽位中居中，左右留白与选项间距随窗口宽度自动对称伸缩。</li><li>宽屏布局保持原有排列，仅在窗口宽度不足时启用新的均衡布局。</li></ul>
<h3>正式版 1.2.20</h3>
<ul><li>浏览器助手图标移至扩展根目录，Chrome/Edge 选择扩展目录时不再显示容易误点的 icons 子文件夹。</li><li>软件升级扩展时会自动清理旧版遗留文件及空目录，使 browser_helper 目录选择界面与教程保持一致。</li><li>安装向导明确提示停留在 browser_helper，不要进入任何子文件夹，直接点击“选择文件夹”。</li></ul>
<h3>正式版 1.2.19</h3>
<ul><li>右侧提示词输入框改为高宽自适应，自动占用图片设置栏上方的全部可用空间。</li><li>图片设置、提交间隔、说明和应用按钮整体固定在面板底部，不再在高窗口中留下无效空白。</li><li>保留现有下拉菜单弹出方式，不强制改变菜单方向。</li></ul>
<h3>正式版 1.2.18</h3>
<ul><li>图片设置栏重构为五个真正等宽的分段选择器，图标与文字整体居中，宽屏与半屏保持一致排列。</li><li>设置选项改用独立宽度菜单，长选项不再随窄按钮省略；音频开启与关闭使用不同状态图标。</li><li>新增低高度紧凑布局，提示词框和说明文字按高度收缩，同时锁定设置栏高度，避免控件互相覆盖。</li></ul>
<h3>正式版 1.2.17</h3>
<ul><li>修复桌面端与浏览器助手版本不一致时扩展每隔数秒自我重载，最终被 Chrome 停用的问题。</li><li>版本不一致时改为暂停自动化、断开调试连接并显示黄色异常状态；版本恢复一致后自动恢复检测。</li><li>扩展弹出页会明确显示桌面软件与浏览器助手版本，并提示关闭旧版软件或手动重新加载一次。</li></ul>
<h3>正式版 1.2.16</h3>
<ul><li>移除右侧设置滚动条，参照 Grok 制作页将图片用途、音频、分辨率、时长和比例合并为紧凑的横向参数栏。</li><li>非首帧模式禁用 1080p，如已选择则自动回退到 720p；首帧模式禁用比例选择并显示“底图决定”。</li><li>参数限制同时在界面交互、设置收集和任务提交层校验，防止非法组合进入任务队列。</li></ul>
<h3>正式版 1.2.15</h3>
<ul><li>修复窗口高度不足时提示词和图片用途等参数控件被压扁、文字重叠的问题。</li><li>右侧设置内容改为高度不足时局部滚动，高度充足时自动恢复完整显示。</li><li>“应用到所选任务”和“所选任务恢复默认设置”固定在设置面板底部，不随参数内容滚走。</li></ul>
<h3>正式版 1.2.14</h3>
<ul><li>新增 Windows 左右半屏吸附布局，在约 960 像素半屏宽度下不再越过屏幕中线。</li><li>半屏时仅将顶部工具、任务操作和底部命令栏切换为双行，任务列表与批量设置仍保持左右并排。</li><li>保留 1180×760 完整布局基线，未扩展到其他屏幕尺寸的额外适配。</li></ul>
<h3>正式版 1.2.13</h3>
<ul><li>图片预览窗口在当前浅色主题下改用雾蓝灰背景、白色图片画布和浅色按钮。</li><li>保留明暗主题双套样式，未来启用全局深色模式时可自动切换。</li></ul>
<h3>正式版 1.2.12</h3>
<ul><li>统一适配更新日志、安装向导和系统确认等弹窗的 Windows 标题栏，与主界面浅色蓝灰主题保持一致。</li><li>图片预览标题栏新增自动主题机制，可随以后的全局深色模式自动切换。</li><li>保留 Windows 原生标题栏按钮行为，未改动弹窗名称及固定布局。</li></ul>
<h3>正式版 1.2.11</h3>
<ul><li>重绘浏览器专用太阳头像，并针对 16、32、48、128 像素分别输出清晰图标。</li><li>扩展状态点升级为五态显示：灰色休眠、绿色就绪、蓝色运行、黄色暂停或版本异常、红色任务错误。</li><li>工具栏悬停提示和扩展弹出页同步显示当前状态。</li></ul>
<h3>正式版 1.2.10</h3>
<ul><li>浏览器助手与桌面软件连接时，工具栏图标右下角显示绿色运行状态点；桌面软件关闭后自动恢复普通图标。</li><li>浏览器助手弹出页标题旁新增当前正式版本号。</li></ul>
<h3>正式版 1.2.9</h3>
<ul><li>彻底修复任务状态变化时悬浮移除按钮短暂脱离父控件、显示成黑白小窗口的问题。</li><li>状态、参数和错误信息改为在原任务卡片内原位更新，不再因状态变化销毁并重建列表。</li></ul>
<h3>正式版 1.2.8</h3>
<ul><li>修复任务状态变化时列表清空与重建之间短暂露出残留控件和未完成绘制的问题。</li><li>任务列表刷新改为原子绘制，并在重建前立即收起旧任务的悬浮移除按钮。</li></ul>
<h3>正式版 1.2.7</h3>
<ul><li>顶部版本号改为与品牌标题一致的纯文字样式，移除胶囊底色和边框。</li><li>“打开 Grok”按钮改用 Grok 官方标志轮廓，取代通用外链图标。</li></ul>
<h3>正式版 1.2.6</h3>
<ul><li>主界面顶部新增“打开 Grok”按钮，可直接前往 Grok Imagine 工作页面。</li><li>按钮归入浏览器状态与助手检查功能组，并采用统一的蓝色描边与双层线性图标。</li></ul>
<h3>正式版 1.2.5</h3>
<ul><li>任务右键菜单新增“在文件夹中显示”和单任务“恢复默认设置”。</li><li>菜单按等待、失败、已提交与处理中状态动态显示对应操作。</li><li>等待任务支持设为下一个任务，失败任务支持复制错误信息；处理中任务禁止编辑、恢复、移除与重新提交。</li></ul>
<h3>正式版 1.2.4</h3>
<ul><li>重绘全套功能图标，统一为 24 像素圆角双层线性风格。</li><li>为选择、删除、设置、应用、恢复、重试、重新提交和扩展安装设计独立语义图形。</li><li>移除图标外围统一的小方块底板，放大有效图形并统一线宽、色彩和视觉中心。</li></ul>
<h3>正式版 1.2.3</h3>
<ul><li>将任务悬停移除按钮提升到任务模块浮层，修复按钮在列表右边缘被裁切的问题。</li></ul>
<h3>正式版 1.2.2</h3>
<ul><li>重新绘制任务悬停移除按钮，叉号在圆形按钮内严格居中。</li><li>按钮改为列表浮层并移动到任务卡片右上角边缘，滚动列表时自动同步位置。</li></ul>
<h3>正式版 1.2.1</h3>
<ul><li>修复两个滑动开关和任务复选框可视区域与实际点击区域不一致的问题。</li><li>滑动开关增加轨道渐变和圆点平移动画。</li><li>任务支持右键打开预览、编辑、重试与移除菜单。</li><li>任务卡片右上角增加悬停显示的“×”快速移除按钮。</li><li>“应用到所选任务”升级为蓝底白字主操作按钮。</li></ul>
<h3>正式版 1.2.0</h3>
<ul><li>建立“晨曦云海·蓝金轻奢”统一设计系统，重构主界面、任务卡片与全部弹窗。</li><li>任务状态改用克制的色彩、左侧状态条与柔和过渡，不再依赖厚重边框。</li><li>加入按钮按压反馈、任务入场、悬停操作和弹窗淡入上移动效。</li><li>新增底部总体进度条，统一线性图标、胶囊滚动条、控件圆角和排版层级。</li><li>严格保留 EA 0.97.11 的 1180×760 固定布局基线，未加入响应式重排。</li></ul>
<h3>正式版 1.1.2</h3>
<ul><li>将六步浏览器扩展安装图完整内置到软件安装向导。</li><li>教程支持胶囊滚动浏览和单独放大查看。</li><li>强化“文件夹显示空白是正常的，直接选择当前文件夹”提示。</li></ul>
<h3>正式版 1.1.1</h3>
<ul><li>移除等比例缩略图外围的固定方形底框和描边。</li><li>圆角只作用于实际图片区域，横图与竖图周围保持透明。</li></ul>
<h3>正式版 1.1.0</h3>
<ul><li>移除任务被选中时由系统绘制的黑色焦点边框。</li><li>缩略图改为按原图比例完整显示，可直接区分横图、竖图和方图。</li></ul>
<h3>正式版 1.0.4</h3>
<ul><li>双击任务行即可直接预览对应图片。</li><li>任务复选框改为更醒目的大号高对比样式。</li><li>更新日志使用与任务列表一致的胶囊滚动条。</li><li>图片预览窗口根据图片尺寸和屏幕可用空间自动适配。</li></ul>
<h3>正式版 1.0.3</h3>
<ul><li>彻底修复任务行快捷按钮接收错误参数、点击后无反应的问题。</li><li>更新日志补齐全部历史版本记录。</li></ul>
<h3>正式版 1.0.2</h3>
<ul><li>修复快捷按钮悬停区域提前隐藏的问题。</li><li>增加软件内图片预览、任务编辑反馈和手动更新日志弹窗。</li></ul>
<h3>正式版 1.0.1</h3>
<ul><li>任务列表增加复选框、全选、彩色状态标签和参数摘要。</li><li>增加当前任务/失败任务高亮及悬停快捷操作区。</li></ul>
<h3>正式版 1.0.0</h3>
<ul><li>首个正式版本，以稳定的 EA 0.97.11 布局为基础。</li><li>合入多格式缩略图支持，软件和浏览器助手版本统一。</li></ul>

<h2>EA 测试版本</h2>
<h3>EA 0.97.14</h3><ul><li>撤销不满意的响应式布局改动，恢复 0.97.11 的固定布局结构。</li></ul>
<h3>EA 0.97.13</h3><ul><li>继续校正小尺寸窗口下的区域压缩和控制栏显示。</li></ul>
<h3>EA 0.97.12</h3><ul><li>尝试多分辨率窗口适配；干净重建版仅保留多格式缩略图修复。</li><li>新增 JPG、WebP、BMP、GIF、TIFF、AVIF、HEIC/HEIF 缩略图解码。</li></ul>
<h3>EA 0.97.11</h3><ul><li>完成浅色标题栏主题适配并确立稳定固定布局基线。</li></ul>
<h3>EA 0.97.10</h3><ul><li>分辨率、时长、比例和音频只在目标状态不一致时操作。</li><li>软件标题栏开始适配整体浅色主题。</li></ul>
<h3>EA 0.97.9</h3><ul><li>增加视频是否附带音频的选项并接入浏览器自动化。</li></ul>
<h3>EA 0.97.8</h3><ul><li>浏览器助手增加桌面软件存活检测；软件关闭后停止影响 Grok 页面。</li></ul>
<h3>EA 0.97.7</h3><ul><li>放大任务栏图标的有效图形区域，改善小尺寸辨识度。</li></ul>
<h3>EA 0.97.6</h3><ul><li>修正应用图标在窗口、任务栏和高 DPI 环境中的显示。</li></ul>
<h3>EA 0.97.5</h3><ul><li>提示词输入区增加一键清空按钮。</li><li>全部常用按钮增加鼠标悬停交互效果。</li></ul>
<h3>EA 0.97.4</h3><ul><li>更换软件及浏览器助手图标，并同步多尺寸图标资源。</li></ul>
<h3>EA 0.97.3</h3><ul><li>移除不符合预期的厚重投影，改为更清爽的层次表现。</li></ul>
<h3>EA 0.97.2</h3><ul><li>加入轻微立体层次、语义图标和模块差异色。</li><li>强化复选按钮开启与关闭状态的视觉差别。</li></ul>
<h3>EA 0.97.1</h3><ul><li>滚动条改为无箭头、灰色描边和圆角滑块。</li><li>软件正式更名为“老七的Grok视频助手”。</li></ul>

<h2>0.9 系列</h2>
<h3>0.9.7</h3><ul><li>继续完善浅色主题细节、按钮图标和列表区域表现。</li></ul>
<h3>0.9.6</h3><ul><li>调整界面底色，避免大面积纯白，并优化滚动条外观。</li></ul>
<h3>0.9.5</h3><ul><li>顶部品牌区与背景融合，补充按钮语义图标并校正字体粗细。</li></ul>
<h3>0.9.4</h3><ul><li>将最终选定的浅色蓝金 UI 概念应用到软件。</li><li>合并拖入提示与任务列表，选择按钮行置顶。</li></ul>
<h3>0.9.3</h3><ul><li>完成拖入提示与任务列表结构合并的前置整理。</li></ul>
<h3>0.9.2</h3><ul><li>同步软件与浏览器助手品牌图标。</li></ul>
<h3>0.9.1</h3><ul><li>安装/修复按钮改为只打开教程，不再同时打开浏览器管理页。</li></ul>
<h3>0.9.0</h3><ul><li>浏览器助手图标与软件图标同步。</li></ul>

<h2>0.8 系列</h2>
<h3>0.8.9</h3><ul><li>无需打开 Grok 即可检测 Chrome/Edge 中是否已安装助手。</li></ul>
<h3>0.8.8</h3><ul><li>安装教程用语改为“加载未打包的扩展程序”。</li><li>软件首次启动时自动显示助手安装教程。</li></ul>
<h3>0.8.7</h3><ul><li>内置扩展释放到固定目录，增加引导式安装、修复和版本检测。</li><li>浏览器助手版本号开始与软件版本同步。</li></ul>
<h3>0.8.6</h3><ul><li>改善 Chrome/Edge 扩展管理页面的打开方式与失败提示。</li></ul>
<h3>0.8.5</h3><ul><li>扩展上下文失效时安全停止轮询，减少浏览器错误记录。</li></ul>
<h3>0.8.4</h3><ul><li>提交间隔范围调整为 1–5 秒。</li><li>修复更新扩展后出现的 Extension context invalidated 报错。</li></ul>
<h3>0.8.3</h3><ul><li>增强成功提交后的队列衔接与超时错误提示。</li></ul>
<h3>0.8.2</h3><ul><li>增加底图比例自动识别，并修复成功后未继续下一项的问题。</li></ul>
<h3>0.8.1</h3><ul><li>完善窗口和任务栏图标设置，改善高分辨率图标显示。</li></ul>
<h3>0.8.0</h3><ul><li>任务列表增加图片缩略图。</li><li>增加完成列表后暂停、完成后清空列表和提交间隔滑条。</li><li>首帧用途自动切换为自动比例。</li></ul>

<h2>早期版本</h2>
<h3>0.7.7</h3><ul><li>形成批量图片导入、任务列表、批量设置和浏览器助手协作的基础版本。</li></ul>
<h3>0.7.0–0.7.6</h3><ul><li>持续完善批量提交、失败重试、所选重新提交及任务状态显示。</li></ul>
<h3>0.6.1</h3><ul><li>首个可运行原型，建立桌面控制台与浏览器扩展之间的本地通信。</li></ul>
"""


def app_icon_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "app_icon.png"


def native_icon_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "app_icon.ico"


def brand_sky_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "brand_sky.png"


def brand_sun_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "brand_sun.png"


def extension_tutorial_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets" / "browser_extension_tutorial.png"


def read_thumbnail_image(path: str, size: int = 72):
    """Decode a thumbnail with Qt first and Pillow/HEIF support as fallback."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    source_size = reader.size()
    if source_size.isValid():
        reader.setScaledSize(source_size.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if not image.isNull():
        return image

    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        from PIL import Image, ImageOps
        from PIL.ImageQt import ImageQt

        with Image.open(path) as source:
            source.seek(0)
            source = ImageOps.exif_transpose(source).convert("RGBA")
            width, height = source.size
            if width and height:
                scale = min(size / width, size / height)
                source = source.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
            return ImageQt(source).copy()
    except (OSError, ValueError, RuntimeError):
        return image


def read_preview_image(path: str, maximum: QSize):
    """Decode a preview without enlarging small images or exceeding the screen."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    reader.setDecideFormatFromContent(True)
    source_size = reader.size()
    if source_size.isValid() and (source_size.width() > maximum.width() or source_size.height() > maximum.height()):
        reader.setScaledSize(source_size.scaled(maximum, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if not image.isNull():
        return image
    try:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        from PIL import Image, ImageOps
        from PIL.ImageQt import ImageQt

        with Image.open(path) as source:
            source.seek(0)
            source = ImageOps.exif_transpose(source).convert("RGBA")
            source.thumbnail((maximum.width(), maximum.height()), Image.Resampling.LANCZOS)
            return ImageQt(source).copy()
    except (OSError, ValueError, RuntimeError):
        return image


def line_icon(kind: str, color: str = ACCENT, size: int = 22) -> QIcon:
    """Draw the app's rounded duotone icon family on a common 24 px grid."""
    canvas = QPixmap(24, 24); canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    primary = QColor(color); secondary = QColor(color); secondary.setAlpha(38)
    pen = QPen(primary, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)

    def badge(cx, cy, radius=4.1):
        painter.setPen(QPen(primary, 1.45)); painter.setBrush(secondary)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)

    def plus(cx, cy, arm=2.1):
        painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
        painter.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))

    def check(cx, cy, scale=1.0):
        path = QPainterPath(); path.moveTo(cx - 2.5 * scale, cy)
        path.lineTo(cx - .7 * scale, cy + 1.8 * scale); path.lineTo(cx + 2.8 * scale, cy - 2.1 * scale)
        painter.drawPath(path)

    if kind in {"image", "image_add", "image_tag"}:
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3, 4, 17, 16), 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawEllipse(QRectF(6.1, 7.1, 3.2, 3.2))
        path = QPainterPath(); path.moveTo(4.8, 17.2); path.lineTo(9.5, 12.6); path.lineTo(12.5, 15.4)
        path.lineTo(15.4, 11.8); path.lineTo(19.2, 16.5); painter.drawPath(path)
        if kind == "image_add":
            badge(18.1, 5.9, 3.9); plus(18.1, 5.9, 1.8)
        elif kind == "image_tag":
            tag = QPainterPath(); tag.moveTo(15.1, 4.2); tag.lineTo(20.3, 4.2); tag.lineTo(22, 5.9)
            tag.lineTo(18.1, 9.8); tag.lineTo(14.2, 5.9); tag.closeSubpath()
            painter.setBrush(secondary); painter.drawPath(tag); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(18.2, 5.2, 1.2, 1.2))
    elif kind in {"folder", "folder_add", "folder_reveal"}:
        path = QPainterPath(); path.moveTo(3, 7.2); path.lineTo(8.6, 7.2); path.lineTo(10.7, 9.2)
        path.lineTo(21, 9.2); path.lineTo(19.2, 19.3); path.lineTo(4.3, 19.3); path.closeSubpath()
        painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(4.1, 7.2), QPointF(5.7, 5.2)); painter.drawLine(QPointF(5.7, 5.2), QPointF(10.3, 5.2))
        if kind == "folder_add":
            badge(18.2, 8.1, 3.9); plus(18.2, 8.1, 1.8)
        elif kind == "folder_reveal":
            badge(17.7, 16.8, 3.8); painter.drawEllipse(QPointF(17.2, 16.3), 1.6, 1.6)
            painter.drawLine(QPointF(18.5, 17.6), QPointF(21.2, 20.3))
    elif kind in {"trash", "trash_check", "trash_all"}:
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(7, 8, 10, 12), 2, 2)
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(5, 7), QPointF(19, 7))
        painter.drawLine(QPointF(9, 4.5), QPointF(15, 4.5)); painter.drawLine(QPointF(10, 11), QPointF(10, 16.8)); painter.drawLine(QPointF(14, 11), QPointF(14, 16.8))
        if kind == "trash_check":
            badge(18.4, 17.8, 3.8); check(18.4, 17.8, .8)
        elif kind == "trash_all":
            painter.drawLine(QPointF(18.5, 3.2), QPointF(21.2, 3.2)); painter.drawLine(QPointF(19.9, 1.8), QPointF(19.9, 4.6))
            painter.drawLine(QPointF(2.8, 11.3), QPointF(4.8, 11.3))
    elif kind in {"sliders", "gear"}:
        for y, knob in ((6.0, 9.0), (12.0, 16.0), (18.0, 7.0)):
            painter.drawLine(QPointF(3.5, y), QPointF(20.5, y)); painter.setBrush(secondary)
            painter.drawEllipse(QPointF(knob, y), 2.35, 2.35); painter.setBrush(Qt.BrushStyle.NoBrush)
    elif kind in {"moon", "sun"}:
        if kind == "moon":
            moon = QPainterPath(); moon.addEllipse(QRectF(4, 3, 16, 18))
            cutout = QPainterPath(); cutout.addEllipse(QRectF(9, 1, 14, 16))
            moon = moon.subtracted(cutout)
            painter.setBrush(secondary); painter.drawPath(moon); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawPath(moon)
        else:
            painter.setBrush(secondary); painter.drawEllipse(QRectF(7, 7, 10, 10)); painter.setBrush(Qt.BrushStyle.NoBrush)
            for first, second in (((12, 2), (12, 5)), ((12, 19), (12, 22)), ((2, 12), (5, 12)),
                                  ((19, 12), (22, 12)), ((4.8, 4.8), (7, 7)), ((17, 17), (19.2, 19.2)),
                                  ((4.8, 19.2), (7, 17)), ((17, 7), (19.2, 4.8))):
                painter.drawLine(QPointF(*first), QPointF(*second))
    elif kind in {"monitor", "monitor_focus"}:
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3, 4, 18, 13), 2.5, 2.5)
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(12, 17), QPointF(12, 20.5)); painter.drawLine(QPointF(8.5, 20.5), QPointF(15.5, 20.5))
        if kind == "monitor_focus":
            painter.drawLine(QPointF(6, 9), QPointF(6, 7)); painter.drawLine(QPointF(6, 7), QPointF(8, 7))
            painter.drawLine(QPointF(16, 7), QPointF(18, 7)); painter.drawLine(QPointF(18, 7), QPointF(18, 9))
            painter.drawLine(QPointF(6, 12), QPointF(6, 14)); painter.drawLine(QPointF(6, 14), QPointF(8, 14))
            painter.drawLine(QPointF(16, 14), QPointF(18, 14)); painter.drawLine(QPointF(18, 14), QPointF(18, 12))
    elif kind in {"clock", "timer"}:
        painter.setBrush(secondary); painter.drawEllipse(QRectF(4, 4.5, 16, 16))
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(12, 8), QPointF(12, 12.8)); painter.drawLine(QPointF(12, 12.8), QPointF(15.4, 14.8))
        if kind == "timer":
            painter.drawLine(QPointF(9.3, 2.5), QPointF(14.7, 2.5)); painter.drawLine(QPointF(12, 2.5), QPointF(12, 4.2)); painter.drawLine(QPointF(19, 5.3), QPointF(20.4, 6.7))
    elif kind == "crop":
        painter.drawLine(QPointF(6, 3), QPointF(6, 17)); painter.drawLine(QPointF(6, 17), QPointF(20.5, 17))
        painter.drawLine(QPointF(3, 7), QPointF(17, 7)); painter.drawLine(QPointF(17, 7), QPointF(17, 21))
        painter.setPen(QPen(secondary, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)); painter.drawLine(QPointF(9.2, 10.2), QPointF(13.8, 14.8)); painter.setPen(pen)
    elif kind in {"audio", "audio_off"}:
        path = QPainterPath(); path.moveTo(3.5, 10); path.lineTo(7.7, 10); path.lineTo(12.5, 6.2)
        path.lineTo(12.5, 17.8); path.lineTo(7.7, 14); path.lineTo(3.5, 14); path.closeSubpath()
        painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "audio_off":
            painter.drawLine(QPointF(14.2, 8.2), QPointF(21, 15.8)); painter.drawLine(QPointF(21, 8.2), QPointF(14.2, 15.8))
        else:
            painter.drawArc(QRectF(12.3, 7.2, 6.2, 9.6), -60 * 16, 120 * 16); painter.drawArc(QRectF(12.1, 4.2, 10.5, 15.6), -55 * 16, 110 * 16)
    elif kind == "interval":
        painter.setBrush(secondary); painter.drawEllipse(QPointF(5.3, 12), 2.5, 2.5); painter.drawEllipse(QPointF(18.7, 12), 2.5, 2.5)
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(8.4, 12), QPointF(15.6, 12)); painter.drawLine(QPointF(10, 10), QPointF(8, 12)); painter.drawLine(QPointF(8, 12), QPointF(10, 14)); painter.drawLine(QPointF(14, 10), QPointF(16, 12)); painter.drawLine(QPointF(16, 12), QPointF(14, 14))
        painter.drawArc(QRectF(8.5, 3, 7, 7), 35 * 16, 290 * 16)
    elif kind in {"restore", "reset", "retry_alert"}:
        painter.setBrush(secondary); painter.drawEllipse(QRectF(4.3, 4.3, 15.4, 15.4)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(4.3, 4.3, 15.4, 15.4), 35 * 16, 285 * 16); painter.drawLine(QPointF(5.1, 5.1), QPointF(5.1, 10.1)); painter.drawLine(QPointF(5.1, 5.1), QPointF(10.1, 5.1))
        if kind == "restore":
            painter.setBrush(primary); painter.drawEllipse(QPointF(12, 12), 1.4, 1.4); painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "retry_alert":
            badge(18.4, 17.8, 3.8); painter.drawLine(QPointF(18.4, 15.8), QPointF(18.4, 18)); painter.drawPoint(QPointF(18.4, 19.6))
    elif kind == "history":
        painter.setBrush(secondary); painter.drawEllipse(QRectF(2.8, 6.2, 11.6, 11.6)); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(QRectF(2.8, 6.2, 11.6, 11.6), 35 * 16, 292 * 16)
        painter.drawLine(QPointF(3.6, 6.9), QPointF(3.6, 10.2)); painter.drawLine(QPointF(3.6, 6.9), QPointF(6.9, 6.9))
        painter.drawLine(QPointF(8.6, 9), QPointF(8.6, 12.1)); painter.drawLine(QPointF(8.6, 12.1), QPointF(10.8, 13.4))
        for y, width in ((7.2, 5.2), (12, 6.8), (16.8, 5.2)):
            painter.drawLine(QPointF(16, y), QPointF(16 + width, y))
    elif kind in {"list", "resubmit"}:
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(4, 3, 16, 18), 2.5, 2.5); painter.setBrush(Qt.BrushStyle.NoBrush)
        for y in (7.5, 12, 16.5): painter.drawLine(QPointF(8, y), QPointF(17, y))
        if kind == "resubmit":
            painter.drawArc(QRectF(13.5, 13.3, 8, 8), 35 * 16, 280 * 16); painter.drawLine(QPointF(14.3, 14.1), QPointF(14.3, 17.3)); painter.drawLine(QPointF(14.3, 14.1), QPointF(17.5, 14.1))
    elif kind in {"prompt", "prompt_spark"}:
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3, 5, 16, 12), 3.5, 3.5); painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(); path.moveTo(7.3, 17); path.lineTo(6.2, 20.7); path.lineTo(11, 17); painter.drawPath(path)
        painter.drawLine(QPointF(20.5, 2.5), QPointF(20.5, 7.5)); painter.drawLine(QPointF(18, 5), QPointF(23, 5))
        painter.drawLine(QPointF(16.4, 2.8), QPointF(17.3, 3.7))
    elif kind == "eraser":
        path = QPainterPath(); path.moveTo(3.6, 15.2); path.lineTo(11.8, 7); path.lineTo(20.2, 15.4)
        path.lineTo(14.4, 21); path.lineTo(8, 21); path.lineTo(3.1, 17.2); path.closeSubpath()
        painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(8.1, 10.8), QPointF(16.2, 18.9)); painter.drawLine(QPointF(14.3, 21), QPointF(21.5, 21))
        painter.drawLine(QPointF(20.2, 6.2), QPointF(22, 4.4)); painter.drawLine(QPointF(19.5, 3.8), QPointF(19.5, 1.8))
    elif kind == "apply":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3, 4, 13, 9), 2.2, 2.2); painter.drawRoundedRect(QRectF(6, 11, 13, 9), 2.2, 2.2)
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(20.2, 5), QPointF(20.2, 14)); painter.drawLine(QPointF(17.8, 11.6), QPointF(20.2, 14)); painter.drawLine(QPointF(22.4, 11.6), QPointF(20.2, 14))
    elif kind == "move_next":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3, 4, 12, 16), 2.5, 2.5); painter.setBrush(Qt.BrushStyle.NoBrush)
        for y in (8, 12, 16): painter.drawLine(QPointF(6, y), QPointF(12, y))
        painter.drawLine(QPointF(17.5, 4.5), QPointF(17.5, 16)); painter.drawLine(QPointF(14.6, 7.5), QPointF(17.5, 4.5)); painter.drawLine(QPointF(20.4, 7.5), QPointF(17.5, 4.5))
    elif kind == "copy":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(7, 7, 13, 13), 2.5, 2.5); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(3.5, 3.5, 13, 13), 2.5, 2.5)
    elif kind == "external_link":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(3.5, 7, 13.5, 13.5), 2.7, 2.7); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(11, 13), QPointF(20.5, 3.5)); painter.drawLine(QPointF(14.7, 3.5), QPointF(20.5, 3.5)); painter.drawLine(QPointF(20.5, 3.5), QPointF(20.5, 9.3))
    elif kind == "grok_mark":
        # Grok's official mark, redrawn from its original 33.7 x 33 vector paths.
        painter.save(); painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(primary)
        painter.translate(1.5, 1.1); painter.scale(0.625, 0.625)
        upper = QPainterPath(); upper.moveTo(13.2371, 21.0407); upper.lineTo(24.3186, 12.8506)
        upper.cubicTo(24.8619, 12.4491, 25.6384, 12.6057, 25.8973, 13.2294)
        upper.cubicTo(27.2597, 16.5185, 26.651, 20.4712, 23.9403, 23.1851)
        upper.cubicTo(21.2297, 25.8989, 17.4581, 26.4941, 14.0108, 25.1386)
        upper.lineTo(10.2449, 26.8843); upper.cubicTo(15.6463, 30.5806, 22.2053, 29.6665, 26.304, 25.5601)
        upper.cubicTo(29.5551, 22.3051, 30.562, 17.8683, 29.6205, 13.8673)
        upper.lineTo(29.629, 13.8758); upper.cubicTo(28.2637, 7.99809, 29.9647, 5.64871, 33.449, 0.844576)
        upper.cubicTo(33.5314, 0.730667, 33.6139, 0.616757, 33.6964, 0.5)
        upper.lineTo(29.1113, 5.09055); upper.lineTo(29.1113, 5.07631); upper.lineTo(13.2343, 21.0436); upper.closeSubpath()
        lower = QPainterPath(); lower.moveTo(10.9503, 23.0313)
        lower.cubicTo(7.07343, 19.3235, 7.74185, 13.5853, 11.0498, 10.2763)
        lower.cubicTo(13.4959, 7.82722, 17.5036, 6.82767, 21.0021, 8.2971)
        lower.lineTo(24.7595, 6.55998); lower.cubicTo(24.0826, 6.07017, 23.2155, 5.54334, 22.2195, 5.17313)
        lower.cubicTo(17.7198, 3.31926, 12.3326, 4.24192, 8.67479, 7.90126)
        lower.cubicTo(5.15635, 11.4239, 4.0499, 16.8403, 5.94992, 21.4622)
        lower.cubicTo(7.36924, 24.9165, 5.04257, 27.3598, 2.69884, 29.826)
        lower.cubicTo(1.86829, 30.7002, 1.0349, 31.5745, 0.36364, 32.5)
        lower.lineTo(10.9474, 23.0341); lower.closeSubpath()
        painter.drawPath(upper); painter.drawPath(lower); painter.restore()
    elif kind == "puzzle_check":
        path = QPainterPath(); path.moveTo(4, 6); path.lineTo(9, 6); path.cubicTo(8.6, 3.2, 10.1, 2, 12, 2)
        path.cubicTo(13.9, 2, 15.4, 3.2, 15, 6); path.lineTo(20, 6); path.lineTo(20, 11)
        path.cubicTo(22.8, 10.6, 24, 12.1, 24, 14); path.cubicTo(24, 15.9, 22.8, 17.4, 20, 17)
        path.lineTo(20, 20); path.lineTo(4, 20); path.closeSubpath(); painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush)
        badge(18.2, 18.2, 3.8); check(18.2, 18.2, .8)
    elif kind in {"send", "play"}:
        path = QPainterPath(); path.moveTo(3, 5); path.lineTo(21.5, 12); path.lineTo(3, 19)
        path.lineTo(7.3, 12); path.closeSubpath(); painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(7.3, 12), QPointF(17.8, 12))
    elif kind == "pause":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(5, 4, 5.2, 16), 2, 2); painter.drawRoundedRect(QRectF(13.8, 4, 5.2, 16), 2, 2)
    elif kind == "eye":
        path = QPainterPath(); path.moveTo(2, 12); path.cubicTo(6, 5.5, 18, 5.5, 22, 12); path.cubicTo(18, 18.5, 6, 18.5, 2, 12)
        painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawEllipse(QRectF(9, 9, 6, 6)); painter.setBrush(primary); painter.drawEllipse(QPointF(12, 12), 1.3, 1.3)
    elif kind == "edit":
        painter.setBrush(secondary); painter.drawRoundedRect(QRectF(4, 5, 14, 15), 2.5, 2.5); painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(8, 16), QPointF(18, 6)); painter.drawLine(QPointF(16, 4), QPointF(20, 8)); painter.drawLine(QPointF(7.5, 16.5), QPointF(7, 19))
    elif kind == "check":
        check(12, 12, 2.3)
    elif kind == "alert":
        path = QPainterPath(); path.moveTo(12, 3); path.lineTo(22, 20); path.lineTo(2, 20); path.closeSubpath()
        painter.setBrush(secondary); painter.drawPath(path); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawLine(QPointF(12, 9), QPointF(12, 14)); painter.drawPoint(QPointF(12, 17))
    else:
        painter.drawEllipse(QRectF(5, 5, 14, 14)); painter.drawPoint(QPointF(12, 12))

    painter.end()
    return QIcon(canvas.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


def premium_icon(kind: str, color: str = ACCENT, size: int = 24) -> QIcon:
    """Return a full-size duotone glyph without a generic enclosing tile."""
    return line_icon(kind, color, size)


def icon_text_label(text: str, kind: str, color: str = GOLD, point_size: int = 10,
                    weight: QFont.Weight = QFont.Weight.DemiBold, *, centered: bool = False,
                    text_object_name: str = "") -> QWidget:
    container = QWidget(); container.setObjectName("iconText")
    row = QHBoxLayout(container); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
    icon = QLabel(); icon.setPixmap(premium_icon(kind, color, 24).pixmap(24, 24)); icon.setFixedSize(26, 26)
    label = QLabel(text); label.setFont(QFont("Microsoft YaHei UI", point_size, weight))
    if text_object_name:
        label.setObjectName(text_object_name)
    if centered:
        row.addStretch(1)
    row.addWidget(icon); row.addWidget(label); row.addStretch(1)
    return container


class SettingSelector(QPushButton):
    """Equal-width settings segment with a content-sized, non-eliding menu."""

    currentTextChanged = Signal(str)

    def __init__(self, label: str, values: list[str], icon_kind: str, icon_color: str,
                 value_icons: dict[str, tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        self.label = label
        self._values = tuple(values)
        self._current = self._values[0]
        self._display_override: str | None = None
        self._value_icons = value_icons or {}
        self._icons: dict[str, QIcon] = {}
        self._actions: dict[str, QAction] = {}
        self.setObjectName("settingSelector")
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0); self.setFixedHeight(38)
        self.menu = QMenu(self); self.menu.setObjectName("settingMenu")
        for value in self._values:
            kind, color = self._value_icons.get(value, (icon_kind, icon_color))
            icon = premium_icon(kind, color, 18)
            self._icons[value] = icon
            action = self.menu.addAction(icon, value)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, selected=value: self.setCurrentText(selected))
            self._actions[value] = action
        widest = max(QFontMetrics(self.font()).horizontalAdvance(value) for value in self._values)
        self.menu.setMinimumWidth(max(132, widest + 72))
        self.setMenu(self.menu)
        self.setCompact(False)
        self._refresh_visual()

    def currentText(self) -> str:
        return self._current

    def setCurrentText(self, value: str):
        if value not in self._actions:
            return
        changed = value != self._current
        self._current = value
        self._display_override = None
        self._refresh_visual()
        if changed:
            self.currentTextChanged.emit(value)

    def setDisplayOverride(self, text: str | None):
        self._display_override = text
        self._refresh_visual()

    def setOptionEnabled(self, value: str, enabled: bool, reason: str = ""):
        action = self._actions.get(value)
        if action is not None:
            action.setEnabled(enabled)
            action.setToolTip(reason)
            action.setStatusTip(reason)

    def setCompact(self, compact: bool):
        self.setIconSize(QSize(13, 13) if compact else QSize(16, 16))
        font = QFont("Microsoft YaHei UI", 8 if compact else 9, QFont.Weight.DemiBold)
        self.setFont(font)

    def _refresh_visual(self):
        self.setText(self._display_override or self._current)
        self.setIcon(self._icons[self._current])
        for value, action in self._actions.items():
            action.setChecked(value == self._current)


class StateCheckBox(QCheckBox):
    """Compact switch matching the visual language of the concept design."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.state_label = label
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(150 if "暂停" in label else 156)
        self.setFixedHeight(36)
        self._switch_progress = 0.0
        self._switch_animation = QPropertyAnimation(self, b"switchProgress", self)
        self._switch_animation.setDuration(190)
        self._switch_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_toggle)

    def _get_switch_progress(self):
        return self._switch_progress

    def _set_switch_progress(self, value):
        self._switch_progress = float(value)
        self.update()

    switchProgress = Property(float, _get_switch_progress, _set_switch_progress)

    def _animate_toggle(self, checked: bool):
        self._switch_animation.stop()
        self._switch_animation.setStartValue(self._switch_progress)
        self._switch_animation.setEndValue(1.0 if checked else 0.0)
        self._switch_animation.start()

    def paintEvent(self, _event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRect(3, 7, 38, 22)
        off_track, on_track = QColor(theme_color("track")), QColor(theme_color("accent"))
        amount = max(0.0, min(1.0, self._switch_progress))
        track_color = QColor(
            round(off_track.red() + (on_track.red() - off_track.red()) * amount),
            round(off_track.green() + (on_track.green() - off_track.green()) * amount),
            round(off_track.blue() + (on_track.blue() - off_track.blue()) * amount),
        )
        painter.setPen(QPen(track_color.darker(112), 1))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 11, 11)
        knob_x = round((track.left() + 3) + ((track.right() - 18) - (track.left() + 3)) * amount)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#FFFFFF" if application_theme_mode() == "light" else "#DDEAF5"))
        painter.drawEllipse(QRect(knob_x, track.top() + 3, 16, 16))
        painter.setPen(QColor(theme_color("text") if self.isEnabled() else theme_color("muted")))
        painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.DemiBold))
        painter.drawText(QRect(49, 0, self.width() - 49, self.height()), Qt.AlignmentFlag.AlignVCenter, self.state_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled() and self.rect().contains(event.position().toPoint()):
            self.setChecked(not self.isChecked())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ButtonMotion(QObject):
    """Small, non-layout-changing feedback shared by every push button."""

    def __init__(self, button: QPushButton):
        super().__init__(button)
        self.button = button
        self.effect = QGraphicsOpacityEffect(button)
        self.effect.setOpacity(1.0)
        button.setGraphicsEffect(self.effect)
        button.installEventFilter(self)
        self.animation = QPropertyAnimation(self.effect, b"opacity", self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def animate(self, value: float, duration: int):
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.setStartValue(self.effect.opacity())
        self.animation.setEndValue(value)
        self.animation.start()

    def eventFilter(self, watched, event):
        if watched is self.button and self.button.isEnabled():
            if event.type() == QEvent.Type.Enter:
                self.animate(0.96, 120)
            elif event.type() == QEvent.Type.Leave:
                self.animate(1.0, 150)
            elif event.type() == QEvent.Type.MouseButtonPress:
                self.animate(0.80, 70)
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self.animate(0.96 if self.button.underMouse() else 1.0, 120)
        return super().eventFilter(watched, event)


def install_button_motion(root: QWidget):
    for button in root.findChildren(QPushButton):
        if button.objectName() == "taskMore":
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            continue
        if not hasattr(button, "_motion_controller"):
            button._motion_controller = ButtonMotion(button)
        button.setCursor(Qt.CursorShape.PointingHandCursor)


class AnimatedDialog(QDialog):
    """Consistent subtle entrance motion for application dialogs."""

    native_titlebar_theme = "auto"

    def refresh_native_titlebar_theme(self):
        apply_windows_titlebar_theme(self, self.native_titlebar_theme)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh_native_titlebar_theme)
        install_button_motion(self)
        end = self.pos()
        self.setWindowOpacity(0.0)
        self.move(end + QPoint(0, 8))
        opacity = QPropertyAnimation(self, b"windowOpacity", self)
        opacity.setDuration(190); opacity.setStartValue(0.0); opacity.setEndValue(1.0)
        opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
        position = QPropertyAnimation(self, b"pos", self)
        position.setDuration(210); position.setStartValue(end + QPoint(0, 8)); position.setEndValue(end)
        position.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance = QParallelAnimationGroup(self)
        self._entrance.addAnimation(opacity); self._entrance.addAnimation(position); self._entrance.start()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            QTimer.singleShot(0, self.refresh_native_titlebar_theme)


class ThemedMessageBox(QMessageBox):
    """Native message box whose caption follows the application's visual system."""

    def showEvent(self, event):
        super().showEvent(event)
        mode = application_theme_mode()
        colors = THEMES[mode]
        self.setStyleSheet(
            f"QMessageBox{{background:{colors['panel']};color:{colors['text']};}}"
            f"QLabel{{color:{colors['text']};background:transparent;}}"
            f"QPushButton{{background:{colors['surface']};color:{colors['text']};border:1px solid {colors['border']};"
            "border-radius:8px;padding:7px 18px;min-width:72px;font-weight:700;}"
            f"QPushButton:hover{{background:{colors['surface_hover']};border-color:{colors['accent']};}}"
        )
        QTimer.singleShot(0, lambda: apply_windows_titlebar_theme(self, "auto"))


def show_themed_message(parent, icon, title, text, buttons=QMessageBox.StandardButton.Ok):
    box = ThemedMessageBox(icon, title, text, buttons, parent)
    box.setWindowIcon(parent.windowIcon() if parent is not None else QIcon(str(app_icon_path())))
    return QMessageBox.StandardButton(box.exec())


def acquire_single_instance_mutex(name: str = SINGLE_INSTANCE_MUTEX_NAME):
    """Hold a named Windows mutex for the full desktop-process lifetime."""
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("无法创建软件单实例锁。")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance_mutex(handle):
    if sys.platform == "win32" and handle:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def bundled_extension_source() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "extension"


def provision_extension(target: Path | None = None) -> tuple[Path, str]:
    source = bundled_extension_source()
    target = target or APP_DIR / "browser_helper"
    target.mkdir(parents=True, exist_ok=True)
    source_files = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
    for relative in source_files:
        src, dst = source / relative, target / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or src.read_bytes() != dst.read_bytes():
            shutil.copy2(src, dst)
    managed_files = source_files | {Path("app_icon.png")}
    for stale in [path for path in target.rglob("*") if path.is_file() and path.relative_to(target) not in managed_files]:
        stale.unlink()
    for stale_dir in sorted((path for path in target.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
        try:
            stale_dir.rmdir()
        except OSError:
            pass
    icon_source, icon_target = app_icon_path(), target / "app_icon.png"
    if not icon_target.exists() or icon_source.read_bytes() != icon_target.read_bytes():
        shutil.copy2(icon_source, icon_target)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != APP_VERSION:
        manifest["version"] = APP_VERSION
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target, APP_VERSION


def default_browser_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0]).lower()
    except OSError:
        prog_id = ""
    return "Edge" if "edge" in prog_id else "Chrome"


def installed_browsers() -> dict[str, tuple[str, str]]:
    definitions = {
        "Chrome": ("chrome.exe", r"Google\Chrome\Application\chrome.exe", "chrome://extensions/"),
        "Edge": ("msedge.exe", r"Microsoft\Edge\Application\msedge.exe", "edge://extensions/"),
    }
    found: dict[str, tuple[str, str]] = {}
    for name, (exe_name, relative, url) in definitions.items():
        paths: list[Path] = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}") as key:
                    paths.append(Path(str(winreg.QueryValueEx(key, "")[0])))
            except OSError:
                pass
        for root in (os.getenv("PROGRAMFILES", ""), os.getenv("PROGRAMFILES(X86)", ""), os.getenv("LOCALAPPDATA", "")):
            if root:
                paths.append(Path(root) / relative)
        executable = next((path for path in paths if path.is_file()), None)
        if executable:
            found[name] = (str(executable), url)
    return found


def detect_installed_helper_browsers(extension_dir: Path, roots: dict[str, Path] | None = None) -> set[str]:
    roots = roots or {
        "Chrome": Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
        "Edge": Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
    }
    expected = os.path.normcase(os.path.abspath(str(extension_dir)))
    detected: set[str] = set()
    for browser, root in roots.items():
        if not root.is_dir():
            continue
        profiles = [path for path in root.iterdir() if path.is_dir() and (path.name == "Default" or path.name.startswith("Profile "))]
        for profile in profiles:
            for filename in ("Secure Preferences", "Preferences"):
                preferences = profile / filename
                if not preferences.is_file():
                    continue
                try:
                    data = json.loads(preferences.read_text(encoding="utf-8"))
                    settings = data.get("extensions", {}).get("settings", {})
                    if any(
                        os.path.normcase(os.path.abspath(str(value.get("path", "")))) == expected
                        for value in settings.values() if isinstance(value, dict) and value.get("path")
                    ):
                        detected.add(browser)
                        break
                except (OSError, ValueError, TypeError):
                    continue
            if browser in detected:
                break
    return detected


def chromium_window_handles() -> list[int]:
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, _data):
        if not user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        if buffer.value == "Chrome_WidgetWin_1":
            handles.append(int(hwnd))
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return handles


def type_internal_browser_url(hwnd: int, url: str) -> bool:
    if sys.platform != "win32" or hwnd not in chromium_window_handles():
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.restype = ctypes.c_uint
    foreground = int(user32.GetForegroundWindow() or 0)
    foreground_thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), None) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    current_thread = kernel32.GetCurrentThreadId()
    attached: list[int] = []
    for thread_id in {foreground_thread, target_thread}:
        if thread_id and thread_id != current_thread and user32.AttachThreadInput(current_thread, thread_id, True):
            attached.append(thread_id)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.ShowWindow(ctypes.c_void_p(hwnd), 9)
    user32.BringWindowToTop(ctypes.c_void_p(hwnd))
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    user32.SetFocus(ctypes.c_void_p(hwnd))
    user32.keybd_event(0x12, 0, 0x0002, 0)
    time.sleep(0.12)
    focused = int(user32.GetForegroundWindow() or 0) == hwnd
    for thread_id in attached:
        user32.AttachThreadInput(current_thread, thread_id, False)
    if not focused: return False
    QApplication.clipboard().setText(url)
    key_up, control, key_l, key_v, enter = 0x0002, 0x11, 0x4C, 0x56, 0x0D
    pointer_size = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KeyboardInput(ctypes.Structure):
        _fields_ = [("vk", ctypes.c_ushort), ("scan", ctypes.c_ushort), ("flags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("extra", pointer_size)]

    class InputUnion(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("padding", ctypes.c_byte * 32)]

    class Input(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", ctypes.c_ulong), ("data", InputUnion)]

    def send(keys):
        events = (Input * len(keys))(*[Input(type=1, keyboard=KeyboardInput(key, 0, flags, 0, 0)) for key, flags in keys])
        return user32.SendInput(len(events), events, ctypes.sizeof(Input)) == len(events)

    if not send([(control, 0), (key_l, 0), (key_l, key_up), (control, key_up)]): return False
    time.sleep(0.08)
    if not send([(control, 0), (key_v, 0), (key_v, key_up), (control, key_up)]): return False
    time.sleep(0.08)
    if not send([(enter, 0), (enter, key_up)]): return False
    return True


class BrandHeader(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("brandHeader")
        self._brand_art = QPixmap(str(brand_sky_path()))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._brand_art.isNull():
            return
        layer = QPixmap(self.size()); layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        source = self._brand_art.rect()
        # Use only the upper sky/ray strip here; the portrait belongs in the app icon
        # and empty-list watermark, not behind the header controls.
        source.setHeight(max(1, round(source.height() * 0.14)))
        layer_painter.setOpacity(0.10 if application_theme_mode() == "dark" else 0.15)
        layer_painter.drawPixmap(self.rect(), self._brand_art, source)
        layer_painter.setOpacity(1.0)
        layer_painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        vertical = QLinearGradient(0, 0, 0, self.height())
        vertical.setColorAt(0.0, QColor(255, 255, 255, 235)); vertical.setColorAt(0.62, QColor(255, 255, 255, 180)); vertical.setColorAt(1.0, QColor(255, 255, 255, 0))
        layer_painter.fillRect(self.rect(), vertical)
        horizontal = QLinearGradient(0, 0, self.width(), 0)
        horizontal.setColorAt(0.0, QColor(255, 255, 255, 0)); horizontal.setColorAt(0.08, QColor(255, 255, 255, 255))
        horizontal.setColorAt(0.92, QColor(255, 255, 255, 255)); horizontal.setColorAt(1.0, QColor(255, 255, 255, 0))
        layer_painter.fillRect(self.rect(), horizontal); layer_painter.end()
        painter = QPainter(self); painter.drawPixmap(0, 0, layer)


class CapsuleScrollBar(QScrollBar):
    """Scrollbar with a genuinely rounded track and thumb on every Qt theme."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setFixedWidth(18)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(3, 2, -3, -2)
        radius = track.width() / 2
        dark = application_theme_mode() == "dark"
        painter.setPen(QPen(QColor("#40586D" if dark else "#A4B1BE"), 1))
        painter.setBrush(QColor("#203142" if dark else "#DDE5ED"))
        painter.drawRoundedRect(track, radius, radius)

        span = self.maximum() - self.minimum()
        if span <= 0 or track.height() <= 0:
            return
        total = span + max(1, self.pageStep())
        handle_height = min(track.height(), max(48, round(track.height() * self.pageStep() / total)))
        travel = max(0, track.height() - handle_height)
        offset = round(travel * (self.value() - self.minimum()) / span)
        handle = QRect(track.left() + 1, track.top() + offset + 1,
                       max(1, track.width() - 2), max(1, handle_height - 2))
        gradient = QLinearGradient(handle.left(), 0, handle.right(), 0)
        gradient.setColorAt(0.0, QColor("#65A9E8" if dark else "#79AADB"))
        gradient.setColorAt(1.0, QColor("#347FBC" if dark else "#3F78B5"))
        painter.setPen(QPen(QColor("#56738D" if dark else "#8799AB"), 1))
        painter.setBrush(gradient)
        handle_radius = handle.width() / 2
        painter.drawRoundedRect(handle, handle_radius, handle_radius)


class TaskListWidget(QListWidget):
    """Task list that doubles as the empty-state drop target."""

    blankClicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("taskList")
        self.setVerticalScrollBar(CapsuleScrollBar(Qt.Orientation.Vertical, self))
        self._brand_art = QPixmap(str(brand_sky_path()))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count():
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.viewport().rect().adjusted(14, 14, -14, -14)
        if not self._brand_art.isNull():
            scaled = self._brand_art.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                            Qt.TransformationMode.SmoothTransformation)
            x = max(0, (scaled.width() - area.width()) // 2)
            y = max(0, (scaled.height() - area.height()) // 2)
            watermark = scaled.copy(QRect(x, y, area.width(), area.height()))
            painter.setOpacity(0.045 if application_theme_mode() == "dark" else 0.075)
            painter.drawPixmap(area.topLeft(), watermark)
            painter.setOpacity(1.0)

        painter.setPen(QPen(QColor("#527BA5" if application_theme_mode() == "dark" else "#8EB6E6"), 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(area, 13, 13)

        center = area.center()
        painter.setPen(QPen(QColor(theme_color("accent")), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        cloud = QPainterPath(); cloud.moveTo(center.x() - 30, center.y() - 40)
        cloud.cubicTo(center.x() - 36, center.y() - 54, center.x() - 22, center.y() - 66, center.x() - 10, center.y() - 60)
        cloud.cubicTo(center.x() - 3, center.y() - 78, center.x() + 24, center.y() - 74, center.x() + 25, center.y() - 54)
        cloud.cubicTo(center.x() + 42, center.y() - 54, center.x() + 42, center.y() - 34, center.x() + 27, center.y() - 32)
        cloud.lineTo(center.x() - 26, center.y() - 32)
        cloud.cubicTo(center.x() - 42, center.y() - 32, center.x() - 43, center.y() - 51, center.x() - 30, center.y() - 40)
        painter.drawPath(cloud)
        painter.drawLine(center.x(), center.y() - 60, center.x(), center.y() - 24)
        painter.drawLine(center.x(), center.y() - 60, center.x() - 11, center.y() - 49)
        painter.drawLine(center.x(), center.y() - 60, center.x() + 11, center.y() - 49)

        painter.setPen(QColor(theme_color("text")))
        painter.setFont(QFont("Microsoft YaHei UI", 14, QFont.Weight.Bold))
        title_rect = area.adjusted(24, area.height() // 2 - 10, -24, -(area.height() // 2 - 42))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "拖入图片或文件夹")

        painter.setPen(QColor(theme_color("muted")))
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        note_rect = area.adjusted(24, area.height() // 2 + 28, -24, -(area.height() // 2 - 70))
        painter.drawText(note_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "支持一次加入数十或数百张图片 · 按文件名自然排序")

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.itemAt(event.position().toPoint()) is None):
            self.blankClicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class SelectAllCheckBox(QCheckBox):
    """Theme-independent select-all control with a visible box in both palettes."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("selectAll")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        return QSize(metrics.horizontalAdvance(self.text()) + 34, max(28, metrics.height() + 8))

    def paintEvent(self, _event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = application_theme_mode() == "dark"
        box = QRect(1, max(1, (self.height() - 18) // 2), 18, 18)
        if self.isChecked():
            border, fill = QColor("#2B78C7"), QColor(theme_color("accent"))
        elif self.underMouse():
            border = QColor("#65A9FF" if dark else "#3B83C7")
            fill = QColor("#203A53" if dark else "#E3F1FF")
        else:
            border = QColor("#6D8CA8" if dark else "#7897B7")
            fill = QColor("#101B26" if dark else "#FFFFFF")
        painter.setPen(QPen(border, 1.6)); painter.setBrush(fill)
        painter.drawRoundedRect(box, 4, 4)
        if self.isChecked():
            painter.setPen(QPen(QColor("#FFFFFF"), 2.1, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            path = QPainterPath(); path.moveTo(5, box.center().y()); path.lineTo(8.5, box.center().y() + 3.5); path.lineTo(15, box.center().y() - 4)
            painter.drawPath(path)
        painter.setPen(QColor(theme_color("text") if self.isEnabled() else theme_color("muted")))
        painter.setFont(self.font())
        painter.drawText(QRect(27, 0, max(0, self.width() - 27), self.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())

    def enterEvent(self, event):
        self.update(); super().enterEvent(event)

    def leaveEvent(self, event):
        self.update(); super().leaveEvent(event)


class HoverRemoveButton(QPushButton):
    """Precisely centered overlay close button for a task card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskRemoveHover")
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._leave_callback = None

    def paintEvent(self, _event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse(); pressed = self.isDown()
        dark = application_theme_mode() == "dark"
        fill = QColor("#B94353" if pressed else "#D85D6C" if hovered else "#172431" if dark else "#FFFFFF")
        stroke = QColor("#FFFFFF" if hovered or pressed else "#A9BAC9" if dark else "#8D9BA8")
        border = QColor("#A33646" if pressed else "#C64A5B" if hovered else "#40566B" if dark else "#D7E1EA")
        painter.setPen(QPen(border, 1)); painter.setBrush(fill)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        center = QPointF(self.width() / 2.0, self.height() / 2.0); arm = 3.5
        painter.setPen(QPen(stroke, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(center.x() - arm, center.y() - arm),
                         QPointF(center.x() + arm, center.y() + arm))
        painter.drawLine(QPointF(center.x() + arm, center.y() - arm),
                         QPointF(center.x() - arm, center.y() + arm))

    def leaveEvent(self, event):
        if self._leave_callback:
            QTimer.singleShot(80, self._leave_callback)
        super().leaveEvent(event)


class ResponsiveTaskParameters(QLabel):
    """Keep a task summary on one line when possible and balance it over two lines when narrow."""

    SEPARATOR = "  ·  "

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setObjectName("taskParams")
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_parameter_text(text)

    def set_parameter_text(self, text: str):
        self._full_text = text
        self.setToolTip(text)
        self._apply_responsive_text()

    def _responsive_text(self, available_width: int) -> str:
        parts = [part.strip() for part in self._full_text.split("·") if part.strip()]
        single_line = self.SEPARATOR.join(parts)
        if len(parts) < 2 or self.fontMetrics().horizontalAdvance(single_line) <= available_width:
            return single_line
        candidates = []
        for split_at in range(1, len(parts)):
            first = self.SEPARATOR.join(parts[:split_at])
            second = self.SEPARATOR.join(parts[split_at:])
            first_width = self.fontMetrics().horizontalAdvance(first)
            second_width = self.fontMetrics().horizontalAdvance(second)
            overflow = max(0, first_width - available_width) + max(0, second_width - available_width)
            candidates.append((overflow, max(first_width, second_width), abs(first_width - second_width), first, second))
        _, _, _, first, second = min(candidates)
        return f"{first}\n{second}"

    def _apply_responsive_text(self):
        display = self._responsive_text(max(1, self.contentsRect().width()))
        if self.text() != display:
            self.setText(display)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_text()


def task_selection_after_click(task_ids, selected_ids, anchor_id, target_id, modifiers):
    """Return checkbox-free desktop selection state for one task-card click."""
    ordered = list(task_ids)
    selected = set(selected_ids).intersection(ordered)
    if target_id not in ordered:
        return selected, anchor_id if anchor_id in ordered else None

    control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    if shift and anchor_id in ordered:
        anchor_index = ordered.index(anchor_id)
        target_index = ordered.index(target_id)
        start, end = sorted((anchor_index, target_index))
        clicked_range = set(ordered[start:end + 1])
        return (selected | clicked_range if control else clicked_range), anchor_id

    if control:
        if target_id in selected:
            selected.remove(target_id)
        else:
            selected.add(target_id)
        return selected, target_id

    return {target_id}, target_id


class TaskRowWidget(QFrame):
    """Rich task row whose card surface toggles multi-selection."""

    STATUS_COLORS = {
        "等待提交": ("#687A8F", "#EDF1F5"),
        "正在上传": ("#1769AA", "#DDEEFF"),
        "正在设置": ("#7657C8", "#EEE8FF"),
        "正在提交": ("#A66A0A", "#FFF0CC"),
        "已提交": ("#16865F", "#DDF4EB"),
        "提交失败": ("#B33E50", "#FFE4E8"),
        "需要人工处理": ("#B35E18", "#FFE9D8"),
    }
    DARK_STATUS_COLORS = {
        "等待提交": ("#B4C3D1", "#263545"),
        "正在上传": ("#77BAFF", "#173652"),
        "正在设置": ("#B7A1FF", "#30284F"),
        "正在提交": ("#F0C36B", "#42351F"),
        "已提交": ("#62D5A7", "#183D33"),
        "提交失败": ("#FF8995", "#48252D"),
        "需要人工处理": ("#F3A56F", "#482F20"),
    }

    def __init__(self, task, thumbnail: QIcon, parameters: str, checked: bool,
                 on_selection_request, on_preview, on_reveal, on_edit, on_restore, on_retry,
                 on_prioritize, on_copy_error, on_remove, on_select):
        super().__init__()
        self.setObjectName("taskRow")
        self._active = task.status in {"正在上传", "正在设置", "正在提交"}
        self._failed = task.status in {"提交失败", "需要人工处理"}
        self._status = task.status
        self._error = task.error
        self._selected = checked
        self._hover_progress = 0.0
        self._on_selection_request = on_selection_request
        self._on_select = on_select
        self._on_preview = on_preview
        self._on_reveal = on_reveal
        self._on_edit = on_edit
        self._on_restore = on_restore
        self._on_retry = on_retry
        self._on_prioritize = on_prioritize
        self._on_copy_error = on_copy_error
        self._on_remove = on_remove
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(160)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("单击单选；Ctrl+单击切换多选；Shift+单击连续选择")
        row = QHBoxLayout(self); row.setContentsMargins(13, 7, 9, 7); row.setSpacing(10)

        preview = QLabel(); preview.setObjectName("taskThumbnail"); preview.setFixedSize(72, 72)
        preview.setPixmap(thumbnail.pixmap(72, 72)); preview.setScaledContents(True)
        preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True); row.addWidget(preview)

        text_box = QVBoxLayout(); text_box.setSpacing(3); text_box.setContentsMargins(0, 2, 0, 2)
        self.name_label = QLabel(task.name); self.name_label.setObjectName("taskName"); self.name_label.setToolTip(task.path)
        self.params_label = ResponsiveTaskParameters(parameters)
        self.error_label = QLabel(task.error); self.error_label.setObjectName("taskError")
        self.error_label.setToolTip(task.error); self.error_label.setVisible(bool(task.error))
        for label in (self.name_label, self.params_label, self.error_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_box.addWidget(self.name_label); text_box.addWidget(self.params_label); text_box.addWidget(self.error_label)
        text_box.addStretch(); row.addLayout(text_box, 1)

        side = QHBoxLayout(); side.setSpacing(7); side.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel(); self.status_label.setObjectName("statusBadge")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.status_label.setFixedHeight(30)
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._update_status_badge(task.status)
        side.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.action_menu = QMenu(self)
        self._rebuild_action_menu()
        self.more_button = QPushButton("•••"); self.more_button.setObjectName("taskMore")
        self.more_button.setFixedSize(34, 30); self.more_button.setToolTip("更多任务操作")
        self.more_button.clicked.connect(self._open_action_menu)
        self.more_button.setVisible(True); side.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(side)
        self.remove_hover = HoverRemoveButton(self)
        self.remove_hover.setToolTip("从列表移除")
        self.remove_hover.setEnabled(not self._active)
        self.remove_hover.clicked.connect(lambda _checked=False: self._on_remove())
        self.remove_hover._leave_callback = self._hide_remove_if_outside
        self._overlay_viewport = None
        self._overlay_surface = None
        self.remove_hover.hide()
        install_button_motion(self)

    def _update_status_badge(self, status: str):
        colors = self.DARK_STATUS_COLORS if application_theme_mode() == "dark" else self.STATUS_COLORS
        foreground, background = colors.get(status, (theme_color("muted"), theme_color("panel_alt")))
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"color:{foreground};background:{background};border:1px solid {foreground};"
            "border-radius:9px;padding:4px 9px;font-size:9pt;font-weight:700;"
        )

    def refresh_theme(self):
        self._update_status_badge(self._status)
        self._rebuild_action_menu()
        self.update()
        if self.remove_hover is not None:
            self.remove_hover.update()

    def update_task_state(self, task, parameters: str, checked: bool, selected: bool):
        """Refresh a card in place so status changes never destroy its widgets."""
        self._active = task.status in {"正在上传", "正在设置", "正在提交"}
        self._failed = task.status in {"提交失败", "需要人工处理"}
        self._status = task.status
        self._error = task.error
        self.name_label.setText(task.name); self.name_label.setToolTip(task.path)
        self.params_label.set_parameter_text(parameters)
        self.error_label.setText(task.error); self.error_label.setToolTip(task.error)
        self.error_label.setVisible(bool(task.error))
        self._update_status_badge(task.status)
        self.remove_hover.setEnabled(not self._active)
        if self._active:
            self.remove_hover.hide()
        self._rebuild_action_menu()
        self.set_selected(selected)
        self.update()

    def _add_menu_action(self, kind, text, callback, color=ACCENT, enabled=True):
        action = QAction(premium_icon(kind, color, 22), text, self.action_menu)
        action.setEnabled(enabled)
        action.triggered.connect(lambda _checked=False, task_action=callback: task_action())
        self.action_menu.addAction(action)
        return action

    def _rebuild_action_menu(self):
        """Build only the operations that make sense for the task's current status."""
        self.action_menu.clear()
        self._add_menu_action("eye", "预览图片", self._on_preview)
        self._add_menu_action("folder_reveal", "在文件夹中显示", self._on_reveal, "#3F89B8")
        self.action_menu.addSeparator()
        self._add_menu_action("edit", "编辑此任务设置", self._on_edit, ACCENT, not self._active)
        self._add_menu_action("restore", "恢复此任务默认设置", self._on_restore, ACCENT, not self._active)
        self.action_menu.addSeparator()
        if self._status == "等待提交":
            self._add_menu_action("move_next", "设为下一个任务", self._on_prioritize, GOLD)
        elif self._status in {"提交失败", "需要人工处理"}:
            self._add_menu_action("retry_alert", "重试此任务", self._on_retry, GOLD)
            self._add_menu_action("copy", "复制错误信息", self._on_copy_error, "#6C7890", bool(self._error))
        elif self._status == "已提交":
            self._add_menu_action("resubmit", "重新提交", self._on_retry, GOLD)
        elif self._active:
            self._add_menu_action("resubmit", "重新提交", self._on_retry, GOLD, False)
        self.action_menu.addSeparator()
        self._add_menu_action("trash", "从列表移除", self._on_remove, "#C65A68", not self._active)

    def attach_remove_overlay(self, viewport, overlay_surface, scrollbar):
        """Place the close button above the list and its clipping viewport."""
        self._overlay_viewport = viewport
        self._overlay_surface = overlay_surface
        button = self.remove_hover
        button.setParent(overlay_surface)
        scrollbar.valueChanged.connect(self._position_remove_overlay)
        self._position_remove_overlay()

    def prepare_for_list_rebuild(self):
        """Hide out-of-row overlays before QListWidget removes this card."""
        self._hover_animation.stop()
        if self.remove_hover is not None:
            self.remove_hover.hide()
            self.remove_hover.setEnabled(False)
            self.remove_hover._leave_callback = None
            self.remove_hover.deleteLater()
            self.remove_hover = None

    def _position_remove_overlay(self, *_args):
        if self._overlay_viewport is None or self._overlay_surface is None or self.remove_hover is None:
            return
        corner = self.mapTo(self._overlay_surface, self.rect().topRight())
        self.remove_hover.move(
            corner.x() - self.remove_hover.width() + 9,
            corner.y() - 7,
        )
        viewport_top = self._overlay_viewport.mapTo(self._overlay_surface, QPoint(0, 0)).y()
        viewport_bottom = viewport_top + self._overlay_viewport.height()
        if corner.y() < viewport_top or corner.y() > viewport_bottom:
            self.remove_hover.hide()
        else:
            self.remove_hover.raise_()

    def _open_action_menu(self):
        self._rebuild_action_menu()
        self.action_menu.exec(self.more_button.mapToGlobal(QPoint(-160, self.more_button.height())))

    def _get_hover_progress(self):
        return self._hover_progress

    def _set_hover_progress(self, value):
        self._hover_progress = float(value)
        self.update()

    hoverProgress = Property(float, _get_hover_progress, _set_hover_progress)

    @staticmethod
    def _mix(first: QColor, second: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            round(first.red() + (second.red() - first.red()) * amount),
            round(first.green() + (second.green() - first.green()) * amount),
            round(first.blue() + (second.blue() - first.blue()) * amount),
            round(first.alpha() + (second.alpha() - first.alpha()) * amount),
        )

    def paintEvent(self, _event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = application_theme_mode() == "dark"
        if self._failed:
            base, accent = QColor(theme_color("failed")), QColor("#F06B78" if dark else "#D96B78")
        elif self._active:
            base, accent = QColor(theme_color("active")), QColor(theme_color("accent"))
        elif self._selected:
            base, accent = QColor(theme_color("selected")), QColor(theme_color("accent"))
        else:
            base, accent = QColor(theme_color("card")), QColor("#3A5369" if dark else "#D8E4EF")
        fill = self._mix(base, QColor(theme_color("card_hover")), self._hover_progress)
        card = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(theme_color("border")), 1)); painter.setBrush(fill)
        painter.drawRoundedRect(card, 13, 13)
        if self._selected:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(theme_color("accent")))
            painter.drawRoundedRect(QRect(card.left(), card.top() + 10, 4, max(8, card.height() - 20)), 2, 2)

    def animate_in(self):
        effect = QGraphicsOpacityEffect(self); effect.setOpacity(0.0); self.setGraphicsEffect(effect)
        self._entrance_animation = QPropertyAnimation(effect, b"opacity", self)
        self._entrance_animation.setDuration(210); self._entrance_animation.setStartValue(0.0); self._entrance_animation.setEndValue(1.0)
        self._entrance_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance_animation.finished.connect(lambda: self.setGraphicsEffect(None))
        self._entrance_animation.start()

    def enterEvent(self, event):
        self._position_remove_overlay()
        if not self._active and self.remove_hover is not None:
            self.remove_hover.show(); self.remove_hover.raise_()
        self._hover_animation.stop(); self._hover_animation.setStartValue(self._hover_progress); self._hover_animation.setEndValue(1.0); self._hover_animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        QTimer.singleShot(80, self._hide_remove_if_outside)
        self._hover_animation.stop(); self._hover_animation.setStartValue(self._hover_progress); self._hover_animation.setEndValue(0.0); self._hover_animation.start()
        super().leaveEvent(event)

    def _hide_remove_if_outside(self):
        if self.remove_hover is None:
            return
        cursor = QCursor.pos()
        over_row = self.rect().contains(self.mapFromGlobal(cursor))
        over_button = self.remove_hover.rect().contains(self.remove_hover.mapFromGlobal(cursor))
        if not over_row and not over_button:
            self.remove_hover.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_remove_overlay()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._position_remove_overlay()

    def contextMenuEvent(self, event):
        self._on_select()
        if not self._selected:
            self._on_selection_request(Qt.KeyboardModifier.ControlModifier)
        self._rebuild_action_menu()
        self.action_menu.exec(event.globalPos())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_select()
            self._on_selection_request(event.modifiers())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_preview()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def set_selected(self, selected: bool):
        self._selected = selected
        self.update()


class DragOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = application_theme_mode() == "dark"
        painter.fillRect(self.rect(), QColor(3, 11, 20, 205) if dark else QColor(12, 45, 83, 164))
        center = self.rect().center()
        card = self.rect().adjusted(max(24, self.width() // 2 - 260), max(24, self.height() // 2 - 105),
                                    -max(24, self.width() // 2 - 260), -max(24, self.height() // 2 - 105))
        painter.setBrush(QColor("#131F2B" if dark else "#FAFDFF"))
        painter.setPen(QPen(QColor(theme_color("accent")), 2))
        painter.drawRoundedRect(card, 24, 24)
        painter.setPen(QPen(QColor(theme_color("accent")), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center.x(), center.y() - 60, center.x(), center.y() - 20)
        painter.drawLine(center.x() - 16, center.y() - 36, center.x(), center.y() - 20)
        painter.drawLine(center.x() + 16, center.y() - 36, center.x(), center.y() - 20)
        painter.setPen(QColor(theme_color("text")))
        painter.setFont(QFont("Microsoft YaHei UI", 18, QFont.Weight.Bold))
        painter.drawText(card.adjusted(20, 82, -20, -55), Qt.AlignmentFlag.AlignCenter, "释放即可添加")
        painter.setPen(QColor(theme_color("muted")))
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.drawText(card.adjusted(20, 130, -20, -18), Qt.AlignmentFlag.AlignCenter, "支持图片和包含图片的文件夹")


class ImagePreviewDialog(AnimatedDialog):
    native_titlebar_theme = "auto"

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片预览")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(10)
        header = QHBoxLayout()
        title_box = QVBoxLayout(); title_box.setSpacing(2)
        title = QLabel(Path(image_path).name); title.setObjectName("previewTitle")
        reader = QImageReader(image_path); reader.setAutoTransform(True); original = reader.size()
        dimensions = QLabel(f"{original.width()} × {original.height()}" if original.isValid() else "图片预览")
        dimensions.setObjectName("previewMeta")
        title_box.addWidget(title); title_box.addWidget(dimensions); header.addLayout(title_box); header.addStretch()
        open_original = QPushButton("打开原图"); open_original.setIcon(premium_icon("image", "#BFD8F4", 22))
        open_original.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(image_path)))
        header.addWidget(open_original); layout.addLayout(header)
        image_label = QLabel(); image_label.setObjectName("previewImage")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        maximum = QSize(max(320, int(available.width() * 0.82) - 48),
                        max(240, int(available.height() * 0.82) - 120))
        image = read_preview_image(image_path, maximum)
        if image.isNull():
            image_label.setText("无法读取这张图片")
            image_label.setFixedSize(460, 300)
        else:
            pixmap = QPixmap.fromImage(image)
            image_label.setPixmap(pixmap)
            image_label.setFixedSize(pixmap.size())
        layout.addWidget(image_label, 1)
        close = QPushButton("关闭预览"); close.setObjectName("previewClose"); close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        target_width = min(int(available.width() * 0.86), max(360, image_label.width() + 32))
        target_height = min(int(available.height() * 0.86), max(280, image_label.height() + 106))
        self.resize(target_width, target_height)
        self.apply_preview_color_theme()

    def apply_preview_color_theme(self):
        """Keep the preview light today while remaining ready for global dark mode."""
        if application_theme_mode() == "dark":
            self.setStyleSheet("""
                QDialog { background:#0F1B2A; color:#EEF5FC; }
                QLabel#previewTitle { font:700 11pt 'Microsoft YaHei UI'; color:#F4F8FC; padding:2px; }
                QLabel#previewMeta { color:#91A7BF; font-size:9pt; padding:2px; }
                QLabel#previewImage { background:#0A121E; border:1px solid #25384D; border-radius:16px; padding:10px; }
                QPushButton { background:#18283A; border:1px solid #35516E; border-radius:10px; padding:8px 20px; color:#EAF3FC; font-weight:700; }
                QPushButton:hover { background:#223B55; border-color:#5E91C6; }
                QPushButton:pressed { background:#132235; }
            """)
        else:
            self.setStyleSheet(f"""
                QDialog {{ background:{BG}; color:{TEXT}; }}
                QLabel#previewTitle {{ font:700 11pt 'Microsoft YaHei UI'; color:{TEXT}; padding:2px; }}
                QLabel#previewMeta {{ color:{MUTED}; font-size:9pt; padding:2px; }}
                QLabel#previewImage {{ background:#FFFFFF; border:1px solid #D4E2EE; border-radius:16px; padding:10px; }}
                QPushButton {{ background:#FBFDFF; border:1px solid #C9DCEB; border-radius:10px; padding:8px 20px; color:{TEXT}; font-weight:700; }}
                QPushButton:hover {{ background:#E5F1FC; border-color:{ACCENT}; }}
                QPushButton:pressed {{ background:#DCEAF7; }}
            """)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            self.apply_preview_color_theme()


class ChangeLogDialog(AnimatedDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("版本更新日志")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(620, 520)
        layout = QVBoxLayout(self); layout.setContentsMargins(22, 20, 22, 18); layout.setSpacing(12)
        title = QLabel(f"{APP_NAME} · 更新日志"); title.setObjectName("changeLogTitle")
        subtitle = QLabel(f"当前版本：{DISPLAY_VERSION}"); subtitle.setObjectName("changeLogVersion")
        layout.addWidget(title); layout.addWidget(subtitle)
        content = QTextEdit(); content.setReadOnly(True); content.setObjectName("changeLogContent")
        content.setHtml(CHANGE_LOG_HTML)
        content.setVerticalScrollBar(CapsuleScrollBar(Qt.Orientation.Vertical, content))
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(content, 1)
        close = QPushButton("关闭"); close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.apply_color_theme()

    def apply_color_theme(self):
        colors = THEMES[application_theme_mode()]
        self.setStyleSheet(f"""
            QDialog {{ background:{colors['window']}; color:{colors['text']}; }}
            QLabel#changeLogTitle {{ color:{colors['text']}; font:800 16pt 'Microsoft YaHei UI'; }}
            QLabel#changeLogVersion {{ color:{colors['muted']}; font-weight:700; }}
            QTextEdit#changeLogContent {{ background:{colors['panel']}; border:1px solid {colors['border']}; border-radius:14px; padding:14px; color:{colors['text']}; font:10pt 'Microsoft YaHei UI'; }}
            QPushButton {{ background:{colors['surface']}; border:1px solid {colors['border']}; border-radius:10px; padding:8px 24px; color:{colors['text']}; font-weight:700; }}
            QPushButton:hover {{ background:{colors['surface_hover']}; border-color:{colors['accent']}; }}
        """)
        self.refresh_native_titlebar_theme()


class ExtensionGuideDialog(AnimatedDialog):
    def __init__(self, owner: "MainWindow", open_browser: bool = False):
        super().__init__(owner)
        self.owner = owner
        self.was_ready = owner.helper_connected() and owner.store.connected_helper_version == APP_VERSION
        owner.scan_installed_helpers(force=True)
        self.was_installed = bool(owner.installed_helper_browsers)
        self.setWindowTitle("安装 / 修复浏览器助手")
        self.setWindowIcon(owner.windowIcon())
        self.setModal(False)
        available = (owner.screen() or QApplication.primaryScreen()).availableGeometry()
        guide_width = min(980, max(720, int(available.width() * 0.82)))
        guide_height = min(860, max(620, int(available.height() * 0.86)))
        self.resize(guide_width, guide_height)
        self.setMinimumSize(700, 600)

        layout = QVBoxLayout(self); layout.setContentsMargins(22, 20, 22, 20); layout.setSpacing(13)
        title = QLabel("浏览器助手安装向导")
        title.setFont(QFont("Microsoft YaHei UI", 17, QFont.Weight.Bold)); layout.addWidget(title)
        intro = QLabel(f"助手文件已安全释放到软件固定目录，版本 {DISPLAY_VERSION}。首次安装仍需你在浏览器中确认一次。")
        intro.setWordWrap(True); intro.setObjectName("guideMuted"); layout.addWidget(intro)

        self.status = QLabel(); self.status.setObjectName("guideStatus"); self.status.setWordWrap(True); layout.addWidget(self.status)

        self.browser_row = QHBoxLayout(); layout.addLayout(self.browser_row)
        self.browsers = installed_browsers()
        ordered = sorted(self.browsers, key=lambda name: name != default_browser_name())
        for name in ordered:
            button = QPushButton(f"打开 {name} 扩展页面")
            button.clicked.connect(lambda _checked=False, browser_name=name: self.open_browser(browser_name))
            self.browser_row.addWidget(button)
        if not ordered:
            unavailable = QLabel("未检测到 Chrome 或 Edge，请先安装浏览器。")
            unavailable.setObjectName("guideError"); self.browser_row.addWidget(unavailable)
        self.launch_note = QLabel(""); self.launch_note.setWordWrap(True)
        self.launch_note.setObjectName("guideMuted"); layout.addWidget(self.launch_note)

        actions = QHBoxLayout()
        copy_button = QPushButton("复制扩展目录"); copy_button.clicked.connect(self.copy_path)
        folder_button = QPushButton("打开扩展目录"); folder_button.clicked.connect(self.open_folder)
        enlarge_button = QPushButton("放大教程图"); enlarge_button.clicked.connect(self.open_tutorial_preview)
        close_button = QPushButton("关闭"); close_button.clicked.connect(self.close)
        actions.addWidget(copy_button); actions.addWidget(folder_button); actions.addWidget(enlarge_button)
        actions.addStretch(); actions.addWidget(close_button)
        layout.addLayout(actions)

        important = QLabel("第 6 步重点：停留在 browser_helper，不要进入任何子文件夹。列表区域不显示文件是正常的，直接点击右下角“选择文件夹”。")
        important.setObjectName("guideImportant"); important.setWordWrap(True); layout.addWidget(important)

        tutorial = QPixmap(str(extension_tutorial_path()))
        tutorial_width = max(620, guide_width - 72)
        tutorial = tutorial.scaledToWidth(tutorial_width, Qt.TransformationMode.SmoothTransformation)
        tutorial_label = QLabel(); tutorial_label.setObjectName("tutorialImage")
        tutorial_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        tutorial_label.setPixmap(tutorial); tutorial_label.setFixedSize(tutorial.size())
        tutorial_scroll = QScrollArea(); tutorial_scroll.setObjectName("tutorialScroll")
        tutorial_scroll.setWidget(tutorial_label); tutorial_scroll.setWidgetResizable(False)
        tutorial_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        tutorial_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tutorial_scroll.setVerticalScrollBar(CapsuleScrollBar(Qt.Orientation.Vertical, tutorial_scroll))
        layout.addWidget(tutorial_scroll, 1)

        self.apply_color_theme()

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_status); self.timer.start(600)
        self.copy_path(); self.refresh_status()
        if open_browser and ordered:
            QTimer.singleShot(100, lambda: self.open_browser(ordered[0]))

    def apply_color_theme(self):
        colors = THEMES[application_theme_mode()]
        dark = application_theme_mode() == "dark"
        important_bg, important_text, important_border = (
            ("#3A3020", "#F3D285", "#68552D") if dark else ("#FFF8E7", "#805918", "#EAD79A")
        )
        self.setStyleSheet(f"""
            QDialog {{ background:{colors['window']}; color:{colors['text']}; }}
            QLabel {{ color:{colors['text']}; background:transparent; }}
            QLabel#guideMuted {{ color:{colors['muted']}; }}
            QLabel#guideError {{ color:{'#FF8995' if dark else '#C64A4A'}; }}
            QLabel#guideImportant {{ color:{important_text}; background:{important_bg}; border:1px solid {important_border}; border-radius:12px; padding:10px 13px; font-weight:700; }}
            QScrollArea#tutorialScroll, QLabel#tutorialImage {{ background:{colors['panel_alt']}; border:1px solid {colors['border']}; border-radius:14px; }}
            QLabel#tutorialImage {{ border:0; }}
            QPushButton {{ background:{colors['surface']}; border:1px solid {colors['border']}; border-radius:10px; padding:8px 14px; color:{colors['text']}; font-weight:700; }}
            QPushButton:hover {{ background:{colors['surface_hover']}; border-color:{colors['accent']}; }}
        """)
        self.refresh_native_titlebar_theme()

    def copy_path(self):
        QApplication.clipboard().setText(str(self.owner.extension_dir))

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.owner.extension_dir)))

    def open_tutorial_preview(self):
        ImagePreviewDialog(str(extension_tutorial_path()), self).exec()

    def open_browser(self, name: str):
        browser = self.browsers.get(name)
        if browser:
            before = set(chromium_window_handles())
            QApplication.clipboard().setText(browser[1])
            self.launch_note.setText(f"正在打开 {name} 扩展管理页…")
            subprocess.Popen([browser[0], "--new-window"])
            QTimer.singleShot(500, lambda: self.finish_browser_navigation(name, browser[1], before, 0))

    def finish_browser_navigation(self, name: str, url: str, before: set[int], attempt: int):
        current = chromium_window_handles()
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        foreground = int(user32.GetForegroundWindow() or 0)
        new_windows = [handle for handle in current if handle not in before]
        target = new_windows[-1] if new_windows else (foreground if foreground in current else 0)
        if target:
            if type_internal_browser_url(target, url):
                self.launch_note.setText(f"已打开 {name} 扩展管理页；扩展目录仍保留在软件内，可随时点击下方按钮复制。")
                QTimer.singleShot(700, self.copy_path)
                return
        if attempt < 11:
            QTimer.singleShot(250, lambda: self.finish_browser_navigation(name, url, before, attempt + 1))
            return
        QApplication.clipboard().setText(url)
        self.launch_note.setText(f"未能定位 {name} 窗口，管理页地址 {url} 已复制，请粘贴到浏览器地址栏。")
        self.launch_note.setStyleSheet("color:#B77C14")

    def refresh_status(self):
        self.owner.scan_installed_helpers()
        connected = self.owner.helper_connected()
        reported = self.owner.store.connected_helper_version
        browser = self.owner.store.connected_browser or "浏览器"
        if connected and reported == APP_VERSION:
            self.status.setText(f"● 已连接：{browser} 浏览器助手 {reported}")
            self.status.setStyleSheet("color:#16865F;font-weight:700;padding:8px")
            if not self.was_ready:
                self.timer.stop(); QTimer.singleShot(900, self.accept)
        elif connected:
            self.status.setText(f"● 检测到旧版助手 {reported or '未知版本'}，正在等待浏览器自动重载…")
            self.status.setStyleSheet("color:#B77C14;font-weight:700;padding:8px")
        elif self.owner.installed_helper_browsers:
            names = " / ".join(sorted(self.owner.installed_helper_browsers))
            self.status.setText(f"● 已检测到助手安装在 {names}；打开 Grok 页面后即可连接。")
            self.status.setStyleSheet("color:#16865F;font-weight:700;padding:8px")
            if not self.was_installed:
                self.timer.stop(); QTimer.singleShot(1100, self.accept)
        else:
            self.status.setText("● 尚未检测到助手：请完成下面三步。")
            self.status.setStyleSheet("color:#B77C14;font-weight:700;padding:8px")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {DISPLAY_VERSION}")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        QTimer.singleShot(0, self.apply_native_window_icon)
        self.resize(1180, 760)
        self.setMinimumSize(860, 700)
        self.setAcceptDrops(True)
        self._shutdown_in_progress = False
        self._shutdown_complete = False
        self.store = TaskStore()
        self.theme_preference = self.store.theme_mode
        app = QApplication.instance()
        if app is not None:
            app.setProperty("themePreference", self.theme_preference)
            app.setProperty("themeMode", resolved_theme_mode(self.theme_preference))
        self.extension_dir, self.store.helper_version = provision_extension()
        self.installed_helper_browsers: set[str] = set()
        self.last_helper_scan = 0.0
        self.scan_installed_helpers(force=True)
        self.bridge = Bridge(self.store)
        self.bridge.start()
        self.checked_task_ids: set[str] = set()
        self.selection_anchor_id: str | None = None
        self._known_task_ids: set[str] = set()
        self._task_render_signature = None
        self.task_feedback_until = 0.0
        self.build_ui()
        self.drag_overlay = DragOverlay(self)
        self.thumbnail_cache: dict[str, QIcon] = {}
        self.load_defaults()
        self.apply_color_theme()
        self.draft_defaults = self.collect_defaults()
        self.had_selection = False
        self.extension_guide: ExtensionGuideDialog | None = None
        self.refresh()
        self.connection_timer = QTimer(self); self.connection_timer.timeout.connect(self.refresh_connection); self.connection_timer.start(1500)
        self.ui_timer = QTimer(self); self.ui_timer.timeout.connect(self.refresh); self.ui_timer.start(700)
        QTimer.singleShot(900, self.offer_first_install)

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(22, 16, 22, 18); outer.setSpacing(13)
        header = BrandHeader(); header.setFixedHeight(64); self.header_panel = header
        header_stack = QVBoxLayout(header); header_stack.setContentsMargins(2, 5, 2, 5); header_stack.setSpacing(4)
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(10); self.header_primary_layout = head
        header_secondary = QWidget(); header_secondary.setObjectName("layoutRow")
        head_secondary = QHBoxLayout(header_secondary); head_secondary.setContentsMargins(0, 0, 0, 0); head_secondary.setSpacing(10)
        self.header_secondary_layout = head_secondary
        header_stack.addLayout(head); header_stack.addWidget(header_secondary); self.header_secondary_widget = header_secondary
        brand_icon = QLabel(); brand_icon.setFixedSize(52, 52)
        brand_icon.setPixmap(QPixmap(str(brand_sun_path())).scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio,
                                                                   Qt.TransformationMode.SmoothTransformation))
        brand = QLabel(APP_NAME); brand.setFont(QFont("Microsoft YaHei UI", 19, QFont.Weight.Bold))
        version = QLabel(DISPLAY_VERSION); version.setObjectName("version")
        self.change_log_button = QPushButton("更新日志")
        self.change_log_button.setObjectName("changeLogButton")
        self.change_log_button.setIcon(premium_icon("history", "#55779E", 22)); self.change_log_button.setIconSize(QSize(22, 22))
        self.change_log_button.setToolTip("查看软件各版本的更新内容")
        self.change_log_button.clicked.connect(self.show_change_log)
        self.theme_button = QPushButton("主题")
        self.theme_button.setObjectName("themeButton")
        self.theme_menu = QMenu(self.theme_button)
        self.theme_actions = {}
        for preference, label in (("light", "浅色"), ("dark", "深色"), ("system", "跟随 Windows")):
            action = self.theme_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, value=preference: self.set_theme_preference(value))
            self.theme_actions[preference] = action
        self.theme_button.setMenu(self.theme_menu)
        self.theme_button.setToolTip("切换浅色、深色或跟随 Windows")
        self.connection = QLabel("● 等待浏览器助手连接"); self.connection.setStyleSheet("color:#B77C14;font-weight:700")
        self.open_grok_button = QPushButton("打开 Grok"); self.open_grok_button.setObjectName("grokButton")
        self.open_grok_button.setIcon(premium_icon("grok_mark", TEXT, 22)); self.open_grok_button.setIconSize(QSize(22, 22))
        self.open_grok_button.setToolTip("打开 Grok Imagine 工作页面")
        self.open_grok_button.clicked.connect(self.open_grok)
        self.install_button = QPushButton("一键安装 / 修复浏览器助手"); self.install_button.setObjectName("installButton"); self.install_button.clicked.connect(self.install_extension)
        self.install_button.setIcon(premium_icon("puzzle_check", ACCENT, 24)); self.install_button.setIconSize(QSize(24, 24))
        self.header_brand_widgets = (brand_icon, brand, version, self.change_log_button)
        self.header_theme_spacer = QWidget(); self.header_theme_spacer.setObjectName("themeBalanceSpacer")
        head.addWidget(brand_icon); head.addWidget(brand); head.addWidget(version); head.addWidget(self.change_log_button); head.addWidget(self.theme_button)
        head.addStretch(); head.addWidget(self.connection); head.addWidget(self.open_grok_button); head.addWidget(self.install_button)
        outer.addWidget(header)

        body = QHBoxLayout(); body.setSpacing(14); outer.addLayout(body, 1); self.body_layout = body
        left = QFrame(); left.setObjectName("taskPanel"); ll = QVBoxLayout(left); ll.setContentsMargins(10, 10, 10, 10); ll.setSpacing(8)
        queue_head = QHBoxLayout(); queue_head.setContentsMargins(4, 0, 4, 0)
        queue_title = QLabel("任务队列"); queue_title.setObjectName("queueTitle")
        self.selection_count = QLabel("未选择任务"); self.selection_count.setObjectName("selectionCount")
        self.select_all = SelectAllCheckBox("全选任务")
        self.select_all.toggled.connect(self.toggle_select_all)
        queue_head.addWidget(queue_title); queue_head.addStretch(); queue_head.addWidget(self.selection_count); queue_head.addWidget(self.select_all)
        ll.addLayout(queue_head)
        task_controls = QVBoxLayout(); task_controls.setContentsMargins(0, 0, 0, 0); task_controls.setSpacing(6)
        buttons = QHBoxLayout(); buttons.setSpacing(7); self.task_primary_layout = buttons
        task_secondary = QWidget(); task_secondary.setObjectName("layoutRow")
        task_secondary_layout = QHBoxLayout(task_secondary); task_secondary_layout.setContentsMargins(0, 0, 0, 0); task_secondary_layout.setSpacing(10)
        self.task_secondary_layout = task_secondary_layout
        add_images = QPushButton("选择图片"); add_images.setObjectName("primaryAdd"); add_images.setIcon(premium_icon("image_add", "#FFFFFF")); add_images.clicked.connect(self.pick_images)
        add_folder = QPushButton("选择文件夹"); add_folder.setObjectName("addButton"); add_folder.setIcon(premium_icon("folder_add")); add_folder.clicked.connect(self.pick_folder)
        self.clear_on_complete = StateCheckBox("完成后清空列表"); self.clear_on_complete.setObjectName("clearComplete")
        self.clear_on_complete.setToolTip("整批任务全部成功提交后自动清空列表；如有失败则保留任务和错误信息。")
        remove = QPushButton("移除所选"); remove.setObjectName("removeButton"); remove.setIcon(premium_icon("trash_check", "#C65A68")); remove.clicked.connect(self.remove_selected)
        remove_all = QPushButton("移除所有"); remove_all.setObjectName("removeButton"); remove_all.setIcon(premium_icon("trash_all", "#C65A68")); remove_all.clicked.connect(self.remove_all)
        for button in (add_images, add_folder, remove, remove_all): button.setIconSize(QSize(24, 24))
        for b in (add_images, add_folder, self.clear_on_complete): buttons.addWidget(b)
        buttons.addStretch()
        for b in (remove, remove_all): buttons.addWidget(b)
        self.task_add_widgets = (add_images, add_folder, self.clear_on_complete)
        self.task_remove_buttons = (remove, remove_all)
        task_controls.addLayout(buttons); task_controls.addWidget(task_secondary); self.task_secondary_widget = task_secondary
        ll.addLayout(task_controls)
        self.list = TaskListWidget(); self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setIconSize(QSize(72, 72)); self.list.setSpacing(3); self.list.setWordWrap(True)
        self.list.blankClicked.connect(self.clear_task_selection); ll.addWidget(self.list, 1)
        body.addWidget(left, 5); self.task_panel = left

        right = QFrame(); right.setObjectName("settingsPanel"); right.setMinimumWidth(360)
        form = QVBoxLayout(right); form.setContentsMargins(13, 10, 13, 10); form.setSpacing(7); self.settings_form = form
        self.settings_title = icon_text_label(
            "批量默认设置", "sliders", ACCENT, 14, QFont.Weight.Bold,
            centered=True, text_object_name="settingsTitleText",
        )
        form.addWidget(self.settings_title)

        prompt_head = QHBoxLayout(); prompt_head.setContentsMargins(0, 0, 0, 0); prompt_head.setSpacing(8)
        prompt_head.addWidget(icon_text_label("提示词", "prompt_spark", "#7657C8"), 1)
        self.prompt_clear = QPushButton("一键清空"); self.prompt_clear.setObjectName("promptClear")
        self.prompt_clear.setIcon(premium_icon("eraser", "#B75870", 22)); self.prompt_clear.setIconSize(QSize(22, 22))
        self.prompt_clear.setToolTip("立即清空提示词输入框中的全部内容")
        self.prompt_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prompt_clear.clicked.connect(lambda: self.prompt.clear())
        prompt_head.addWidget(self.prompt_clear); form.addLayout(prompt_head)
        self.prompt = QTextEdit(); self.prompt.setPlaceholderText("为全部图片输入提示词…")
        self.prompt.setMinimumHeight(84); self.prompt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.prompt.textChanged.connect(self.update_prompt_clear_state); form.addWidget(self.prompt, 1)
        self.usage = SettingSelector("图片用途", ["首帧", "中间帧", "末帧", "循环", "参考"], "image_tag", "#5B86D6")
        self.audio = SettingSelector("音频", ["开启", "关闭"], "audio", "#3A9A83", {
            "开启": ("audio", "#3A9A83"), "关闭": ("audio_off", "#8496A8"),
        })
        self.resolution = SettingSelector("分辨率", ["480p", "720p", "1080p"], "monitor_focus", "#3F89B8")
        self.duration = SettingSelector("时长", ["6s", "10s", "15s"], "timer", "#D29A27")
        self.ratio = SettingSelector("比例", ["自动", "2:3", "3:2", "1:1", "9:16", "16:9"], "crop", "#6F6CCB")
        self.audio.setToolTip("控制 Grok 生成的视频是否附带音频。")
        self.ratio.setToolTip("选择“自动”后，上传时读取底图宽高，并匹配最接近的 Grok 视频比例。")
        self._first_frame_active = False
        self._ratio_before_first = "1:1"
        parameter_bar = QFrame(); parameter_bar.setObjectName("parameterBar"); parameter_bar.setFixedHeight(46)
        self.parameter_bar = parameter_bar
        parameter_layout = QHBoxLayout(parameter_bar); parameter_layout.setContentsMargins(5, 4, 5, 4); parameter_layout.setSpacing(0)
        self.setting_selectors = (self.usage, self.audio, self.resolution, self.duration, self.ratio)
        for index, widget in enumerate(self.setting_selectors):
            parameter_layout.addWidget(widget, 1)
            if index < len(self.setting_selectors) - 1:
                divider = QFrame(); divider.setObjectName("parameterDivider"); divider.setFixedSize(1, 22)
                parameter_layout.addWidget(divider)
        form.addWidget(parameter_bar)
        self.usage.currentTextChanged.connect(self.apply_rules)
        self.resolution.currentTextChanged.connect(self.apply_rules)
        self.ratio.currentTextChanged.connect(self.apply_rules)
        delay = QVBoxLayout(); delay.setSpacing(4)
        delay_head = QHBoxLayout(); delay_head.addWidget(icon_text_label("两次提交之间至少间隔", "interval", "#D29A27")); delay_head.addStretch()
        self.interval_value = QLabel(); self.interval_value.setObjectName("intervalValue"); delay_head.addWidget(self.interval_value)
        self.interval = QSlider(Qt.Orientation.Horizontal); self.interval.setRange(1, 5); self.interval.setSingleStep(1)
        self.interval.setTickInterval(1); self.interval.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.interval.valueChanged.connect(self.update_interval_label)
        delay_ends = QHBoxLayout(); delay_ends.addWidget(QLabel("1 秒", objectName="sliderEnd")); delay_ends.addStretch(); delay_ends.addWidget(QLabel("5 秒", objectName="sliderEnd"))
        delay.addLayout(delay_head); delay.addWidget(self.interval); delay.addLayout(delay_ends); form.addLayout(delay)
        note_text = "选中任务后可点击“应用到所选”，为单张或多张图片覆盖默认设置。"
        note = QLabel(note_text); self.settings_note = note
        note.setWordWrap(True); note.setToolTip(note_text); note.setStyleSheet(f"color:{MUTED}"); form.addWidget(note)
        apply_selected = QPushButton("应用到所选任务"); apply_selected.setObjectName("applyButton"); apply_selected.setIcon(premium_icon("apply", "#FFFFFF")); apply_selected.clicked.connect(self.apply_to_selected); form.addWidget(apply_selected)
        clear_override = QPushButton("所选任务恢复默认设置"); clear_override.setIcon(premium_icon("restore")); clear_override.clicked.connect(self.clear_overrides); form.addWidget(clear_override)
        body.addWidget(right, 3); self.settings_panel = right

        foot = QFrame(); foot.setObjectName("commandBar"); self.footer_panel = foot
        footer_stack = QVBoxLayout(foot); footer_stack.setContentsMargins(13, 8, 13, 8); footer_stack.setSpacing(6)
        fl = QHBoxLayout(); fl.setContentsMargins(0, 0, 0, 0); fl.setSpacing(10); self.footer_primary_layout = fl
        footer_secondary = QWidget(); footer_secondary.setObjectName("layoutRow")
        footer_actions = QHBoxLayout(footer_secondary); footer_actions.setContentsMargins(0, 0, 0, 0); footer_actions.setSpacing(10)
        self.footer_secondary_layout = footer_actions
        footer_stack.addLayout(fl); footer_stack.addWidget(footer_secondary); self.footer_secondary_widget = footer_secondary
        def metric(title_text):
            card = QFrame(); card.setObjectName("metricCard"); box = QVBoxLayout(card); box.setContentsMargins(9, 4, 9, 4); box.setSpacing(0)
            title_label = QLabel(title_text); title_label.setObjectName("metricTitle")
            value_label = QLabel("0"); value_label.setObjectName("metricValue")
            box.addWidget(title_label); box.addWidget(value_label); return card, value_label
        total_card, self.total_value = metric("总任务")
        waiting_card, self.waiting_value = metric("等待中")
        submitted_card, self.submitted_value = metric("已提交")
        fl.addWidget(total_card); fl.addWidget(waiting_card); fl.addWidget(submitted_card)
        progress_box = QFrame(); progress_box.setObjectName("progressBox")
        progress_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.progress_box = progress_box
        progress_layout = QVBoxLayout(progress_box); progress_layout.setContentsMargins(5, 3, 5, 3); progress_layout.setSpacing(5)
        self.progress_caption = QLabel("整体进度  0 / 0"); self.progress_caption.setObjectName("progressCaption")
        self.batch_progress = QProgressBar(); self.batch_progress.setObjectName("batchProgress")
        self.batch_progress.setRange(0, 100); self.batch_progress.setValue(0); self.batch_progress.setTextVisible(False)
        self.batch_progress.setFixedHeight(6); progress_layout.addWidget(self.progress_caption); progress_layout.addWidget(self.batch_progress); fl.addWidget(progress_box)
        self.footer_status = QLabel("●  准备就绪"); self.footer_status.setObjectName("footerStatus")
        self.footer_status.setToolTip("自动运行期间，请勿在目标 Grok 标签页手动上传、提交、删除或刷新任务。")
        fl.addWidget(self.footer_status, 1)
        self.footer_info_widgets = (total_card, waiting_card, submitted_card, progress_box, self.footer_status)
        self.summary = QLabel(); self.summary.hide()
        self.retry = QPushButton("重试失败"); self.retry.setIcon(premium_icon("retry_alert", GOLD)); self.retry.clicked.connect(self.retry_failed)
        self.resubmit = QPushButton("所选重新提交"); self.resubmit.setIcon(premium_icon("resubmit", ACCENT)); self.resubmit.clicked.connect(self.resubmit_selected)
        self.auto_pause = StateCheckBox("完成列表后暂停"); self.auto_pause.setObjectName("autoPause")
        self.auto_pause.setToolTip("开启：列表中的任务全部处理完后自动暂停。\n关闭：保持持续运行，新加入的图片也会继续自动提交。")
        self.start = QPushButton("开始批量提交"); self.start.setIcon(premium_icon("send", TEXT)); self.start.setObjectName("primary"); self.start.clicked.connect(self.toggle_run)
        fl.addWidget(self.retry); fl.addWidget(self.resubmit); fl.addWidget(self.auto_pause); fl.addWidget(self.start)
        self.footer_action_widgets = (self.retry, self.resubmit, self.auto_pause, self.start)
        outer.addWidget(foot)
        self.setStyleSheet(f"""
            QWidget {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #F6F9FC,stop:0.48 #F1F6FB,stop:1 #EAF2F8); color:{TEXT}; font:10pt 'Microsoft YaHei UI'; }}
            QLabel {{ background:transparent; font-weight:500; }}
            QLabel#version {{ color:#16365F; background:transparent; border:none; padding:0 2px; font-weight:700; }}
            QWidget#iconText {{ background:transparent; }}
            QLabel#settingsTitleText {{ color:#315779; font:800 12pt 'Microsoft YaHei UI'; }}
            QWidget#layoutRow, QWidget#themeBalanceSpacer {{ background:transparent; border:0; }}
            QFrame#brandHeader {{ background:transparent; border:0; border-radius:0; }}
            QFrame#taskPanel {{ background:rgba(251,253,255,242); border:1px solid #DCE7F1; border-radius:18px; }}
            QFrame#settingsPanel {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(251,253,255,247),stop:1 rgba(240,246,251,247)); border:1px solid #DCE7F1; border-radius:18px; }}
            QFrame#parameterBar {{ background:#EEF5FB; border:1px solid #D4E2EE; border-radius:12px; }}
            QFrame#parameterDivider {{ background:#D6E2EC; border:0; }}
            QPushButton#settingSelector {{ background:transparent; border:0; border-radius:8px; padding:4px 1px; text-align:center; font-weight:700; }}
            QPushButton#settingSelector:hover {{ background:#DDEDFC; color:#175E9E; }}
            QPushButton#settingSelector:pressed {{ background:#CFE5F8; color:#145E9F; }}
            QPushButton#settingSelector:disabled {{ color:#8496A8; background:rgba(226,234,241,150); }}
            QPushButton#settingSelector::menu-indicator {{ image:none; width:0; }}
            QFrame#commandBar {{ background:rgba(251,253,255,246); border:1px solid #DCE7F1; border-radius:17px; }}
            QFrame#metricCard {{ background:transparent; border:0; border-right:1px solid #DCE7F1; min-width:48px; }}
            QLabel#metricTitle {{ color:{MUTED}; font-size:8pt; font-weight:600; }}
            QLabel#metricValue {{ color:{TEXT}; font-size:14pt; font-weight:800; }}
            QFrame#progressBox {{ background:transparent; border:0; min-width:105px; }}
            QLabel#progressCaption {{ color:#456581; font-size:8pt; font-weight:700; }}
            QLabel#footerStatus {{ color:#41647F; background:#EEF5FB; border:0; border-radius:11px; padding:9px 11px; font-size:9pt; font-weight:600; }}
            QPushButton, QTextEdit {{ background:#F7FAFD; border:1px solid #D4E1EC; border-radius:10px; padding:8px; color:{TEXT}; font-weight:600; }}
            QPushButton:hover {{ background:#E8F3FD; border-color:#78A9DC; color:#175E9E; }}
            QPushButton:pressed {{ background:#D8EAF9; border-color:#4B8BCB; color:#0E4F89; }}
            QPushButton#primaryAdd {{ color:#FFFFFF; background:#347FDC; border-color:#2A70BE; font-weight:700; }}
            QPushButton#primaryAdd:hover {{ color:#FFFFFF; background:#428DE5; border-color:#1E64AD; }}
            QPushButton#addButton {{ color:#1769AA; }}
            QPushButton#removeButton {{ color:#6B7B8D; background:#F7FAFD; border-color:#D4E1EC; }}
            QPushButton#removeButton:hover {{ color:#A33245; background:#FFECEF; border-color:#D77C8A; }}
            QPushButton#installButton {{ background:#F6FAFE; border-color:#C7D9E9; }}
            QPushButton#installButton:hover {{ background:#E6F1FF; border-color:#5796D5; }}
            QPushButton#grokButton {{ color:#1769AA; background:#F6FAFE; border-color:#AFCBE7; padding:7px 11px; }}
            QPushButton#grokButton:hover {{ color:#FFFFFF; background:#347FDC; border-color:#286DB8; }}
            QPushButton#grokButton:pressed {{ color:#FFFFFF; background:#286DB8; border-color:#205E9F; }}
            QPushButton#applyButton {{ color:#FFFFFF; background:#347FDC; border-color:#2A70BE; font-weight:700; }}
            QPushButton#applyButton:hover {{ color:#FFFFFF; background:#428DE5; border-color:#1E64AD; }}
            QPushButton#applyButton:pressed {{ color:#FFFFFF; background:#286DB8; border-color:#205E9F; }}
            QPushButton#promptClear {{ color:#9B4860; background:#FFF9FB; border:1px solid #DFC1CA; border-radius:9px; padding:5px 10px; font-size:9pt; }}
            QPushButton#promptClear:hover {{ color:#FFFFFF; background:#C8657E; border-color:#B35069; }}
            QPushButton#promptClear:pressed {{ color:#FFFFFF; background:#A94760; border-color:#96394F; }}
            QPushButton#promptClear:disabled {{ color:#9BA7B3; background:#EFF2F5; border-color:#D3D9DF; }}
            QPushButton#changeLogButton, QPushButton#themeButton {{ color:#55779E; background:rgba(255,255,255,150); border:1px solid #B7CDE3; border-radius:9px; padding:5px 9px; font-size:9pt; }}
            QPushButton#changeLogButton:hover, QPushButton#themeButton:hover {{ color:#1769AA; background:#FFFFFF; border-color:#6E9ED0; }}
            QTextEdit:focus, QPushButton#settingSelector:focus {{ border:1px solid {ACCENT}; }}
            QCheckBox#autoPause, QCheckBox#clearComplete {{ background:transparent; border:0; padding:0; }}
            QCheckBox#autoPause::indicator, QCheckBox#clearComplete::indicator {{ width:0; height:0; }}
            QLabel#summaryBadge {{ color:#315779; background:#EDF4FA; border:0; border-radius:9px; padding:7px 10px; font-weight:700; }}
            QLabel#intervalValue {{ color:#B77C14; font-weight:700; }}
            QLabel#sliderEnd {{ color:{MUTED}; font-size:9pt; }}
            QLabel#queueTitle {{ color:#315779; font-weight:800; padding:2px 3px; }}
            QLabel#selectionCount {{ color:{MUTED}; font-size:9pt; padding-right:8px; }}
            QCheckBox#selectAll {{ color:#315779; font-weight:700; spacing:6px; }}
            QCheckBox#selectAll::indicator {{ width:17px; height:17px; }}
            QSlider {{ background:transparent; border:0; }}
            QSlider::groove:horizontal {{ height:6px; background:#D7E3F0; border:0; border-radius:3px; }}
            QSlider::sub-page:horizontal {{ background:{ACCENT}; border-radius:3px; }}
            QSlider::handle:horizontal {{ width:18px; margin:-6px 0; background:#FFF7DA; border:2px solid {GOLD}; border-radius:9px; }}
            QProgressBar#batchProgress {{ background:#DCE7F1; border:0; border-radius:2px; }}
            QProgressBar#batchProgress::chunk {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #347FDC,stop:0.72 #6EA8E5,stop:1 #D8A83E); border-radius:2px; }}
            QPushButton#primary {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #F4D57B,stop:1 #DDAE42); color:#17365D; border:1px solid #C99A31; font-weight:800; padding:10px 18px; border-radius:11px; }}
            QPushButton#primary:hover {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #F9DF92,stop:1 #E8BD58); border-color:#B9851F; }}
            QPushButton#primary:pressed {{ background:#D8A43A; border-color:#A97718; color:#17365D; }}
            QListWidget {{ background:#EDF4FB; border:0; border-radius:10px; padding:5px; }}
            QListWidget#taskList {{ background:#EDF4FA; border:0; border-radius:14px; padding:5px; outline:0; }}
            QListWidget::item {{ padding:0; margin:2px 1px; border:0; min-height:90px; }}
            QListWidget::item:selected {{ background:transparent; border:0; }}
            QListWidget::item:focus {{ outline:0; border:0; }}
            QFrame#taskRow {{ background:transparent; border:0; }}
            QLabel#taskThumbnail {{ background:transparent; border:0; }}
            QLabel#taskName {{ color:{TEXT}; font-weight:700; font-size:10pt; }}
            QLabel#taskParams {{ color:#6684A8; font-size:9pt; }}
            QLabel#taskError {{ color:#B33E50; font-size:9pt; }}
            QWidget#taskActions {{ background:transparent; }}
            QPushButton#taskAction {{ background:#F9FCFF; border:1px solid #D7E3EE; border-radius:8px; padding:3px; }}
            QPushButton#taskAction:hover {{ background:#E1F0FF; border-color:#5D99D5; }}
            QPushButton#taskMore {{ color:#37698F; background:transparent; border:0; border-radius:9px; padding:0; font-weight:900; font-size:13pt; }}
            QPushButton#taskMore:hover {{ color:#1769AA; background:#DDEDFC; }}
            QMenu {{ background:#FBFDFF; color:{TEXT}; border:1px solid #D4E1EC; border-radius:10px; padding:6px; }}
            QMenu::item {{ background:transparent; border-radius:7px; padding:7px 22px 7px 8px; }}
            QMenu::item:selected {{ background:#E5F1FC; color:#145E9F; }}
            QScrollBar:vertical {{ background:transparent; width:18px; margin:0; border:0; }}
            QScrollBar::handle:vertical {{ background:transparent; min-height:48px; }}
            QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{ height:0; border:0; background:transparent; }}
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{ width:0; height:0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
            QToolTip {{ background:#F9FCFF; color:{TEXT}; border:1px solid #AFC7E1; padding:5px; }}
        """)
        self._light_stylesheet = self.styleSheet()
        install_button_motion(self)
        for checkbox in self.findChildren(QCheckBox):
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self._half_screen_layout = None
        self.apply_half_screen_layout(self.width())
        self.apply_settings_density(self.width(), self.height())
        self.update_prompt_clear_state()

    def set_theme_preference(self, preference: str):
        if preference not in {"light", "dark", "system"}:
            return
        self.theme_preference = preference
        self.store.theme_mode = preference
        self.store.save()
        app = QApplication.instance()
        if app is not None:
            app.setProperty("themePreference", preference)
            app.setProperty("themeMode", resolved_theme_mode(preference))
        self.apply_color_theme()

    def apply_color_theme(self):
        mode = resolved_theme_mode(getattr(self, "theme_preference", "light"))
        app = QApplication.instance()
        if app is not None:
            app.setProperty("themePreference", getattr(self, "theme_preference", "light"))
            app.setProperty("themeMode", mode)
        colors = THEMES[mode]
        dark_overrides = """
            QWidget { background:#0D1621; color:#E8F1FA; }
            QLabel { background:transparent; color:#E8F1FA; }
            QLabel#version { color:#D9E9F7; }
            QWidget#iconText, QWidget#layoutRow, QWidget#themeBalanceSpacer, QFrame#brandHeader { background:transparent; border:0; }
            QLabel#settingsTitleText, QLabel#queueTitle { color:#CFE4F7; }
            QLabel#selectionCount, QLabel#metricTitle, QLabel#sliderEnd { color:#91A7BB; }
            QCheckBox#selectAll { color:#CFE4F7; }
            QFrame#taskPanel, QFrame#settingsPanel, QFrame#commandBar { background:#131F2B; border:1px solid #2B4053; }
            QFrame#parameterBar { background:#182737; border:1px solid #31495F; }
            QFrame#parameterDivider { background:#344B60; }
            QFrame#metricCard { background:transparent; border:0; border-right:1px solid #2B4053; }
            QLabel#metricValue { color:#E8F1FA; }
            QLabel#progressCaption { color:#AFC3D5; }
            QPushButton, QTextEdit { background:#101B26; border:1px solid #2B4053; color:#E8F1FA; }
            QPushButton:hover { background:#223B55; border-color:#4C9AFF; color:#F2F7FC; }
            QPushButton:pressed { background:#172B3E; border-color:#3B82D1; color:#E8F1FA; }
            QPushButton:disabled { background:#16222D; border-color:#293B4C; color:#667C90; }
            QPushButton#settingSelector { background:transparent; color:#D9E8F5; }
            QPushButton#settingSelector:hover { background:#213A52; color:#78B8FF; }
            QPushButton#settingSelector:pressed { background:#192F44; color:#78B8FF; }
            QPushButton#settingSelector:disabled { color:#667C90; background:#172431; }
            QPushButton#primaryAdd, QPushButton#applyButton { color:#FFFFFF; background:#347FDC; border-color:#4C9AFF; }
            QPushButton#primaryAdd:hover, QPushButton#applyButton:hover { color:#FFFFFF; background:#428DE5; }
            QPushButton#addButton, QPushButton#grokButton { color:#78B8FF; }
            QPushButton#removeButton { color:#AEBECB; background:#101B26; border-color:#2B4053; }
            QPushButton#removeButton:hover { color:#FF9CAA; background:#3A2029; border-color:#874653; }
            QPushButton#installButton, QPushButton#grokButton { background:#182737; border-color:#35516E; }
            QPushButton#installButton:hover, QPushButton#grokButton:hover { background:#223B55; border-color:#5E91C6; color:#FFFFFF; }
            QPushButton#promptClear { color:#F08CA4; background:#291A23; border-color:#633746; }
            QPushButton#promptClear:hover { color:#FFFFFF; background:#A94760; border-color:#C8657E; }
            QPushButton#promptClear:disabled { color:#667C90; background:#16222D; border-color:#293B4C; }
            QPushButton#changeLogButton, QPushButton#themeButton { color:#BFD5E8; background:#182737; border:1px solid #35516E; }
            QPushButton#changeLogButton:hover, QPushButton#themeButton:hover { color:#FFFFFF; background:#223B55; border-color:#5E91C6; }
            QLabel#summaryBadge, QLabel#footerStatus { color:#BFD5E8; background:#182737; }
            QLabel#intervalValue { color:#F0C36B; }
            QSlider { background:transparent; border:0; }
            QSlider::groove:horizontal { background:#39536A; border:0; }
            QSlider::sub-page:horizontal { background:#4C9AFF; }
            QSlider::handle:horizontal { background:#F6E9B9; border-color:#D8A83E; }
            QProgressBar#batchProgress { background:#2A3C4E; }
            QListWidget, QListWidget#taskList { background:#101B26; border:0; }
            QLabel#taskName { color:#E8F1FA; }
            QLabel#taskParams { color:#91A7BB; }
            QLabel#taskError { color:#FF8995; }
            QPushButton#taskAction { background:#172431; border-color:#31485C; }
            QPushButton#taskAction:hover { background:#223B55; border-color:#5E91C6; }
            QPushButton#taskMore { color:#8CB9DF; background:transparent; }
            QPushButton#taskMore:hover { color:#B9DCFF; background:#223B55; }
            QMenu { background:#131F2B; color:#E8F1FA; border:1px solid #344B60; }
            QMenu::item:selected { background:#223B55; color:#FFFFFF; }
            QMenu::item:disabled { color:#667C90; }
            QMenu::separator { height:1px; background:#2B4053; margin:4px 8px; }
            QToolTip { background:#182737; color:#E8F1FA; border:1px solid #45647E; }
        """
        self.setStyleSheet(self._light_stylesheet + (dark_overrides if mode == "dark" else ""))
        self.settings_note.setStyleSheet(f"color:{colors['muted']}")
        preference_labels = {"light": "浅色", "dark": "深色", "system": "跟随 Windows"}
        for value, action in self.theme_actions.items():
            action.setChecked(value == self.theme_preference)
        self.theme_button.setIcon(premium_icon("moon" if mode == "dark" else "sun", colors["text"], 21))
        self.theme_button.setIconSize(QSize(21, 21))
        self.theme_button.setText("主题")
        self.theme_button.setToolTip(f"当前：{preference_labels[self.theme_preference]}（实际为{'深色' if mode == 'dark' else '浅色'}）")
        self.change_log_button.setIcon(premium_icon("history", colors["muted"], 22))
        self.open_grok_button.setIcon(premium_icon("grok_mark", colors["text"], 22))
        self.install_button.setIcon(premium_icon("puzzle_check", colors["accent"], 24))
        self.refresh()
        for row in self.list.findChildren(TaskRowWidget):
            row.refresh_theme()
        self.centralWidget().update()
        self.list.viewport().update()
        if hasattr(self, "drag_overlay"):
            self.drag_overlay.update()
        apply_windows_titlebar_theme(self, "auto")
        for widget in QApplication.topLevelWidgets():
            if widget is self:
                continue
            if hasattr(widget, "apply_preview_color_theme"):
                widget.apply_preview_color_theme()
            elif hasattr(widget, "apply_color_theme"):
                widget.apply_color_theme()
            apply_windows_titlebar_theme(widget, "auto")

    @staticmethod
    def clear_layout_items(layout):
        while layout.count():
            layout.takeAt(0)

    @staticmethod
    def add_centered_group(layout, widgets):
        layout.addStretch(1)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)

    def rebuild_header_layout(self, compact: bool):
        """Build a centered two-row compact header or restore the wide header."""
        first_row = self.header_brand_widgets + (self.open_grok_button, self.install_button)
        all_widgets = first_row + (self.connection, self.theme_button, self.header_theme_spacer)
        layouts = (self.header_primary_layout, self.header_secondary_layout)
        for widget in all_widgets:
            for layout in layouts:
                layout.removeWidget(widget)
        for layout in layouts:
            self.clear_layout_items(layout)
        if compact:
            self.add_centered_group(self.header_primary_layout, first_row)
            self.header_theme_spacer.setFixedWidth(self.theme_button.sizeHint().width())
            self.add_centered_group(
                self.header_secondary_layout,
                (self.header_theme_spacer, self.connection, self.theme_button),
            )
        else:
            for widget in self.header_brand_widgets:
                self.header_primary_layout.addWidget(widget)
            self.header_primary_layout.addWidget(self.theme_button)
            self.header_primary_layout.addStretch(1)
            self.header_primary_layout.addWidget(self.connection)
            self.header_primary_layout.addWidget(self.open_grok_button)
            self.header_primary_layout.addWidget(self.install_button)
            self.header_primary_layout.setSpacing(10)

    def rebuild_task_layout(self, compact: bool):
        """Center both wrapped task rows or restore the wide split toolbar."""
        layouts = (self.task_primary_layout, self.task_secondary_layout)
        for widget in self.task_add_widgets + self.task_remove_buttons:
            for layout in layouts:
                layout.removeWidget(widget)
        for layout in layouts:
            self.clear_layout_items(layout)
        if compact:
            self.add_centered_group(self.task_primary_layout, self.task_add_widgets)
            self.add_centered_group(self.task_secondary_layout, self.task_remove_buttons)
        else:
            for widget in self.task_add_widgets:
                self.task_primary_layout.addWidget(widget)
            self.task_primary_layout.addStretch(1)
            for widget in self.task_remove_buttons:
                self.task_primary_layout.addWidget(widget)
            self.task_primary_layout.setSpacing(7)

    def rebuild_footer_layout(self, compact: bool):
        """Center the compact information and action rows or restore the wide command bar."""
        layouts = (self.footer_primary_layout, self.footer_secondary_layout)
        for widget in self.footer_info_widgets + self.footer_action_widgets:
            for layout in layouts:
                layout.removeWidget(widget)
        for layout in layouts:
            self.clear_layout_items(layout)
        if compact:
            for widget in self.footer_info_widgets[:3]:
                self.footer_primary_layout.addWidget(widget)
            self.footer_primary_layout.addWidget(self.progress_box, 1)
            self.footer_primary_layout.addWidget(self.footer_status)
            self.add_centered_group(self.footer_secondary_layout, self.footer_action_widgets)
        else:
            for widget in self.footer_info_widgets[:-1]:
                self.footer_primary_layout.addWidget(widget)
            self.footer_primary_layout.addWidget(self.footer_status, 1)
            for widget in self.footer_action_widgets:
                self.footer_primary_layout.addWidget(widget)
            self.footer_primary_layout.setSpacing(10)

    def update_compact_group_spacing(self, width: int):
        """Increase intra-group breathing room without disturbing symmetric outer space."""
        spacing = max(8, min(24, 8 + round((width - 860) * 16 / 190)))
        header_spacing = max(6, min(18, 6 + round((width - 860) * 12 / 190)))
        for layout in (self.header_primary_layout, self.header_secondary_layout):
            layout.setSpacing(header_spacing)
        for layout in (self.task_primary_layout, self.task_secondary_layout, self.footer_primary_layout, self.footer_secondary_layout):
            layout.setSpacing(spacing)

    def apply_half_screen_layout(self, width: int):
        """Reflow only the crowded single-line toolbars for Windows half-screen use."""
        compact = width < 1050
        if self._half_screen_layout == compact:
            if compact:
                self.update_compact_group_spacing(width)
            return
        self._half_screen_layout = compact
        if compact:
            self.body_layout.setStretch(0, 4); self.body_layout.setStretch(1, 3)
            self.rebuild_header_layout(True)
            self.rebuild_task_layout(True)
            self.rebuild_footer_layout(True)
            self.update_compact_group_spacing(width)
            self.header_secondary_widget.show(); self.task_secondary_widget.show(); self.footer_secondary_widget.show()
            self.header_panel.setFixedHeight(102)
        else:
            self.body_layout.setStretch(0, 5); self.body_layout.setStretch(1, 3)
            self.rebuild_header_layout(False)
            self.rebuild_task_layout(False)
            self.rebuild_footer_layout(False)
            self.header_secondary_widget.hide(); self.task_secondary_widget.hide(); self.footer_secondary_widget.hide()
            self.header_panel.setFixedHeight(64)
        self.centralWidget().layout().activate()

    def apply_settings_density(self, width: int, height: int):
        """Keep the bottom settings block stable while the prompt uses spare height."""
        compact_width = width < 1050
        compact_height = height < 740
        self.prompt.setMinimumHeight(84)
        self.prompt.setMaximumHeight(16777215)
        self.settings_form.setSpacing(5 if compact_height else 7)
        self.settings_note.setWordWrap(not compact_height)
        self.settings_note.setMaximumHeight(20 if compact_height else 16777215)
        for selector in self.setting_selectors:
            selector.setCompact(compact_width)
        self.settings_panel.layout().activate()

    def apply_native_window_icon(self):
        if sys.platform != "win32":
            return
        icon_file = str(native_icon_path())
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_ssize_t
        hwnd = ctypes.c_void_p(int(self.winId()))
        self.apply_native_titlebar_theme(hwnd)
        wm_seticon, image_icon, load_flags = 0x0080, 1, 0x8010
        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except (AttributeError, OSError):
            dpi = 96
        big_size = max(64, round(32 * dpi / 96))
        small_size = max(32, round(16 * dpi / 96))
        sizes = (
            (1, big_size, big_size),
            (0, small_size, small_size),
            (2, small_size, small_size),
        )
        self._native_icons = []
        for icon_type, width, height in sizes:
            handle = user32.LoadImageW(None, icon_file, image_icon, width, height, load_flags)
            if handle:
                self._native_icons.append(handle)
                user32.SendMessageW(hwnd, wm_seticon, ctypes.c_void_p(icon_type), handle)

    @staticmethod
    def apply_native_titlebar_theme(hwnd):
        """Blend the Windows caption into the active application theme."""
        apply_windows_titlebar_theme(hwnd, "auto")

    def native_window_icon_is_set(self) -> bool:
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return True
        user32 = ctypes.windll.user32
        user32.SendMessageW.restype = ctypes.c_ssize_t
        hwnd = ctypes.c_void_p(int(self.winId()))
        return any(user32.SendMessageW(hwnd, 0x007F, icon_type, 0) for icon_type in (2, 0, 1))

    def load_defaults(self):
        self.set_controls(self.store.defaults)

    def set_controls(self, d):
        self.prompt.setPlainText(d.prompt); self.resolution.setCurrentText(d.resolution); self.duration.setCurrentText(d.duration)
        self._applying_rules = False; self._first_frame_active = False; self.ratio.setEnabled(True)
        self.ratio.setDisplayOverride(None); self.ratio.setCurrentText("自动" if d.ratio == "由底图决定" else d.ratio)
        self._ratio_before_first = d.ratio; self.usage.setCurrentText(d.usage); self.audio.setCurrentText(d.audio)
        self.interval.setValue(d.submit_interval); self.update_interval_label(self.interval.value())
        self.auto_pause.setChecked(d.auto_pause); self.clear_on_complete.setChecked(d.clear_on_complete); self.apply_rules()

    def collect_defaults(self) -> Defaults:
        usage = self.usage.currentText()
        resolution = self.resolution.currentText()
        if usage != "首帧" and resolution == "1080p":
            resolution = "720p"
        ratio = "自动" if usage == "首帧" else self.ratio.currentText()
        return Defaults(self.prompt.toPlainText().strip(), usage, resolution,
                        self.duration.currentText(), ratio, self.audio.currentText(), self.interval.value(),
                        self.auto_pause.isChecked(), self.clear_on_complete.isChecked())

    def update_interval_label(self, value: int):
        self.interval_value.setText(f"{value} 秒")

    def update_prompt_clear_state(self):
        self.prompt_clear.setEnabled(bool(self.prompt.toPlainText()))

    def apply_rules(self):
        if getattr(self, "_applying_rules", False):
            return
        self._applying_rules = True
        try:
            first = self.usage.currentText() == "首帧"
            if first and not self._first_frame_active:
                self._ratio_before_first = self.ratio.currentText()
                self._first_frame_active = True
                self.ratio.setCurrentText("自动")
                self.ratio.setDisplayOverride("底图决定")
            elif not first and self._first_frame_active:
                self._first_frame_active = False
                self.ratio.setCurrentText(self._ratio_before_first)
                self.ratio.setDisplayOverride(None)
            self.ratio.setEnabled(not first)
            self.ratio.setToolTip("首帧模式的比例由底图决定。" if first else "选择“自动”后，上传时读取底图宽高并匹配最接近的 Grok 视频比例。")
            self.resolution.setOptionEnabled("1080p", first, "" if first else "1080p 仅首帧模式可用")
            if not first and self.resolution.currentText() == "1080p":
                self.resolution.setCurrentText("720p")
            self.resolution.setToolTip("非首帧模式不支持 1080p。" if not first else "首帧模式可使用 1080p。")
        finally:
            self._applying_rules = False

    def refresh(self):
        selected = self.checked_task_ids.copy()
        live_ids = {task.id for task in self.store.tasks}
        self.checked_task_ids.intersection_update(live_ids)
        if self.selection_anchor_id not in live_ids:
            self.selection_anchor_id = None
        base = self.store.defaults if self.store.running else self.collect_defaults()
        signature = tuple(
            (task.id, task.path, task.status, task.error, task.detected_ratio,
             json.dumps(task.overrides, ensure_ascii=False, sort_keys=True),
             *(task.effective(base).get(key, "") for key in ("usage", "resolution", "duration", "ratio", "audio")))
            for task in self.store.tasks
        )
        if signature != self._task_render_signature:
            def task_parameters(index, task):
                cfg = task.effective(base)
                ratio = task.detected_ratio or cfg["ratio"]
                return (
                    f"#{index}  ·  {cfg['usage']}  ·  {cfg['resolution']}  ·  {cfg['duration']}  ·  "
                    f"{ratio}  ·  音频{cfg['audio']}"
                    + ("  ·  单独设置" if task.overrides else "")
                )

            existing_rows = []
            can_update_in_place = self.list.count() == len(self.store.tasks)
            if can_update_in_place:
                for item_index, task in enumerate(self.store.tasks):
                    item = self.list.item(item_index)
                    row = self.list.itemWidget(item)
                    if item.data(Qt.ItemDataRole.UserRole) != task.id or not isinstance(row, TaskRowWidget):
                        can_update_in_place = False
                        break
                    existing_rows.append(row)

            if can_update_in_place:
                for index, (task, row) in enumerate(zip(self.store.tasks, existing_rows), 1):
                    row.update_task_state(
                        task, task_parameters(index, task), task.id in selected,
                        task.id in selected,
                    )
            else:
                # Only additions, removals and reordering rebuild the list. Ordinary
                # status changes stay on the existing row and never detach its overlay.
                self.list.setUpdatesEnabled(False)
                self.list.blockSignals(True)
                try:
                    for item_index in range(self.list.count()):
                        old_row = self.list.itemWidget(self.list.item(item_index))
                        if isinstance(old_row, TaskRowWidget):
                            old_row.prepare_for_list_rebuild()
                    self.list.clear()
                    for index, task in enumerate(self.store.tasks, 1):
                        parameters = task_parameters(index, task)
                        item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, task.id)
                        item.setToolTip(task.path); item.setSizeHint(QSize(0, 96))
                        self.list.addItem(item)
                        row = TaskRowWidget(
                            task, self.thumbnail_for(task.path), parameters, task.id in self.checked_task_ids,
                            lambda modifiers, task_id=task.id: self.task_clicked(task_id, modifiers),
                            lambda task_id=task.id: self.preview_task(task_id),
                            lambda task_id=task.id: self.reveal_task_file(task_id),
                            lambda task_id=task.id: self.edit_task(task_id),
                            lambda task_id=task.id: self.restore_task_defaults(task_id),
                            lambda task_id=task.id: self.retry_task(task_id),
                            lambda task_id=task.id: self.prioritize_task(task_id),
                            lambda task_id=task.id: self.copy_task_error(task_id),
                            lambda task_id=task.id: self.remove_task(task_id),
                            lambda task_id=task.id: self.select_task(task_id),
                        )
                        self.list.setItemWidget(item, row)
                        row.attach_remove_overlay(
                            self.list.viewport(), self.list.parentWidget(), self.list.verticalScrollBar()
                        )
                        row.set_selected(task.id in selected)
                        if task.id not in self._known_task_ids:
                            row.animate_in()
                finally:
                    self.list.blockSignals(False)
                    self.list.setUpdatesEnabled(True)
                self.list.viewport().update()
            self._task_render_signature = signature
            self._known_task_ids = live_ids.copy()
        counts = {}
        for t in self.store.tasks: counts[t.status] = counts.get(t.status, 0) + 1
        self.summary.setText(f"共 {len(self.store.tasks)} 张  ·  等待 {counts.get('等待提交',0)}  ·  已提交 {counts.get('已提交',0)}")
        finished = counts.get("已提交", 0) + counts.get("提交失败", 0) + counts.get("需要人工处理", 0)
        self.batch_progress.setValue(round(100 * finished / len(self.store.tasks)) if self.store.tasks else 0)
        self.total_value.setText(str(len(self.store.tasks)))
        self.waiting_value.setText(str(counts.get("等待提交", 0)))
        self.submitted_value.setText(str(counts.get("已提交", 0)))
        self.progress_caption.setText(f"整体进度  {finished} / {len(self.store.tasks)}")
        active = next((task for task in self.store.tasks if task.status in {"正在上传", "正在设置", "正在提交"}), None)
        failed = counts.get("提交失败", 0) + counts.get("需要人工处理", 0)
        dark = application_theme_mode() == "dark"
        if active:
            self.footer_status.setText(f"●  {active.status} · {active.name}")
            self.footer_status.setStyleSheet("color:#77BAFF;background:#173652;" if dark else "color:#1769AA;background:#E5F2FE;")
        elif failed:
            self.footer_status.setText(f"●  有 {failed} 个任务需要处理")
            self.footer_status.setStyleSheet("color:#FF8995;background:#48252D;" if dark else "color:#A33B4B;background:#FFF0F2;")
        elif self.store.running:
            self.footer_status.setText("●  正在等待浏览器助手领取任务")
            self.footer_status.setStyleSheet("color:#77BAFF;background:#173652;" if dark else "color:#1769AA;background:#E5F2FE;")
        else:
            self.footer_status.setText("●  准备就绪")
            self.footer_status.setStyleSheet("color:#BFD5E8;background:#182737;" if dark else "color:#41647F;background:#EEF5FB;")
        self.start.setText("暂停新任务" if self.store.running else "开始批量提交")
        self.start.setIcon(premium_icon("pause" if self.store.running else "send", TEXT))
        self.update_selection_ui()

    def thumbnail_for(self, path: str) -> QIcon:
        cached = self.thumbnail_cache.get(path)
        if cached is not None:
            return cached

        size = 72
        image = read_thumbnail_image(path, size)

        canvas = QPixmap(size, size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image).scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (size - pixmap.width()) // 2; y = (size - pixmap.height()) // 2
            clip = QPainterPath(); clip.addRoundedRect(x, y, pixmap.width(), pixmap.height(), 8, 8)
            painter.setClipPath(clip)
            painter.drawPixmap(x, y, pixmap)
            painter.setClipping(False)
        else:
            painter.setPen(QColor(ACCENT)); painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            painter.drawText(canvas.rect(), Qt.AlignmentFlag.AlignCenter, "图片")
        painter.end()

        icon = QIcon(canvas)
        self.thumbnail_cache[path] = icon
        return icon

    def refresh_connection(self):
        self.scan_installed_helpers()
        connected = self.helper_connected()
        current = self.store.connected_helper_version == APP_VERSION
        browser = self.store.connected_browser
        if connected and current:
            suffix = f" · {browser} {DISPLAY_VERSION}" if browser else f" · {DISPLAY_VERSION}"
            self.connection.setText(f"● 浏览器助手已连接{suffix}")
            self.connection.setStyleSheet("color:#16865F;font-weight:700")
            self.install_button.setText("浏览器助手已安装 ✓")
        elif connected:
            self.connection.setText(f"● 浏览器助手版本不一致 · {self.store.connected_helper_version or '未知'} → {DISPLAY_VERSION}")
            self.connection.setStyleSheet("color:#B77C14;font-weight:700")
            self.install_button.setText("更新 / 修复浏览器助手")
        elif self.installed_helper_browsers:
            names = " / ".join(sorted(self.installed_helper_browsers))
            self.connection.setText(f"● 浏览器助手已安装 · {names} · 等待打开 Grok")
            self.connection.setStyleSheet("color:#16865F;font-weight:700")
            self.install_button.setText("已安装 / 检查浏览器助手")
        else:
            self.connection.setText("● 未检测到浏览器助手 · 点击右侧安装")
            self.connection.setStyleSheet("color:#B77C14;font-weight:700")
            self.install_button.setText("一键安装 / 修复浏览器助手")

    def helper_connected(self) -> bool:
        return time.time() - self.store.browser_seen < 15

    def scan_installed_helpers(self, force: bool = False):
        now = time.time()
        if force or now - self.last_helper_scan >= 3:
            self.installed_helper_browsers = detect_installed_helper_browsers(self.extension_dir)
            self.last_helper_scan = now

    def add_paths(self, paths):
        self.store.add_paths(paths)

    def open_grok(self):
        QDesktopServices.openUrl(QUrl("https://grok.com/imagine"))

    def pick_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择底图", "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif *.heic *.heif)")
        self.add_paths(files)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if folder: self.add_paths([folder])

    def selected_ids(self):
        return self.checked_task_ids.copy()

    def remove_selected(self): self.store.remove(self.selected_ids())

    def remove_all(self):
        if not self.store.tasks: return
        answer = show_themed_message(self, QMessageBox.Icon.Question, "移除所有任务", "确定从任务列表移除全部图片吗？\n原始图片文件不会被删除。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear()

    def task_clicked(self, task_id: str, modifiers=Qt.KeyboardModifier.NoModifier):
        ordered_ids = [task.id for task in self.store.tasks]
        self.checked_task_ids, self.selection_anchor_id = task_selection_after_click(
            ordered_ids, self.checked_task_ids, self.selection_anchor_id, task_id, modifiers,
        )
        self.selection_changed()

    def toggle_select_all(self, checked: bool):
        if self.select_all.signalsBlocked():
            return
        self.checked_task_ids = {task.id for task in self.store.tasks} if checked else set()
        self.selection_anchor_id = self.store.tasks[0].id if checked and self.store.tasks else None
        for index in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(index))
            if isinstance(row, TaskRowWidget):
                row.set_selected(checked)
        self.selection_changed()

    def update_selection_ui(self):
        selected = self.selected_ids()
        total = len(self.store.tasks); count = len(selected)
        if time.monotonic() >= self.task_feedback_until:
            self.selection_count.setText(f"共 {total} 个任务 · 已选择 {count} 个" if total else "尚无任务")
        self.select_all.blockSignals(True)
        self.select_all.setChecked(bool(total) and count == total)
        self.select_all.blockSignals(False)
        for index in range(self.list.count()):
            item = self.list.item(index); row = self.list.itemWidget(item)
            if isinstance(row, TaskRowWidget):
                row.set_selected(item.data(Qt.ItemDataRole.UserRole) in selected)

    def task_by_id(self, task_id: str):
        return next((task for task in self.store.tasks if task.id == task_id), None)

    def preview_task(self, task_id: str):
        task = self.task_by_id(task_id)
        if task:
            dialog = ImagePreviewDialog(task.path, self)
            dialog.exec()

    def reveal_task_file(self, task_id: str):
        task = self.task_by_id(task_id)
        if not task:
            return
        path = Path(task.path)
        if not path.exists():
            self.show_task_feedback(f"找不到原始图片：{task.name}")
            return
        try:
            subprocess.Popen(["explorer.exe", f"/select,{path}"])
        except OSError:
            self.show_task_feedback(f"无法打开图片所在文件夹：{task.name}")

    def select_task(self, task_id: str):
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.list.setCurrentItem(item); break

    def edit_task(self, task_id: str):
        task = self.task_by_id(task_id)
        if not task:
            return
        if task.status in {"正在上传", "正在设置", "正在提交"}:
            self.show_task_feedback("任务正在处理，暂时不能编辑设置")
            return
        self.checked_task_ids = {task_id}; self.selection_anchor_id = task_id
        self.list.clearSelection(); self.select_task(task_id)
        for index in range(self.list.count()):
            item = self.list.item(index); row = self.list.itemWidget(item)
            if isinstance(row, TaskRowWidget):
                row.set_selected(item.data(Qt.ItemDataRole.UserRole) == task_id)
        self.selection_changed()
        values = self.draft_defaults.__dict__.copy(); values.update(task.overrides)
        self.set_controls(Defaults(**values))
        self.show_task_feedback(f"正在编辑：{task.name}；修改后请点击“应用到所选任务”")

    def restore_task_defaults(self, task_id: str):
        task = self.task_by_id(task_id)
        if not task:
            return
        if task.status in {"正在上传", "正在设置", "正在提交"}:
            self.show_task_feedback("任务正在处理，暂时不能恢复设置")
            return
        task.overrides = {}
        self.store.notify()
        self.show_task_feedback(f"已恢复默认设置：{task.name}")

    def prioritize_task(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and self.store.prioritize(task_id):
            self.show_task_feedback(f"已设为下一个任务：{task.name}")

    def copy_task_error(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and task.error:
            QApplication.clipboard().setText(task.error)
            self.show_task_feedback(f"已复制错误信息：{task.name}")

    def retry_task(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and task.status not in {"正在上传", "正在设置", "正在提交"}:
            task.status, task.error = "等待提交", ""
            self.store.notify()
            self.show_task_feedback(f"已将 {task.name} 加入待提交队列")

    def remove_task(self, task_id: str):
        task = self.task_by_id(task_id)
        if task and task.status in {"正在上传", "正在设置", "正在提交"}:
            self.show_task_feedback("任务正在处理，暂时不能移除")
            return
        self.checked_task_ids.discard(task_id)
        if self.selection_anchor_id == task_id:
            self.selection_anchor_id = None
        self.store.remove({task_id})
        if task:
            self.show_task_feedback(f"已从列表移除：{task.name}")

    def show_task_feedback(self, message: str):
        self.task_feedback_until = time.monotonic() + 2.6
        self.selection_count.setText(message)
        QTimer.singleShot(2600, self.update_selection_ui)

    def show_change_log(self):
        ChangeLogDialog(self).exec()

    def selection_changed(self):
        ids = self.selected_ids()
        if ids and not self.had_selection:
            self.draft_defaults = self.collect_defaults()
        elif not ids and self.had_selection:
            self.set_controls(self.draft_defaults)
        self.had_selection = bool(ids)
        self.update_selection_ui()

    def apply_to_selected(self):
        ids = self.selected_ids()
        if not ids: return
        cfg = self.collect_defaults().__dict__
        for task in self.store.tasks:
            if task.id in ids: task.overrides = cfg.copy()
        self.store.notify(); self.clear_task_selection(); self.set_controls(self.draft_defaults)

    def clear_overrides(self):
        ids = self.selected_ids()
        for task in self.store.tasks:
            if task.id in ids: task.overrides = {}
        self.store.notify(); self.clear_task_selection(); self.set_controls(self.draft_defaults)

    def clear_task_selection(self):
        self.checked_task_ids.clear(); self.selection_anchor_id = None; self.list.clearSelection()
        for index in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(index))
            if isinstance(row, TaskRowWidget):
                row.set_selected(False)
        self.selection_changed()

    def retry_failed(self): self.store.reset_failed()

    def resubmit_selected(self):
        ids = self.selected_ids()
        if not ids: return
        for task in self.store.tasks:
            if task.id in ids:
                task.status, task.error = "等待提交", ""
        self.store.notify()

    def toggle_run(self):
        if self.store.running:
            self.store.running = False; self.store.notify(); return
        defaults = self.draft_defaults if self.selected_ids() else self.collect_defaults()
        if not defaults.prompt:
            return show_themed_message(self, QMessageBox.Icon.Warning, "缺少提示词", "请先填写批量默认提示词。")
        if not self.store.tasks:
            return show_themed_message(self, QMessageBox.Icon.Warning, "没有图片", "请先拖入图片或文件夹。")
        answer = show_themed_message(self, QMessageBox.Icon.Question, "开始自动化", "运行期间请勿手动操作目标 Grok 标签页。\n\n我已了解，开始执行？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        self.store.defaults = defaults; self.store.running = True; self.store.notify()

    def install_extension(self):
        self.extension_dir, self.store.helper_version = provision_extension()
        if self.extension_guide:
            self.extension_guide.close()
        self.extension_guide = ExtensionGuideDialog(self, open_browser=False)
        self.extension_guide.show(); self.extension_guide.raise_(); self.extension_guide.activateWindow()

    def offer_first_install(self):
        marker = APP_DIR / ".install_guide_shown_v2"
        if marker.exists():
            return
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(APP_VERSION, encoding="utf-8")
        except OSError:
            pass
        self.extension_dir, self.store.helper_version = provision_extension()
        self.extension_guide = ExtensionGuideDialog(self, open_browser=False)
        self.extension_guide.show(); self.extension_guide.raise_(); self.extension_guide.activateWindow()

    def show_drag_overlay(self):
        if self.drag_overlay.isVisible():
            return
        blur = QGraphicsBlurEffect(self.centralWidget())
        blur.setBlurRadius(9)
        self.centralWidget().setGraphicsEffect(blur)
        self.drag_overlay.setGeometry(self.rect())
        self.drag_overlay.show()
        self.drag_overlay.raise_()

    def hide_drag_overlay(self):
        self.drag_overlay.hide()
        self.centralWidget().setGraphicsEffect(None)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            self.show_drag_overlay()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.hide_drag_overlay()
        event.accept()

    def dropEvent(self, event: QDropEvent):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.hide_drag_overlay()
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_half_screen_layout"):
            self.apply_half_screen_layout(event.size().width())
        if hasattr(self, "setting_selectors"):
            self.apply_settings_density(event.size().width(), event.size().height())
        if hasattr(self, "drag_overlay"):
            self.drag_overlay.setGeometry(self.rect())

    def changeEvent(self, event):
        super().changeEvent(event)
        if (getattr(self, "theme_preference", "light") == "system"
                and event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange)):
            effective = resolved_theme_mode("system")
            app = QApplication.instance()
            if app is not None and app.property("themeMode") != effective:
                QTimer.singleShot(0, self.apply_color_theme)

    def _write_shutdown_log(self, message: str):
        """Best-effort diagnostics; logging must never keep the application alive."""
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            log_path = APP_DIR / "shutdown.log"
            if log_path.exists() and log_path.stat().st_size > 128 * 1024:
                log_path.replace(APP_DIR / "shutdown.previous.log")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} [{APP_VERSION}] {message}\n")
        except (OSError, ValueError):
            pass

    def shutdown(self, reason: str = "application-exit"):
        """Release every owned resource, continuing even when one step fails."""
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True
        errors: list[str] = []
        self._write_shutdown_log(f"shutdown begin: {reason}")

        def run_step(name: str, action):
            try:
                action()
                self._write_shutdown_log(f"shutdown step ok: {name}")
            except Exception as exc:  # Shutdown must continue through unrelated failures.
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                self._write_shutdown_log(f"shutdown step failed: {errors[-1]}")

        try:
            for timer_name in ("connection_timer", "ui_timer"):
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    run_step(f"stop {timer_name}", timer.stop)

            def save_state():
                self.store.running = False
                try:
                    self.store.defaults = self.collect_defaults()
                finally:
                    self.store.save()

            run_step("save state", save_state)

            def close_owned_windows():
                app = QApplication.instance()
                if app is None:
                    return
                for widget in list(app.topLevelWidgets()):
                    if widget is not self and widget.parent() is self:
                        widget.close()

            run_step("close child windows", close_owned_windows)
            run_step("stop browser bridge", self.bridge.stop)
            self._shutdown_complete = getattr(self.bridge, "httpd", None) is None
            outcome = "complete" if self._shutdown_complete and not errors else "complete with errors"
            self._write_shutdown_log(f"shutdown {outcome}: {reason}; {'; '.join(errors) if errors else 'ok'}")
        finally:
            self._shutdown_in_progress = False

    def closeEvent(self, event):
        self.shutdown("main-window-close")
        event.accept()


def main():
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Grok.BatchVideoAssistant")
    app = QApplication(sys.argv); app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName(APP_NAME); app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(str(app_icon_path())))
    instance_mutex = acquire_single_instance_mutex()
    if instance_mutex is None:
        show_themed_message(
            None, QMessageBox.Icon.Warning, "软件已在运行",
            "检测到老七的Grok视频助手已经运行。\n\n请先关闭现有窗口，再启动当前版本。",
        )
        return 2
    try:
        window = MainWindow()
    except BridgePortInUseError as exc:
        show_themed_message(
            None, QMessageBox.Icon.Critical, "浏览器助手端口被占用",
            f"{exc}\n\n请关闭仍在运行的旧版软件，然后重新启动 {DISPLAY_VERSION}。",
        )
        release_single_instance_mutex(instance_mutex)
        return 3
    app.aboutToQuit.connect(lambda: window.shutdown("about-to-quit"))
    window.show()
    if "--thumbnail-smoke" in sys.argv:
        def thumbnail_smoke_check():
            import tempfile
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
                from PIL import Image
                with tempfile.TemporaryDirectory() as folder:
                    source = Image.new("RGB", (96, 54), "#E5A52D")
                    formats = {"jpg":"JPEG", "webp":"WEBP", "bmp":"BMP", "gif":"GIF",
                               "tiff":"TIFF", "avif":"AVIF", "heic":"HEIF"}
                    for suffix, image_format in formats.items():
                        path = Path(folder) / f"sample.{suffix}"
                        source.save(path, format=image_format)
                        if read_thumbnail_image(str(path)).isNull():
                            raise RuntimeError(suffix)
                app.exit(0)
            except Exception:
                app.exit(4)
        QTimer.singleShot(0, thumbnail_smoke_check)
    elif "--smoke" in sys.argv:
        def smoke_check():
            ready = not window.windowIcon().isNull() and window.native_window_icon_is_set()
            app.exit(0 if ready else 3)
        QTimer.singleShot(500, smoke_check)
    elif "--guide-visual-smoke" in sys.argv:
        window.hide()
        guide = ExtensionGuideDialog(window, open_browser=False); guide.show()
        def capture_guide():
            QApplication.processEvents()
            guide.screen().grabWindow(guide.winId()).save(str(Path(__file__).parent / "guide_preview.png"))
            app.quit()
        QTimer.singleShot(800, capture_guide)
    elif "--visual-smoke" in sys.argv or "--drop-overlay-smoke" in sys.argv:
        def capture():
            if "--drop-overlay-smoke" in sys.argv:
                window.show_drag_overlay()
            QApplication.processEvents()
            name = "drop_preview.png" if "--drop-overlay-smoke" in sys.argv else "preview.png"
            window.screen().grabWindow(window.winId()).save(str(Path(__file__).parent / name))
            app.quit()
        QTimer.singleShot(700, capture)
    try:
        return app.exec()
    finally:
        window.shutdown("event-loop-finally")
        release_single_instance_mutex(instance_mutex)


if __name__ == "__main__": sys.exit(main())
