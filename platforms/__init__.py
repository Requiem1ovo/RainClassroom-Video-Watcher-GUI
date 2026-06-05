
from .base import BrowserController, BrowserControllerError, CookieData

__all__ = ["BrowserController", "BrowserControllerError", "CookieData", "create_browser_controller"]


def create_browser_controller() -> "BrowserController":
    """根据当前操作系统选择合适的浏览器控制器实现。"""
    import sys
    if sys.platform.startswith("win"):
        from .windows import WindowsBrowserController
        return WindowsBrowserController()
    raise NotImplementedError(
        f"当前平台 '{sys.platform}' 尚未实现 BrowserController。"
        "目前仅支持 Windows"
    )
