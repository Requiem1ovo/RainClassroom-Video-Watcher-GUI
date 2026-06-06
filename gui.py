from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path
from typing import Optional


# Flet 首次启动从 GitHub 下载客户端(~150 MB),内部走 urllib + ssl.create_default_context()。
# WindowsApps Python 不打包 CA 证书,精简镜像常缺 github.com 证书链,提前设 SSL_CERT_FILE 跳过系统存储。
def _fix_ssl_for_urllib() -> None:
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
    except ImportError:
        return
    bundle = certifi.where()
    if bundle and Path(bundle).is_file():
        os.environ["SSL_CERT_FILE"] = bundle
        os.environ.setdefault("SSL_CERT_DIR", str(Path(bundle).parent))


_fix_ssl_for_urllib()

import flet as ft  # noqa: E402  必须放在 _fix_ssl_for_urllib() 之后

from platforms import BrowserControllerError, create_browser_controller  # noqa: E402
from subprocess_runner import WatcherRunner  # noqa: E402

_logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_ASSETS = _HERE / "assets"
_MD3_PATH = _ASSETS / "md3_colors.json"

with _MD3_PATH.open("r", encoding="utf-8") as _f:
    _MD3 = json.load(_f)["light"]

LOG_LEVEL_COLORS = {
    "INFO": _MD3["on_surface"],
    "SUCCESS": _MD3["success"],
    "ERROR": _MD3["error"],
    "CRITICAL": _MD3["error"],
    "WARNING": _MD3["warning"],
}
DEFAULT_LOG_COLOR = _MD3["on_surface_variant"]

RAINCLASSROOM_URL = "https://{authority}/"

DEFAULT_AUTHORITY = "changjiang.yuketang.cn"
DEFAULT_XTBZ = "ykt"

_CN_FONT_FAMILY = "Microsoft YaHei UI"
_MONO_FONT_FAMILY = "Consolas"

def _build_theme() -> ft.Theme:
    return ft.Theme(
        font_family=_CN_FONT_FAMILY,
        color_scheme_seed=_MD3["primary"],
        color_scheme=ft.ColorScheme(
            primary=_MD3["primary"],
            on_primary=_MD3["on_primary"],
            primary_container=_MD3["primary_container"],
            on_primary_container=_MD3["on_primary_container"],
            secondary=_MD3["secondary"],
            on_secondary=_MD3["on_secondary"],
            secondary_container=_MD3["secondary_container"],
            on_secondary_container=_MD3["on_secondary_container"],
            tertiary=_MD3["tertiary"],
            on_tertiary=_MD3["on_tertiary"],
            tertiary_container=_MD3["tertiary_container"],
            on_tertiary_container=_MD3["on_tertiary_container"],
            error=_MD3["error"],
            on_error=_MD3["on_error"],
            error_container=_MD3["error_container"],
            on_error_container=_MD3["on_error_container"],
            surface=_MD3["surface"],
            on_surface=_MD3["on_surface"],
            on_surface_variant=_MD3["on_surface_variant"],
            outline=_MD3["outline"],
            outline_variant=_MD3["outline_variant"],
            shadow=ft.Colors.TRANSPARENT,
        ),
        use_material3=True,
    )


def _parse_log_line(line: str) -> tuple[str, str]:
    m = re.match(r"^\[(INFO|SUCCESS|ERROR|CRITICAL|WARNING|DEBUG)\]\s*(.*)$", line)
    if m:
        return m.group(1), m.group(2)

    if line.startswith((" ", "\t")):
        return "CONT", line
    return "INFO", line


def _color_for_level(level: str) -> str:
    return LOG_LEVEL_COLORS.get(level, DEFAULT_LOG_COLOR)


def _short_ssl_error(e: BaseException) -> str:
    """把 urllib/ssl 的多层嵌套错误压成一行。"""
    inner = getattr(e, "reason", None) or getattr(e, "args", [None])[0] or e
    msg = str(inner).strip()
    if len(msg) > 240:
        msg = msg[:240] + "..."
    return msg or e.__class__.__name__


