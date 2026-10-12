import json
import inspect
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QMenu, QPushButton

from app import (
    APP_NAME, APP_VERSION, CHANGE_LOG_HTML, DISPLAY_VERSION, AnimatedDialog, ChangeLogDialog, ExtensionGuideDialog, ImagePreviewDialog, MainWindow, SettingSelector, StateCheckBox, TaskListWidget,
    ResponsiveTaskParameters, SelectAllCheckBox, TaskRowWidget, app_icon_path, extension_tutorial_path,
    THEMES, apply_windows_titlebar_theme, brand_sky_path, brand_sun_path, bundled_extension_source, detect_installed_helper_browsers, provision_extension,
    acquire_single_instance_mutex, read_preview_image, read_thumbnail_image, release_single_instance_mutex, task_selection_after_click,
)
from core import Task


class ShutdownTests(unittest.TestCase):
    def test_shutdown_continues_when_collecting_defaults_fails(self):
        bridge = SimpleNamespace(httpd=None, stop=MagicMock())
        store = SimpleNamespace(running=True, defaults=None, save=MagicMock())
        window = SimpleNamespace(
            _shutdown_complete=False,
            _shutdown_in_progress=False,
            connection_timer=SimpleNamespace(stop=MagicMock()),
            ui_timer=SimpleNamespace(stop=MagicMock()),
            bridge=bridge,
            store=store,
            collect_defaults=MagicMock(side_effect=RuntimeError("broken controls")),
            _write_shutdown_log=MagicMock(),
        )

        MainWindow.shutdown(window, "test")

        self.assertFalse(store.running)
        store.save.assert_called_once()
        bridge.stop.assert_called_once()
        self.assertTrue(window._shutdown_complete)

    def test_shutdown_is_idempotent_after_bridge_stops(self):
        bridge = SimpleNamespace(httpd=None, stop=MagicMock())
        store = SimpleNamespace(running=True, defaults=None, save=MagicMock())
        window = SimpleNamespace(
            _shutdown_complete=False,
            _shutdown_in_progress=False,
            bridge=bridge,
            store=store,
            collect_defaults=MagicMock(return_value="defaults"),
            _write_shutdown_log=MagicMock(),
        )

        MainWindow.shutdown(window, "first")
        MainWindow.shutdown(window, "second")

        store.save.assert_called_once()
        bridge.stop.assert_called_once()


