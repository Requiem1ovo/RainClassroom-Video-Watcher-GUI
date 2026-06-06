from __future__ import annotations

import logging
import re
import subprocess
import sys
from typing import Optional

try:
    from selenium import webdriver
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.common.exceptions import WebDriverException
    _SELENIUM_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SELENIUM_AVAILABLE = False

from .base import BrowserController, BrowserControllerError, CookieData

_logger = logging.getLogger(__name__)


def _short_webdriver_error(e: WebDriverException) -> str:
    msg = str(e).strip()
   
    if msg.startswith("Message:"):
        msg = msg[len("Message:"):].lstrip()
    if len(msg) > 200:
        msg = msg[:200] + "..."
    return msg or e.__class__.__name__


def _kill_process_tree(pid: int) -> None:
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            check=False,
            capture_output=True,
        )
    except Exception:  # pragma: no cover
        _logger.exception("taskkill 失败 (pid=%s)", pid)


class WindowsBrowserController(BrowserController):
    """Windows 平台:通过 Selenium 4 + Selenium Manager 启动 Edge 浏览器。"""

    def __init__(self) -> None:
        super().__init__()
        self._driver: Optional["webdriver.Edge"] = None

    def _is_driver_alive(self) -> bool:
        """检测 driver 对象是否仍可正常通信。"""
        if self._driver is None:
            return False
        try:
            _ = self._driver.current_url
            return True
        except WebDriverException:
            return False

    def _reset_driver(self) -> None:
        """重置已失效的 driver 状态。"""
        if self._driver is not None:
            try:
                service = getattr(self._driver, "service", None)
                if service is not None and getattr(service, "process", None) is not None:
                    pid = service.process.pid
                    if pid:
                        _kill_process_tree(pid)
            except Exception:  # pragma: no cover
                _logger.exception("清理失效 driver 进程失败")
            self._driver = None
            self._started = False

    def start_browser(self, url: str) -> None:
        """启动 Edge 浏览器并导航至 `url`。"""
        if not _SELENIUM_AVAILABLE:
            raise BrowserControllerError(
                "selenium 未安装,请先执行 'pip install -r requirements.txt'"
            )
        if self._is_driver_alive():
            _logger.info("浏览器已启动,直接导航至 %s", url)
            self._driver.get(url)
            return
        elif self._driver is not None:
            _logger.info("浏览器已关闭,重新启动")
            self._reset_driver()

        # Selenium Manager 自动解析本机 PATH / 缓存 / 联网下载
        service = EdgeService()
        try:
            self._driver = webdriver.Edge(service=service)
        except WebDriverException as e:
            _logger.exception("启动 Edge 失败")
            raise BrowserControllerError(
                "启动 Edge 失败:"
                "Selenium Manager 未能自动准备 msedgedriver。"
                "请确认已安装 Microsoft Edge 浏览器并能访问网络,"
                "或手动下载 msedgedriver 并加入 PATH 后重试。"
                f"({_short_webdriver_error(e)})"
            ) from e

        self._driver.get(url)
        self._started = True
        _logger.info("Edge 已启动,当前 URL: %s", url)

    def get_cookies(self) -> CookieData:
        """获取当前页面 Cookie 并尝试解析 classroom_id。"""
        if self._driver is None:
            raise BrowserControllerError("浏览器尚未启动,请先调用 start_browser()")

        # 用户可能手动关 Edge → WebDriver 协议已断开
        try:
            raw: dict[str, str] = {}
            for c in self._driver.get_cookies():
                raw[c.get("name", "")] = c.get("value", "")
            current_url = self._driver.current_url or ""
        except WebDriverException as e:
            _logger.exception("获取 Cookie 失败:浏览器可能已关闭")
            self._driver = None
            self._started = False
            raise BrowserControllerError(
                "浏览器已关闭,无法获取 Cookie。"
                "请重新点击「浏览器」按钮启动 Edge。"
                f"({_short_webdriver_error(e)})"
            ) from e

        classroom_id: int | None = None
        m = re.search(r"/(\d+)(?:/?$|\?|#)", current_url)
        if m:
            try:
                classroom_id = int(m.group(1))
            except ValueError:
                classroom_id = None

        return CookieData(
            sessionid=raw.get("sessionid"),
            csrftoken=raw.get("csrftoken"),
            xtbz=raw.get("xtbz") or raw.get("ykt"),
            classroom_id=classroom_id,
            raw_cookies=raw,
            current_url=current_url,
        )

    def quit(self) -> None:
        """关闭 Edge 浏览器并释放资源。"""
        if self._driver is None:
            return
        driver = self._driver
        self._driver = None
        self._started = False
        try:
            driver.quit()
        except Exception:  # pragma: no cover
            _logger.exception("关闭 Edge 浏览器时发生错误")
            # quit() 失败 → 兜底: taskkill 杀 msedgedriver
            try:
                service = getattr(driver, "service", None)
                if service is not None and getattr(service, "process", None) is not None:
                    pid = service.process.pid
                    if pid:
                        _kill_process_tree(pid)
            except Exception:  # pragma: no cover
                _logger.exception("获取 msedgedriver pid 失败")
        finally:
            _logger.info("Edge 浏览器已关闭")