def _show_snack(page: ft.Page, message: str, *, error: bool = False) -> None:
    page.show_dialog(
        ft.SnackBar(
            content=ft.Text(message, color=_MD3["on_primary"]),
            bgcolor=_MD3["error"] if error else _MD3["primary"],
        )
    )


class HomeView:
    """主窗口:参数输入 + 浏览器模式切换。"""

    def __init__(self, page: ft.Page, app: "App") -> None:
        self.page = page
        self.app = app

        self.tf_authority = ft.TextField(
            label="authority",
            value=DEFAULT_AUTHORITY,
            hint_text="Free Software",
            border_radius=8,
            expand=True,
        )
        self.tf_classroom_id = ft.TextField(
            label="classroom_id",
            value="",
            hint_text="课程 ID(URL 末尾数字)",
            keyboard_type=ft.KeyboardType.NUMBER,
            border_radius=8,
            expand=True,
        )
        self.tf_sessionid = ft.TextField(
            label="sessionid",
            value="",
            hint_text="浏览器 Cookie 中的 sessionid",
            password=True,
            can_reveal_password=True,
            border_radius=8,
            expand=True,
        )
        self.tf_csrf = ft.TextField(
            label="csrftoken",
            value="",
            hint_text="浏览器 Cookie 中的 csrftoken",
            password=True,
            can_reveal_password=True,
            border_radius=8,
            expand=True,
        )
        self.tf_xtbz = ft.TextField(
            label="xtbz",
            value=DEFAULT_XTBZ,
            hint_text="https://github.com/Requiem1ovo/RainClassroom-Video-Watcher-GUI",
            border_radius=8,
            expand=True,
        )
        self._loading_browser = False
        self._browser_supported = sys.platform.startswith("win")
        self._browser_btn = ft.OutlinedButton(
            "浏览器" if self._browser_supported else "浏览器(仅 Windows)",
            icon=ft.Icons.LANGUAGE,
            disabled=not self._browser_supported,
            tooltip=(
                "启动 Edge 并自动获取 Cookie"
                if self._browser_supported
                else "浏览器模式目前仅在 Windows 上可用"
            ),
            style=ft.ButtonStyle(
                padding=ft.Padding(24, 16, 24, 16),
                side=ft.BorderSide(1, _MD3["primary"]),
            ),
            on_click=self._on_browser_clicked,
            expand=True,
        )

        self.view = ft.View(
            route="/",
            padding=24,
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.MainAxisAlignment.CENTER,
            controls=[
                ft.Column(
                    [
                        self.tf_authority,
                        self.tf_classroom_id,
                        self.tf_sessionid,
                        self.tf_csrf,
                        self.tf_xtbz,
                    ],
                    spacing=16,
                ),
                ft.Container(height=24),
                ft.Row(
                    [
                        ft.FilledButton(
                            "运行",
                            icon=ft.Icons.PLAY_ARROW,
                            style=ft.ButtonStyle(
                                bgcolor=_MD3["primary"],
                                color=_MD3["on_primary"],
                                padding=ft.Padding(24, 16, 24, 16),
                            ),
                            on_click=self._on_run_clicked,
                            expand=True,
                        ),
                        self._browser_btn,
                    ],
                    spacing=16,
                ),
            ],
        )

    def _read_params(self) -> Optional[dict]:
        authority = (self.tf_authority.value or "").strip() or DEFAULT_AUTHORITY
        xtbz = (self.tf_xtbz.value or "").strip() or DEFAULT_XTBZ
        classroom_id_raw = (self.tf_classroom_id.value or "").strip()
        sessionid = (self.tf_sessionid.value or "").strip()
        csrf = (self.tf_csrf.value or "").strip()

        if not classroom_id_raw:
            _show_snack(self.page, "请填写 classroom_id", error=True)
            self.page.run_task(self.tf_classroom_id.focus)
            return None
        if not sessionid:
            _show_snack(self.page, "请填写 sessionid", error=True)
            self.page.run_task(self.tf_sessionid.focus)
            return None
        if not csrf:
            _show_snack(self.page, "请填写 csrftoken", error=True)
            self.page.run_task(self.tf_csrf.focus)
            return None
        try:
            classroom_id = int(classroom_id_raw)
        except ValueError:
            _show_snack(self.page, "classroom_id 必须是数字", error=True)
            self.page.run_task(self.tf_classroom_id.focus)
            return None

        return {
            "authority": authority,
            "xtbz": xtbz,
            "classroom_id": classroom_id,
            "sessionid": sessionid,
            "csrf_token": csrf,
        }

    def _on_run_clicked(self, e: ft.ControlEvent) -> None:
        params = self._read_params()
        if params is None:
            return
        self.app.open_log_window(**params)

    def _on_browser_clicked(self, e: ft.ControlEvent) -> None:
        if self._loading_browser:
            return
        self._loading_browser = True
        self._browser_btn.disabled = True
        self._browser_btn.icon = None
        self._browser_btn.content = ft.Row(
            [
                ft.ProgressRing(
                    width=16,
                    height=16,
                    stroke_width=2,
                    color=_MD3["primary"],
                ),
                ft.Text("浏览器", color=_MD3["primary"]),
            ],
            spacing=8,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        self.page.update()
        authority = (self.tf_authority.value or "").strip() or DEFAULT_AUTHORITY
        self.app.open_browser_view(authority)

    def on_return_to_home(self) -> None:
        """路由回到 / 时由 App 回调,还原浏览器按钮。"""
        if not self._loading_browser:
            return
        self._loading_browser = False
        self._browser_btn.disabled = False
        self._browser_btn.icon = ft.Icons.LANGUAGE
        self._browser_btn.content = ft.Text("浏览器")
        self.page.update()


class BrowserView:
    """浏览器子页面:启动 Edge + 抓 Cookie。"""

    def __init__(self, page: ft.Page, app: "App", authority: str) -> None:
        self.page = page
        self.app = app
        self.authority = authority

        self._cookie_button = ft.FilledButton(
            "获取Cookie",
            icon=ft.Icons.DOWNLOAD,
            disabled=True,  # 浏览器在 on_mount 异步启动,期间禁用避免误点
            style=ft.ButtonStyle(
                bgcolor=_MD3["primary"],
                color=_MD3["on_primary"],
                shape=ft.RoundedRectangleBorder(radius=16),
                padding=ft.Padding(24, 16, 24, 16),
            ),
            on_click=self._on_get_cookie_clicked,
        )

        self.view = ft.View(
            route="/browser",
            padding=0,
            controls=[
                ft.AppBar(
                    leading=ft.IconButton(
                        ft.Icons.ARROW_BACK,
                        tooltip="返回主界面",
                        on_click=self._on_back_clicked,
                    ),
                    title=ft.Text(
                        "浏览器",
                        size=22,
                        weight=ft.FontWeight.W_400,
                        color=_MD3["on_surface_alt"],
                    ),
                    center_title=False,
                    bgcolor=_MD3["surface"],
                    elevation=0,
                ),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "正在使用WebDriver启动Edge。\n"
                                "请进入你想刷课的课程页面,再点击获取Cookie",
                                size=16,
                                color=_MD3["text_secondary"],
                                text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Container(height=24),
                            ft.Row(
                                [self._cookie_button],
                                alignment=ft.MainAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=8,
                        expand=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=24,
                    expand=True,
                ),
            ],
        )

    async def on_mount(self) -> None:
        _show_snack(self.page, "正在启动浏览器，首次使用可能需要下载驱动...")
        ok = await asyncio.to_thread(
            self.app.start_browser_if_needed, self.authority,
        )
        if not ok:
            _show_snack(self.page, "浏览器未启动,「获取Cookie」将无法工作", error=True)
            return
        self._cookie_button.disabled = False
        try:
            self.page.update()
        except Exception:  # pragma: no cover
            pass

    def _on_get_cookie_clicked(self, e: ft.ControlEvent) -> None:
        self.app.fetch_and_fill_cookies_and_close()

    def _on_back_clicked(self, e: ft.ControlEvent) -> None:
        self.app.close_browser_view()


class LogView:

    MAX_LINES = 5000  # 防止内存爆炸

    def __init__(self, page: ft.Page, app: "App", params: dict) -> None:
        self.page = page
        self.app = app
        self.params = params
        self._lines: list[ft.Text] = []
        self._log_column = ft.Column(
            controls=[],
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            auto_scroll=True,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._status_text = ft.Text(
            "运行中...",
            size=12,
            color=_MD3["on_surface_variant"],
        )
        self._close_button = ft.FilledTonalButton(
            "关闭",
            icon=ft.Icons.CLOSE,
            on_click=self._on_close_clicked,
            visible=False,
        )
        self._cancel_button = ft.OutlinedButton(
            "停止刷课",
            icon=ft.Icons.STOP,
            on_click=self._on_cancel_clicked,
        )

        self.view = ft.View(
            route="/log",
            padding=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            controls=[
                ft.AppBar(
                    leading=ft.IconButton(
                        ft.Icons.ARROW_BACK,
                        tooltip="返回主界面",
                        on_click=self._on_back_clicked,
                    ),
                    title=ft.Text(
                        "日志",
                        size=22,
                        weight=ft.FontWeight.W_400,
                        color=_MD3["on_surface"],
                    ),
                    center_title=False,
                    bgcolor=_MD3["surface"],
                    elevation=0,
                ),
                ft.Container(
                    content=self._log_column,
                    expand=True,
                    bgcolor=_MD3["surface_variant"],
                    border_radius=8,
                    padding=12,
                    margin=ft.Margin(16, 0, 16, 16),
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            self._status_text,
                            ft.Container(expand=True),
                            self._cancel_button,
                            self._close_button,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=ft.Padding(16, 0, 16, 16),
                ),
            ],
        )

        self.runner: Optional[WatcherRunner] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._done = False
        self._user_stopped = False
        self._last_level: str = "INFO"

        self._log_column.controls.append(
            ft.Text(
                "[INFO]免费软件，开源地址https://github.com/Requiem1ovo/RainClassroom-Video-Watcher-GUI",
                color=_color_for_level("INFO"),
                size=13,
                font_family=_MONO_FONT_FAMILY,
                selectable=True,
            )
        )

    def on_mount(self) -> None:
        """View 挂载时启动子进程。"""
        self.runner = WatcherRunner(**self.params)
        try:
            self.runner.start()
        except Exception as e:  # pragma: no cover
            self._append_line(f"[ERROR] 启动子进程失败: {e}")
            self._mark_done(success=False, exit_code=-1)
            return

        self._reader_thread = threading.Thread(
            target=self._read_loop, name="LogView-reader", daemon=True
        )
        self._reader_thread.start()

    def _read_loop(self) -> None:
        # 本地捕获 runner:close_log_window 会置 self.runner=None,必须在循环前抓牢否则 finally 出错。
        runner = self.runner
        if runner is None:
            return
        try:
            for line in runner.stream():
                self.page.run_task(self._append_line, line)
        except Exception as e:  # pragma: no cover
            self.page.run_task(self._append_line, f"[ERROR] 读取子进程输出失败: {e}")
        finally:
            try:
                exit_code = runner.wait()
            except Exception as e:  # pragma: no cover
                _logger.exception("等待子进程退出失败")
                exit_code = -1
            # 页面可能已断开(run_task 失败不应传播到线程外)
            try:
                self.page.run_task(self._mark_done, exit_code == 0, exit_code)
            except Exception:  # pragma: no cover
                _logger.exception("调度 _mark_done 失败")

    async def _append_line(self, line: str) -> None:
        level, text = _parse_log_line(line)
        if level == "CONT":
            color = _color_for_level(self._last_level)
        else:
            color = _color_for_level(level)
            self._last_level = level
        line_text = ft.Text(
            line,
            color=color,
            size=13,
            font_family=_MONO_FONT_FAMILY,
            selectable=True,
        )
        self._lines.append(line_text)
        if len(self._lines) > self.MAX_LINES:
            # list(kept) 解耦 controls 与 _lines 别名;r.page=None 释放已移除 Text 的页面引用。
            kept = self._lines[-self.MAX_LINES :]
            removed = self._lines[: -self.MAX_LINES]
            self._log_column.controls = list(kept)
            for r in removed:
                r.page = None
            self._lines = kept
        else:
            self._log_column.controls.append(line_text)
        self.page.update()

    async def _mark_done(self, success: bool, exit_code: int) -> None:
        if self._done:
            return
        self._done = True
        if self._user_stopped:
            self._status_text.value = "已停止"
            self._status_text.color = _MD3["on_surface_variant"]
            await self._append_line("[WARNING] 用户已停止刷课")
        elif success:
            self._status_text.value = "完成"
            self._status_text.color = _MD3["success"]
        else:
            self._status_text.value = f"异常退出(退出码 {exit_code})"
            self._status_text.color = _MD3["error"]
            await self._append_line(f"[ERROR] 进程异常退出,退出码 {exit_code}")
        self._cancel_button.visible = False
        self._close_button.visible = True
        self.page.update()

    def _mark_closed(self) -> None:
        """视图被外部关闭时调用,阻断 _read_loop.finally 后续 UI 更新。"""
        self._done = True

    @property
    def is_running(self) -> bool:
        """刷课进程是否仍在运行。"""
        return not self._done

    def _on_back_clicked(self, e: ft.ControlEvent) -> None:
        if not self._done:
            self._confirm_cancel_then_back()
        else:
            self.app.close_log_window()

    def _on_close_clicked(self, e: ft.ControlEvent) -> None:
        self.app.close_log_window()

    def _on_cancel_clicked(self, e: ft.ControlEvent) -> None:
        self._confirm_cancel_then_back()

    def _confirm_cancel_then_back(self) -> None:
        """弹出确认对话框,确认后终止子进程并返回主窗口。"""
        def on_yes(e: ft.ControlEvent) -> None:
            try:
                self.page.pop_dialog()
                if self.runner is not None:
                    self._user_stopped = True
                    # runner.terminate() 内部 wait(5s) 会阻塞 Flet 事件循环,改用后台线程。
                    threading.Thread(
                        target=self.runner.terminate,
                        daemon=True,
                        name="terminate-runner",
                    ).start()
                self.page.run_task(self._append_line, "[WARNING] 用户取消,正在终止子进程...")
                self.page.update()
            except Exception:  # pragma: no cover
                _logger.exception("确认停止按钮处理失败")

        def on_no(e: ft.ControlEvent) -> None:
            try:
                self.page.pop_dialog()
            except Exception:  # pragma: no cover
                _logger.exception("关闭确认对话框失败")

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认停止"),
            content=ft.Text("刷课进程仍在运行,确定要停止并关闭日志窗口吗?"),
            actions=[
                ft.TextButton("取消", on_click=on_no),
                ft.FilledButton("停止", on_click=on_yes, style=ft.ButtonStyle(bgcolor=_MD3["error"])),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dlg)


class App:
    """Flet 应用主对象:负责 View 切换与全局状态管理。"""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._setup_page()
        self.home_view = HomeView(page, self)
        self.log_view: Optional[LogView] = None
        self.browser_view: Optional[BrowserView] = None
        self._browser_controller = None

        def on_route_change(e: ft.RouteChangeEvent) -> None:
            if self.page.route == "/":
                self.home_view.on_return_to_home()
            self.page.update()

        self.page.on_route_change = on_route_change
        self.page.views.append(self.home_view.view)
        self.page.run_task(self.page.push_route, "/")

    def _setup_page(self) -> None:
        self.page.title = "雨课堂视频助手"
        self.page.theme = _build_theme()
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.padding = 0
        self.page.window.width = 560
        self.page.window.height = 760
        self.page.window.min_width = 480
        self.page.window.min_height = 600
        self.page.vertical_alignment = ft.MainAxisAlignment.START
        self.page.horizontal_alignment = ft.CrossAxisAlignment.START
        self.page.on_disconnect = self._cleanup
        self.page.on_window_event = self._on_window_event

    def _on_window_event(self, e: ft.WindowEvent) -> None:
        """用户关闭窗口时:若有运行中的 watcher 进程,弹出确认。"""
        if e.type == ft.WindowEventType.CLOSE:
            if self.log_view is not None and self.log_view.is_running:
                e.prevent_default()
                self._confirm_close_with_running_runner()

    def _confirm_close_with_running_runner(self) -> None:
        def on_yes(e: ft.ControlEvent) -> None:
            try:
                self.page.pop_dialog()
            except Exception:  # pragma: no cover
                _logger.exception("关闭确认退出对话框失败")
            self._terminate_watcher_runner()
            self.page.window.close()

        def on_no(e: ft.ControlEvent) -> None:
            try:
                self.page.pop_dialog()
            except Exception:  # pragma: no cover
                _logger.exception("关闭确认退出对话框失败")

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("确认退出"),
            content=ft.Text("刷课进程仍在运行,确定要退出并终止子进程吗?"),
            actions=[
                ft.TextButton("取消", on_click=on_no),
                ft.FilledButton("退出", on_click=on_yes, style=ft.ButtonStyle(bgcolor=_MD3["error"])),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dlg)

    def _terminate_watcher_runner(self) -> None:
        if self.log_view is not None and self.log_view.runner is not None:
            # runner.terminate() 内部 wait(5s) 会阻塞,在后台线程执行避免冻结事件循环。
            runner = self.log_view.runner
            threading.Thread(
                target=runner.terminate,
                daemon=True,
                name="terminate-watcher",
            ).start()

    def _cleanup(self, e=None) -> None:
        self._terminate_watcher_runner()
        if self._browser_controller is not None:
            try:
                self._browser_controller.quit()
            except Exception:  # pragma: no cover
                _logger.exception("关闭浏览器失败")
            self._browser_controller = None

    def open_log_window(self, **params) -> None:
        self.log_view = LogView(self.page, self, params)
        self.page.views.append(self.log_view.view)
        self.page.run_task(self.page.push_route, "/log")
        self.log_view.on_mount()

    def close_log_window(self) -> None:
        if self.log_view is None:
            return
        # _mark_closed 阻断 _read_loop.finally 对已卸载控件的无效更新。
        self.log_view._mark_closed()
        if self.log_view.runner is not None:
            try:
                self.log_view.runner.terminate()
            except Exception:  # pragma: no cover
                _logger.exception("关闭日志窗口时终止子进程失败")
            self.log_view.runner = None
        try:
            self.page.views.remove(self.log_view.view)
        except ValueError:
            pass
        self.log_view = None
        self.page.run_task(self.page.push_route, "/")

    def open_browser_view(self, authority: str) -> None:
        self.browser_view = BrowserView(self.page, self, authority)
        self.page.views.append(self.browser_view.view)
        self.page.run_task(self.page.push_route, "/browser")
        self.page.run_task(self.browser_view.on_mount)

    def close_browser_view(self) -> None:
        if self.browser_view is None:
            return
        try:
            self.page.views.remove(self.browser_view.view)
        except ValueError:
            pass
        self.browser_view = None
        self.page.run_task(self.page.push_route, "/")

    def start_browser_if_needed(self, authority: str) -> bool:
        """惰性创建 + 启动浏览器;成功返回 True。"""
        if self._browser_controller is None:
            try:
                self._browser_controller = create_browser_controller()
            except (NotImplementedError, BrowserControllerError) as e:
                _show_snack(self.page, str(e), error=True)
                return False

        url = RAINCLASSROOM_URL.format(authority=authority)
        try:
            self._browser_controller.start_browser(url)
        except BrowserControllerError as e:
            _show_snack(self.page, str(e), error=True)
            return False
        return True

    def fetch_and_fill_cookies_and_close(self) -> None:
        """从 Edge 抓 Cookie,回填主页字段,关闭浏览器子页面。"""
        if self._browser_controller is None:
            _show_snack(self.page, "请先启动浏览器", error=True)
            return
        try:
            data = self._browser_controller.get_cookies()
        except BrowserControllerError as e:
            _show_snack(self.page, str(e), error=True)
            return

        if not data.is_valid:
            _show_snack(
                self.page,
                "未找到 sessionid/csrf token,请确认已登录",
                error=True,
            )
            return

        if data.classroom_id is None:
            _show_snack(
                self.page,
                "未获取到 classroom_id,请进入课程页面",
                error=True,
            )
            return

        home = self.home_view
        home.tf_sessionid.value = data.sessionid or ""
        home.tf_csrf.value = data.csrftoken or ""
        if data.xtbz:
            home.tf_xtbz.value = data.xtbz
        else:
            home.tf_xtbz.value = DEFAULT_XTBZ
        home.tf_classroom_id.value = str(data.classroom_id)
        home.view.update()
        _show_snack(self.page, "Cookie 已填入")
        self.close_browser_view()


def _suppress_benign_urllib3_warnings() -> None:
    class _Filter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "Connection pool is full" not in record.getMessage()

    logging.getLogger("urllib3.connectionpool").addFilter(_Filter())


def main(page: ft.Page) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _suppress_benign_urllib3_warnings()
    _FLET_PIDS: set[int] = set()

    def register_flet_pid(pid: int) -> None:
        """注册一个由本进程拉起的 flet 子进程 PID,用于退出兜底。"""
        if pid > 0:
            _FLET_PIDS.add(pid)

    if sys.platform == "win32":
        def _cleanup_flet() -> None:
            if not _FLET_PIDS:
                return
            for pid in list(_FLET_PIDS):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        check=False,
                        capture_output=True,
                    )
                except Exception:  # pragma: no cover
                    pass

        atexit.register(_cleanup_flet)
    App(page)


if __name__ == "__main__":
    try:
        ft.run(main)
    except (urllib.error.URLError, OSError) as e:
        # SSLCertVerificationError 是 OSError 的子类
        msg = str(e)
        if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg:
            print(
                "\n[FATAL] Flet 首次启动需要从 GitHub 下载桌面客户端,但 SSL 证书验证失败。",
                file=sys.stderr,
            )
            print(
                "        这通常发生在 WindowsApps (Microsoft Store) Python 上 ——\n"
                "        该发行版依赖 Windows 系统证书存储,而精简镜像里 github.com 的证书链缺失。\n",
                file=sys.stderr,
            )
            print("        解决办法(任选其一):", file=sys.stderr)
            print("        1. 升级到 python.org 官方安装版的 Python 3.10+。", file=sys.stderr)
            print(
                "        2. 在本项目目录下执行:  python -m pip install certifi\n"
                "           本程序会自动设置 SSL_CERT_FILE 环境变量。",
                file=sys.stderr,
            )
            print(
                "        3. 开启代理软件\n",
                file=sys.stderr,
            )
            print(
                "\n        原始错误: " + _short_ssl_error(e),
                file=sys.stderr,
            )
            sys.exit(2)
        raise