class ExtensionPackagingTests(unittest.TestCase):
    def test_light_brand_art_is_available(self):
        self.assertTrue(brand_sky_path().is_file())
        self.assertGreater(brand_sky_path().stat().st_size, 0)
        self.assertTrue(brand_sun_path().is_file())
        self.assertGreater(brand_sun_path().stat().st_size, 0)

    def test_task_list_owns_the_empty_drop_hint(self):
        build_source = inspect.getsource(MainWindow.build_ui)
        list_source = inspect.getsource(TaskListWidget.paintEvent)
        self.assertIn("TaskListWidget()", build_source)
        self.assertNotIn("DropZone()", build_source)
        self.assertIn("拖入图片或文件夹", list_source)
        self.assertIn("if self.count()", list_source)
        self.assertIn("self.blankClicked.emit()", inspect.getsource(TaskListWidget.mousePressEvent))

    def test_task_rows_expose_selection_status_and_hover_actions(self):
        row_source = inspect.getsource(TaskRowWidget)
        refresh_source = inspect.getsource(MainWindow.refresh)
        self.assertNotIn("TaskSelectCheckBox", row_source)
        self.assertIn("self._on_selection_request(event.modifiers())", row_source)
        self.assertIn('setObjectName("statusBadge")', row_source)
        self.assertIn("def enterEvent", row_source)
        self.assertIn("编辑此任务设置", row_source)
        self.assertIn("TaskRowWidget(", refresh_source)
        self.assertIn("task.effective(base)", refresh_source)

    def test_task_rows_support_double_click_preview_and_blue_selection_rail(self):
        row_source = inspect.getsource(TaskRowWidget)
        self.assertIn("def mouseDoubleClickEvent", row_source)
        self.assertIn("self._on_preview()", row_source)
        self.assertIn("if self._selected:", row_source)
        self.assertIn('QColor(theme_color("accent"))', row_source)

    def test_task_card_forwards_ctrl_and_shift_modifiers_without_checkbox(self):
        app = QApplication.instance() or QApplication([])
        modifiers_seen = []

        callback = lambda: None
        row = TaskRowWidget(
            Task("sample.png"), QIcon(), "#1 · 循环 · 480p · 10s · 自动 · 音频关闭", False,
            modifiers_seen.append, callback, callback, callback, callback, callback,
            callback, callback, callback, callback,
        )
        row.resize(560, 96); row.show(); app.processEvents()
        self.assertIsNone(row.findChild(QCheckBox, "taskSelect"))
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(250, 48))
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, QPoint(250, 48))
        self.assertTrue(modifiers_seen[0] & Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(modifiers_seen[1] & Qt.KeyboardModifier.ShiftModifier)
        row.close()

    def test_task_selection_supports_plain_ctrl_shift_and_ctrl_shift_clicks(self):
        ids = ["a", "b", "c", "d", "e"]
        selected, anchor = task_selection_after_click(ids, {"b", "d"}, "b", "c", Qt.KeyboardModifier.NoModifier)
        self.assertEqual(selected, {"c"}); self.assertEqual(anchor, "c")
        selected, anchor = task_selection_after_click(ids, selected, anchor, "e", Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(selected, {"c", "e"}); self.assertEqual(anchor, "e")
        selected, anchor = task_selection_after_click(ids, selected, anchor, "b", Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(selected, {"b", "c", "d", "e"}); self.assertEqual(anchor, "e")
        selected, anchor = task_selection_after_click(
            ids, {"a"}, "b", "d",
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        self.assertEqual(selected, {"a", "b", "c", "d"}); self.assertEqual(anchor, "b")
        selected, anchor = task_selection_after_click(ids, selected, anchor, "a", Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(selected, {"b", "c", "d"}); self.assertEqual(anchor, "a")

    def test_task_parameter_summary_balances_into_two_lines_when_narrow(self):
        app = QApplication.instance() or QApplication([])
        text = "#2  ·  循环  ·  480p  ·  10s  ·  自动  ·  音频关闭"
        label = ResponsiveTaskParameters(text)
        label.resize(185, 42); label.show(); app.processEvents()
        self.assertIn("\n", label.text())
        self.assertNotIn("\n·", label.text())
        self.assertIn("音频关闭", label.text())
        label.resize(600, 42); app.processEvents()
        self.assertNotIn("\n", label.text())
        self.assertEqual(label.toolTip(), text)
        label.close()

    def test_custom_switch_uses_the_full_visible_hit_area(self):
        app = QApplication.instance() or QApplication([])
        switch = StateCheckBox("测试开关")
        switch.resize(160, 36); switch.show(); app.processEvents()
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=QPoint(145, 18))
        self.assertTrue(switch.isChecked())
        switch_source = inspect.getsource(StateCheckBox)
        self.assertIn('QPropertyAnimation(self, b"switchProgress"', switch_source)
        self.assertIn("QEasingCurve.Type.OutCubic", switch_source)
        switch.close()

    def test_select_all_checkbox_is_self_drawn_and_slider_has_no_theme_backplate(self):
        app = QApplication.instance() or QApplication([])
        app.setProperty("themeMode", "light")
        checkbox = SelectAllCheckBox("全选任务")
        checkbox.resize(110, 30); checkbox.show(); app.processEvents()
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=QPoint(96, 15))
        self.assertTrue(checkbox.isChecked())
        source = inspect.getsource(SelectAllCheckBox)
        self.assertIn("theme_color", source)
        self.assertIn("drawRoundedRect", source)
        build = inspect.getsource(MainWindow.build_ui)
        apply_theme = inspect.getsource(MainWindow.apply_color_theme)
        self.assertIn("QSlider {{ background:transparent; border:0; }}", build)
        self.assertIn("QSlider { background:transparent; border:0; }", apply_theme)
        checkbox.close(); app.setProperty("themeMode", None); app.setProperty("themePreference", "light")

    def test_preview_decoder_respects_original_and_screen_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            small = Path(folder) / "small.png"
            large = Path(folder) / "large.png"
            Image.new("RGB", (120, 80), "red").save(small)
            Image.new("RGB", (1200, 800), "blue").save(large)
            self.assertEqual(read_preview_image(str(small), QSize(500, 500)).size(), QSize(120, 80))
            self.assertEqual(read_preview_image(str(large), QSize(300, 300)).size(), QSize(300, 200))

    def test_task_row_action_buttons_are_connected(self):
        app = QApplication.instance() or QApplication([])
        calls = []
        task = Task("sample.png")
        task_callback = lambda task_id="expected-task-id": calls.append(task_id)
        row = TaskRowWidget(
            task, QIcon(), "#1 · 循环 · 480p · 10s · 1:1 · 音频关闭", False,
            lambda checked: calls.append(("checked", checked)),
            task_callback, task_callback, task_callback, task_callback, task_callback,
            task_callback, task_callback, task_callback, lambda: calls.append("select"),
        )
        menu = row.findChild(QMenu)
        self.assertIsNotNone(menu)
        self.assertIn("在文件夹中显示", [action.text() for action in menu.actions()])
        self.assertIn("恢复此任务默认设置", [action.text() for action in menu.actions()])
        self.assertIn("设为下一个任务", [action.text() for action in menu.actions()])
        for action in menu.actions():
            if not action.isSeparator(): action.trigger()
        row.remove_hover.click()
        self.assertEqual(calls, ["expected-task-id"] * 7)
        row_source = inspect.getsource(TaskRowWidget)
        self.assertIn("def contextMenuEvent", row_source)
        row.deleteLater(); app.processEvents()

    def test_task_menu_changes_with_status_and_locks_active_mutations(self):
        app = QApplication.instance() or QApplication([])
        callback = lambda: None

        def menu_for(status, error=""):
            task = Task("sample.png", status=status, error=error)
            row = TaskRowWidget(task, QIcon(), "parameters", False, lambda _checked: None,
                                callback, callback, callback, callback, callback, callback,
                                callback, callback, callback)
            return row, {action.text(): action for action in row.action_menu.actions() if not action.isSeparator()}

        waiting_row, waiting = menu_for("等待提交")
        self.assertIn("设为下一个任务", waiting); self.assertNotIn("重新提交", waiting)
        failed_row, failed = menu_for("提交失败", "网络超时")
        self.assertIn("重试此任务", failed); self.assertTrue(failed["复制错误信息"].isEnabled())
        done_row, done = menu_for("已提交")
        self.assertIn("重新提交", done); self.assertNotIn("设为下一个任务", done)
        active_row, active = menu_for("正在上传")
        for text in ("编辑此任务设置", "恢复此任务默认设置", "重新提交", "从列表移除"):
            self.assertFalse(active[text].isEnabled())
        for row in (waiting_row, failed_row, done_row, active_row): row.deleteLater()
        app.processEvents()

    def test_change_log_is_manual_and_has_a_dedicated_button(self):
        build_source = inspect.getsource(MainWindow.build_ui)
        self.assertIn('QPushButton("更新日志")', build_source)
        self.assertIn("self.show_change_log", build_source)
        dialog_source = inspect.getsource(ChangeLogDialog)
        self.assertIn("CapsuleScrollBar", dialog_source)
        for version in ("正式版 1.2.34", "正式版 1.2.33", "正式版 1.2.32", "正式版 1.2.31", "正式版 1.2.30", "正式版 1.2.29", "正式版 1.2.28", "正式版 1.2.27", "正式版 1.2.26", "正式版 1.2.25", "正式版 1.2.24", "正式版 1.2.23", "正式版 1.2.22", "正式版 1.2.21", "正式版 1.2.20", "正式版 1.2.19", "正式版 1.2.18", "正式版 1.2.17", "正式版 1.2.16", "正式版 1.2.15", "正式版 1.2.14", "正式版 1.2.13", "正式版 1.2.12", "正式版 1.2.11", "正式版 1.2.10", "正式版 1.2.9", "正式版 1.2.8", "正式版 1.2.7", "正式版 1.2.6", "正式版 1.2.5", "正式版 1.2.4", "正式版 1.2.3", "正式版 1.2.2", "正式版 1.2.1", "正式版 1.2.0", "正式版 1.1.2", "正式版 1.1.1", "正式版 1.1.0", "正式版 1.0.4", "正式版 1.0.3", "正式版 1.0.2", "正式版 1.0.1", "正式版 1.0.0",
                        "EA 0.97.11", "0.9.0", "0.8.0", "0.7.7", "0.6.1"):
            self.assertIn(version, CHANGE_LOG_HTML)

    def test_header_exposes_open_grok_action(self):
        build_source = inspect.getsource(MainWindow.build_ui)
        action_source = inspect.getsource(MainWindow.open_grok)
        self.assertIn('QPushButton("打开 Grok")', build_source)
        self.assertIn("self.connection); head.addWidget(self.open_grok_button); head.addWidget(self.install_button)", build_source)
        self.assertIn('https://grok.com/imagine', action_source)
        self.assertIn('premium_icon("grok_mark", TEXT, 22)', build_source)
        self.assertNotIn('premium_icon("external_link"', build_source)

    def test_version_label_is_plain_borderless_text(self):
        style_source = inspect.getsource(MainWindow.build_ui)
        self.assertIn("QLabel#version", style_source)
        self.assertIn("background:transparent; border:none", style_source)

    def test_extension_icon_and_popup_expose_runtime_status_and_version(self):
        helper = bundled_extension_source()
        background = (helper / "background.js").read_text(encoding="utf-8")
        popup_html = (helper / "popup.html").read_text(encoding="utf-8")
        popup_js = (helper / "popup.js").read_text(encoding="utf-8")
        self.assertIn("async function setStatusIcon(state)", background)
        for color in ("#8A98A8", "#20B879", "#2F80ED", "#E7A520", "#E05263"):
            self.assertIn(color, background)
        self.assertIn("await setStatusIcon(state)", background)
        self.assertIn('message.type === "desktop-status"', background)
        self.assertIn('runtimeMessage({type:"desktop-status", state:helperState})', (helper / "content.js").read_text(encoding="utf-8"))
        self.assertIn('id="version"', popup_html)
        self.assertIn("prefers-color-scheme:dark", popup_html)
        self.assertIn("chrome.runtime.getManifest()", popup_js)
        self.assertIn("manifest.version_name", popup_js)
        manifest = json.loads((helper / "manifest.json").read_text(encoding="utf-8"))
        for size in (16, 32, 48, 128):
            icon = helper / manifest["icons"][str(size)]
            self.assertTrue(icon.is_file())

    def test_task_status_refresh_is_atomic(self):
        refresh_source = inspect.getsource(MainWindow.refresh)
        row_source = inspect.getsource(TaskRowWidget.prepare_for_list_rebuild)
        self.assertIn("can_update_in_place", refresh_source)
        self.assertIn("row.update_task_state", refresh_source)
        self.assertIn("self.list.setUpdatesEnabled(False)", refresh_source)
        self.assertIn("old_row.prepare_for_list_rebuild()", refresh_source)
        self.assertIn("self.list.setUpdatesEnabled(True)", refresh_source)
        self.assertIn("self.remove_hover.hide()", row_source)
        self.assertIn("self.remove_hover.deleteLater()", row_source)

    def test_install_guide_does_not_open_browser_by_default(self):
        parameter = inspect.signature(ExtensionGuideDialog.__init__).parameters["open_browser"]
        self.assertIs(parameter.default, False)

    def test_install_guide_embeds_the_six_step_tutorial(self):
        source = inspect.getsource(ExtensionGuideDialog)
        self.assertTrue(extension_tutorial_path().is_file())
        self.assertGreater(extension_tutorial_path().stat().st_size, 1_000_000)
        self.assertIn("extension_tutorial_path()", source)
        self.assertIn("QScrollArea()", source)
        self.assertIn("CapsuleScrollBar", source)
        self.assertIn("停留在 browser_helper，不要进入任何子文件夹", source)
        self.assertIn("open_tutorial_preview", source)
        spec = (Path(__file__).parent / "GrokBatch.spec").read_text(encoding="utf-8")
        self.assertIn("browser_extension_tutorial.png", spec)

    def test_source_manifest_version_matches_application(self):
        manifest = json.loads((bundled_extension_source() / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], APP_VERSION)
        self.assertEqual(manifest["version_name"], DISPLAY_VERSION)
        self.assertEqual(manifest["name"], APP_NAME)

    def test_provisioned_manifest_version_matches_application(self):
        with tempfile.TemporaryDirectory() as folder:
            requested_target = Path(folder) / "browser_helper"
            stale_icons = requested_target / "icons"; stale_icons.mkdir(parents=True)
            (stale_icons / "obsolete.png").write_bytes(b"old")
            target, version = provision_extension(requested_target)
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(version, APP_VERSION)
            self.assertEqual(manifest["version"], APP_VERSION)
            self.assertEqual((target / "app_icon.png").read_bytes(), app_icon_path().read_bytes())
            self.assertEqual(manifest["icons"]["128"], "icon128.png")
            self.assertEqual(manifest["action"]["default_icon"]["16"], "icon16.png")
            self.assertTrue((target / "icon128.png").is_file())
            self.assertFalse((target / "icons").exists())

    def test_detects_unpacked_helper_without_opening_grok(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            helper = root / "fixed" / "browser_helper"; helper.mkdir(parents=True)
            profile = root / "Chrome Data" / "Default"; profile.mkdir(parents=True)
            preferences = {"extensions": {"settings": {"example": {"location": 4, "path": str(helper)}}}}
            (profile / "Secure Preferences").write_text(json.dumps(preferences), encoding="utf-8")
            detected = detect_installed_helper_browsers(helper, {"Chrome": root / "Chrome Data"})
            self.assertEqual(detected, {"Chrome"})

    def test_helper_sleeps_and_detaches_when_desktop_is_closed(self):
        source = bundled_extension_source()
        background = (source / "background.js").read_text(encoding="utf-8")
        content = (source / "content.js").read_text(encoding="utf-8")
        popup = (source / "popup.html").read_text(encoding="utf-8")
        self.assertIn('chrome.debugger.detach', background)
        self.assertIn('desktop-offline', background)
        self.assertIn('automation-idle', background)
        self.assertIn('desktopOnline', content)
        self.assertIn('automationActive', content)
        self.assertNotIn('setInterval(heartbeat, 1500)', content)
        self.assertIn('popup.js', popup)
        self.assertTrue((source / "popup.js").is_file())

    def test_version_mismatch_uses_one_controlled_reload_per_target(self):
        source = bundled_extension_source()
        background = (source / "background.js").read_text(encoding="utf-8")
        content = (source / "content.js").read_text(encoding="utf-8")
        popup = (source / "popup.js").read_text(encoding="utf-8")
        self.assertNotIn("reload-extension", background)
        self.assertNotIn("reload-extension", content)
        self.assertIn("versionCompatible = !mismatch", background)
        self.assertIn("if (attached.size) await detachAll()", background)
        self.assertIn('const RELOAD_ATTEMPT_KEY = "grokBatchReloadAttempt"', background)
        self.assertIn("async function attemptControlledReload(requiredVersion)", background)
        self.assertIn("function compareVersions(first, second)", background)
        self.assertIn("compareVersions(requiredVersion, currentVersion) <= 0", background)
        self.assertIn("if (attempt?.targetVersion === requiredVersion) return false", background)
        self.assertIn("chrome.runtime.reload()", background)
        self.assertIn("await clearSuccessfulReloadAttempt", background)
        self.assertIn("await refreshGrokTabsAfterUpdate()", background)
        self.assertIn('versionMismatch ? 5000', content)
        self.assertIn('state:"warning"', content)
        self.assertIn('requiredVersion:state.required_version', content)
        self.assertIn("版本不一致 · 自动化已暂停", popup)
        self.assertIn("state.version !== manifest.version", popup)
        self.assertIn("浏览器助手不会从", popup)

    def test_desktop_uses_single_instance_mutex(self):
        import sys
        if sys.platform != "win32":
            self.skipTest("Windows named mutex test")
        name = f"Local\\GrokBatchAssistant.Test.{uuid.uuid4()}"
        first = acquire_single_instance_mutex(name)
        try:
            self.assertTrue(first)
            self.assertIsNone(acquire_single_instance_mutex(name))
        finally:
            release_single_instance_mutex(first)
        main_source = inspect.getsource(__import__("app").main)
        self.assertIn("except BridgePortInUseError", main_source)
        self.assertIn("release_single_instance_mutex", main_source)

    def test_next_task_prepares_during_the_send_interval(self):
        content = (bundled_extension_source() / "content.js").read_text(encoding="utf-8")
        self.assertIn('const LAST_SUBMIT_AT_KEY = "grokBatchLastSubmitAt"', content)
        self.assertIn('const LAST_SUBMIT_INTERVAL_KEY = "grokBatchLastSubmitInterval"', content)
        self.assertIn("previousInterval - (Date.now() - previousSubmitAt)", content)
        self.assertIn("if (previousSubmitAt && remainingInterval > 0) await sleep(remainingInterval)", content)
        self.assertIn('sessionStorage.setItem(LAST_SUBMIT_AT_KEY, String(Date.now()))', content)
        self.assertIn('sessionStorage.setItem(LAST_SUBMIT_INTERVAL_KEY', content)
        self.assertNotIn("await sleep(Math.max(0, Number(task.submit_interval) || 0) * 1000)", content)
        self.assertEqual(content.count("await enterImagine();"), 1)
        self.assertIn("let nextDelay = 1200", content)
        self.assertIn("nextDelay = 400", content)
        self.assertIn("scheduleTick(nextDelay)", content)
        self.assertIn("scheduleTick(100);", content)

    def test_enter_imagine_retries_after_a_submission_route_race(self):
        content = (bundled_extension_source() / "content.js").read_text(encoding="utf-8")
        self.assertIn("for (let attempt = 0; attempt < 2; attempt++)", content)
        self.assertIn('if (attempt) await trustedInput(imagine, "click")', content)
        self.assertIn('if (attempt) await trustedInput(video, "click")', content)
        self.assertIn("返回 Imagine 视频制作页失败", content)

    def test_audio_option_is_exposed_and_automated(self):
        ui = inspect.getsource(MainWindow.build_ui)
        content = (bundled_extension_source() / "content.js").read_text(encoding="utf-8")
        self.assertIn('self.audio = SettingSelector("音频", ["开启", "关闭"]', ui)
        self.assertIn('"关闭": ("audio_off", "#8496A8")', ui)
        self.assertIn("configureAudio(task.audio)", content)
        self.assertIn('control.getAttribute(attribute)', content)

    def test_option_controls_skip_matching_current_state(self):
        content = (bundled_extension_source() / "content.js").read_text(encoding="utf-8")
        self.assertIn('control.getAttribute("aria-valuetext")', content)
        self.assertIn('if (desired.some(label => current === label || current.includes(label))) return false;', content)
        self.assertIn('await chooseOption(["480p","720p","1080p"]', content)
        self.assertIn('await chooseOption(["6s","10s","15s"]', content)
        self.assertIn('await chooseOption(["自动模式","auto","2:3","3:2","1:1","9:16","16:9"]', content)

    def test_windows_titlebar_matches_the_application_theme(self):
        source = inspect.getsource(apply_windows_titlebar_theme)
        self.assertIn('DWMWA_CAPTION_COLOR', source)
        self.assertIn('DWMWA_TEXT_COLOR', source)
        self.assertIn('colors["caption"]', source)
        self.assertIn('colors["text"]', source)
        self.assertIn('DWMWA_USE_IMMERSIVE_DARK_MODE', source)

    def test_all_dialogs_receive_themed_native_captions_without_renaming(self):
        animated = inspect.getsource(AnimatedDialog)
        preview = inspect.getsource(ImagePreviewDialog)
        change_log = inspect.getsource(ChangeLogDialog)
        guide = inspect.getsource(ExtensionGuideDialog)
        self.assertIn("apply_windows_titlebar_theme", animated)
        self.assertIn('native_titlebar_theme = "auto"', preview)
        self.assertIn("def apply_preview_color_theme", preview)
        self.assertIn('application_theme_mode() == "dark"', preview)
        self.assertIn('background:#FFFFFF', preview)
        self.assertIn('setWindowTitle("图片预览")', preview)
        self.assertIn('setWindowTitle("版本更新日志")', change_log)
        self.assertIn('setWindowTitle("安装 / 修复浏览器助手")', guide)

    def test_preview_defaults_to_app_light_theme_not_windows_dark_palette(self):
        from app import application_theme_mode
        app = QApplication.instance() or QApplication([])
        app.setProperty("themeMode", None)
        app.setProperty("colorTheme", None)
        app.setProperty("themePreference", "light")
        self.assertEqual(application_theme_mode(), "light")
        app.setProperty("themeMode", "dark")
        self.assertEqual(application_theme_mode(), "dark")
        app.setProperty("themeMode", None)

    def test_theme_menu_and_complete_dark_palette_are_available(self):
        build = inspect.getsource(MainWindow.build_ui)
        apply_theme = inspect.getsource(MainWindow.apply_color_theme)
        self.assertEqual(set(THEMES), {"light", "dark"})
        self.assertIn('("light", "浅色")', build)
        self.assertIn('("dark", "深色")', build)
        self.assertIn('("system", "跟随 Windows")', build)
        self.assertIn("QFrame#taskPanel", apply_theme)
        self.assertIn("QMenu::item:selected", apply_theme)
        self.assertIn("row.refresh_theme()", apply_theme)
        self.assertIn('apply_windows_titlebar_theme(self, "auto")', apply_theme)

    def test_main_window_preserves_full_layout_and_adds_half_screen_reflow(self):
        build = inspect.getsource(MainWindow.build_ui)
        window = inspect.getsource(MainWindow.__init__)
        compact = inspect.getsource(MainWindow.apply_half_screen_layout)
        self.assertIn("self.resize(1180, 760)", window)
        self.assertIn("self.setMinimumSize(860, 700)", window)
        self.assertIn("body = QHBoxLayout()", build)
        self.assertIn("buttons = QHBoxLayout()", build)
        self.assertIn('foot.setObjectName("commandBar")', build)
        self.assertIn("compact = width < 1050", compact)
        self.assertIn("self.header_panel.setFixedHeight(102)", compact)
        self.assertIn("self.header_panel.setFixedHeight(64)", compact)
        self.assertIn("self.footer_secondary_widget.show()", compact)
        self.assertIn("self.rebuild_header_layout(True)", compact)
        self.assertIn("self.rebuild_task_layout(True)", compact)
        self.assertIn("self.rebuild_footer_layout(True)", compact)
        header = inspect.getsource(MainWindow.rebuild_header_layout)
        self.assertIn("self.header_brand_widgets + (self.open_grok_button, self.install_button)", header)
        self.assertIn("(self.header_theme_spacer, self.connection, self.theme_button)", header)
        self.assertEqual(header.count("self.header_primary_layout.addStretch(1)"), 1)
        centered = inspect.getsource(MainWindow.add_centered_group)
        self.assertEqual(centered.count("layout.addStretch(1)"), 2)
        self.assertIn("layout.addWidget(widget)", centered)
        task = inspect.getsource(MainWindow.rebuild_task_layout)
        footer = inspect.getsource(MainWindow.rebuild_footer_layout)
        self.assertIn("self.add_centered_group(self.task_primary_layout, self.task_add_widgets)", task)
        self.assertIn("self.footer_primary_layout.addWidget(self.progress_box, 1)", footer)
        self.assertNotIn("self.add_centered_group(self.footer_primary_layout, self.footer_info_widgets)", footer)
        spacing = inspect.getsource(MainWindow.update_compact_group_spacing)
        self.assertIn("max(8, min(24", spacing)
        self.assertIn("max(6, min(18", spacing)
        self.assertIn("self.body_layout.setStretch(0, 4)", compact)
        self.assertIn("self.body_layout.setStretch(1, 3)", compact)
        self.assertIn('parameter_bar.setObjectName("parameterBar")', build)
        self.assertIn('right.setMinimumWidth(360)', build)
        self.assertIn("Qt.FocusPolicy.NoFocus", build)

    def test_settings_use_a_compact_horizontal_parameter_bar(self):
        build = inspect.getsource(MainWindow.build_ui)
        self.assertIn('centered=True, text_object_name="settingsTitleText"', build)
        self.assertIn("progress_box.setSizePolicy(QSizePolicy.Policy.Expanding", build)
        self.assertNotIn('self.settings_scroll = QScrollArea()', build)
        self.assertIn('self.prompt.setMinimumHeight(84)', build)
        self.assertIn('form.addWidget(self.prompt, 1)', build)
        self.assertNotIn('form.addStretch()', build)
        self.assertIn('parameter_bar = QFrame()', build)
        self.assertIn('parameter_bar.setFixedHeight(46)', build)
        self.assertIn('parameter_layout = QHBoxLayout(parameter_bar)', build)
        self.assertIn('parameter_layout.addWidget(widget, 1)', build)
        self.assertNotIn('self.combo([', build)
        for label in ('"图片用途"', '"音频"', '"分辨率"', '"时长"', '"比例"'):
            self.assertIn(label, build)
        bar_position = build.index('form.addWidget(parameter_bar)')
        apply_position = build.index('form.addWidget(apply_selected)')
        restore_position = build.index('form.addWidget(clear_override)')
        self.assertLess(bar_position, apply_position)
        self.assertLess(apply_position, restore_position)

    def test_image_setting_rules_are_enforced_in_the_ui(self):
        rules = inspect.getsource(MainWindow.apply_rules)
        collect = inspect.getsource(MainWindow.collect_defaults)
        self.assertIn('self.resolution.setOptionEnabled("1080p", first', rules)
        self.assertIn('if not first and self.resolution.currentText() == "1080p"', rules)
        self.assertIn('self.resolution.setCurrentText("720p")', rules)
        self.assertIn('self.ratio.setEnabled(not first)', rules)
        self.assertIn('self.ratio.setDisplayOverride("底图决定")', rules)
        self.assertIn('if usage != "首帧" and resolution == "1080p"', collect)
        self.assertIn('ratio = "自动" if usage == "首帧"', collect)

    def test_setting_selector_uses_full_width_menu_and_dynamic_audio_icon(self):
        app = QApplication.instance() or QApplication([])
        selector = SettingSelector("音频", ["开启", "关闭"], "audio", "#3A9A83", {
            "开启": ("audio", "#3A9A83"), "关闭": ("audio_off", "#8496A8"),
        })
        selector.resize(64, 38)
        enabled_icon = selector.icon().cacheKey()
        selector.setCurrentText("关闭")
        self.assertEqual(selector.currentText(), "关闭")
        self.assertNotEqual(enabled_icon, selector.icon().cacheKey())
        self.assertGreaterEqual(selector.menu.minimumWidth(), 132)
        selector.setOptionEnabled("开启", False, "测试原因")
        self.assertFalse(selector._actions["开启"].isEnabled())
        self.assertEqual([action.text() for action in selector.menu.actions()], ["开启", "关闭"])
        selector.deleteLater(); app.processEvents()

    def test_selector_rules_switch_between_first_frame_and_other_modes(self):
        app = QApplication.instance() or QApplication([])
        class Controls: pass
        controls = Controls()
        controls._applying_rules = False; controls._first_frame_active = False; controls._ratio_before_first = "1:1"
        controls.usage = SettingSelector("图片用途", ["首帧", "循环"], "image_tag", "#5B86D6")
        controls.resolution = SettingSelector("分辨率", ["480p", "720p", "1080p"], "monitor_focus", "#3F89B8")
        controls.ratio = SettingSelector("比例", ["自动", "1:1"], "crop", "#6F6CCB")
        controls.ratio.setCurrentText("1:1"); controls.resolution.setCurrentText("1080p")
        MainWindow.apply_rules(controls)
        self.assertFalse(controls.ratio.isEnabled())
        self.assertEqual(controls.ratio.text(), "底图决定")
        self.assertTrue(controls.resolution._actions["1080p"].isEnabled())
        controls.usage.setCurrentText("循环")
        MainWindow.apply_rules(controls)
        self.assertTrue(controls.ratio.isEnabled())
        self.assertEqual(controls.ratio.currentText(), "1:1")
        self.assertEqual(controls.resolution.currentText(), "720p")
        self.assertFalse(controls.resolution._actions["1080p"].isEnabled())
        for selector in (controls.usage, controls.resolution, controls.ratio): selector.deleteLater()
        app.processEvents()

    def test_short_height_uses_density_mode_instead_of_overlapping_controls(self):
        source = inspect.getsource(MainWindow.apply_settings_density)
        resize = inspect.getsource(MainWindow.resizeEvent)
        self.assertIn("compact_height = height < 740", source)
        self.assertIn("self.prompt.setMinimumHeight(84)", source)
        self.assertIn("self.prompt.setMaximumHeight(16777215)", source)
        self.assertIn("self.settings_note.setMaximumHeight(20 if compact_height else 16777215)", source)
        self.assertIn("self.apply_settings_density(event.size().width(), event.size().height())", resize)

    def test_thumbnail_decoder_preserves_landscape_and_portrait_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            landscape = Path(folder) / "landscape.png"
            portrait = Path(folder) / "portrait.png"
            Image.new("RGB", (160, 80), "blue").save(landscape)
            Image.new("RGB", (80, 160), "red").save(portrait)
            self.assertEqual(read_thumbnail_image(str(landscape), 72).size(), QSize(72, 36))
            self.assertEqual(read_thumbnail_image(str(portrait), 72).size(), QSize(36, 72))

    def test_thumbnail_frame_is_transparent_and_borderless(self):
        thumbnail_source = inspect.getsource(MainWindow.thumbnail_for)
        build_source = inspect.getsource(MainWindow.build_ui)
        self.assertIn("Qt.GlobalColor.transparent", thumbnail_source)
        self.assertNotIn('canvas.fill(QColor("#EDF4FB"))', thumbnail_source)
        self.assertIn("QLabel#taskThumbnail {{ background:transparent; border:0; }}", build_source)

    def test_common_image_formats_generate_real_thumbnails(self):
        import pillow_heif
        pillow_heif.register_heif_opener()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Image.new("RGB", (96, 54), "#E5A52D")
            formats = {
                "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
                "tiff": "TIFF", "avif": "AVIF", "heic": "HEIF",
            }
            for suffix, image_format in formats.items():
                path = root / f"sample.{suffix}"
                source.save(path, format=image_format)
                self.assertFalse(read_thumbnail_image(str(path)).isNull(), suffix)

    def test_thumbnail_plugins_are_packaged_with_full_size_baseline_preserved(self):
        source = inspect.getsource(MainWindow.__init__)
        self.assertNotIn("setMinimumHeight", source)
        self.assertIn("self.resize(1180, 760)", source)
        self.assertIn("self.setMinimumSize(860, 700)", source)
        spec = (Path(__file__).parent / "GrokBatch.spec").read_text(encoding="utf-8")
        for plugin in ("qjpeg.dll", "qgif.dll", "qtiff.dll", "qwebp.dll"):
            self.assertIn(plugin, spec)


if __name__ == "__main__":
    unittest.main()
