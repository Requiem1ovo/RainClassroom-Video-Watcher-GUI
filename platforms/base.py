from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BrowserControllerError(RuntimeError):
    """浏览器控制器相关错误。"""


@dataclass(frozen=True)
class CookieData:
    """从浏览器提取的 Cookie 数据"""

    sessionid: str | None = None
    csrftoken: str | None = None
    xtbz: str | None = None
    classroom_id: int | None = None
    raw_cookies: dict[str, str] = field(default_factory=dict)
    current_url: str = ""

    @property
    def is_valid(self) -> bool:
        """必需字段都已成功获取。"""
        return bool(self.sessionid and self.csrftoken)


class BrowserController(ABC):
    def __init__(self) -> None:
        self._started = False

    @abstractmethod
    def start_browser(self, url: str) -> None:
        """启动浏览器并导航至指定 URL。"""

    @abstractmethod
    def get_cookies(self) -> CookieData:
        raise NotImplementedError

    @abstractmethod
    def quit(self) -> None:
        """关闭浏览器并释放资源。"""
        raise NotImplementedError

    def __enter__(self) -> "BrowserController":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.quit()
