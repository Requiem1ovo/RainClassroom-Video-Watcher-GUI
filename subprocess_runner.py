"""
子进程运行器
"""
from __future__ import annotations

import logging
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_WATCHER_SCRIPT = _HERE / "RainClassroomVideoWatcher.py"


_CHILD_ENV_ENCODING = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}


_SENSITIVE_FLAGS = frozenset({"--sessionid", "--csrf-token", "--xtbz"})


_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(session_?id|csrf_?token|xtbz)\s*=\s*['\"]?([^\s'\"&,;)]+)"
)


def _sanitize_argv(cmd: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for token in cmd:
        if skip_next:
            out.append("***")
            skip_next = False
        elif token in _SENSITIVE_FLAGS:
            out.append(token)
            skip_next = True
        else:
            out.append(token)
    return out


def _sanitize_line(line: str) -> str:
    return _SENSITIVE_ASSIGNMENT_RE.sub(r"\1=***", line)


class WatcherRunner:
    """
    封装 RainClassroomVideoWatcher.py 子进程。

    使用方式::

        runner = WatcherRunner(authority="...", sessionid="...", ...)
        for line in runner.stream():
            handle(line)
        runner.wait()
    """

    def __init__(
        self,
        authority: str,
        sessionid: str,
        csrf_token: str,
        xtbz: str,
        classroom_id: int,
        python: Optional[str] = None,
    ) -> None:
        self.authority = authority
        self.sessionid = sessionid
        self.csrf_token = csrf_token
        self.xtbz = xtbz
        self.classroom_id = classroom_id
        self.python = python or sys.executable
        self._process: Optional[subprocess.Popen] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._terminated = False
        self._terminate_lock = threading.Lock()

    def _build_cmd(self) -> list[str]:
        return [
            self.python,
            str(_WATCHER_SCRIPT),
            "--authority", self.authority,
            "--sessionid", self.sessionid,
            "--csrf-token", self.csrf_token,
            "--xtbz", self.xtbz,
            "--classroom-id", str(self.classroom_id),
        ]

    def start(self) -> None:
        """启动子进程(非阻塞)。"""
        if self._process is not None:
            raise RuntimeError("子进程已在运行")
        if not _WATCHER_SCRIPT.exists():
            raise FileNotFoundError(f"未找到刷课脚本: {_WATCHER_SCRIPT}")
        if not Path(self.python).is_file():
            raise FileNotFoundError(f"python 解释器不存在: {self.python}")

        cmd = self._build_cmd()
        _logger.info("启动子进程: %s", " ".join(shlex.quote(c) for c in _sanitize_argv(cmd)))
        env = os.environ.copy()
        env.update(_CHILD_ENV_ENCODING)
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            thread = threading.Thread(
                target=self._read_stdout,
                args=(process,),
                name="WatcherRunner-stdout",
                daemon=True,
            )
            thread.start()
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        self._process = process
        self._stdout_thread = thread

    def _read_stdout(self, process: subprocess.Popen) -> None:
        """后台线程:逐行读取子进程 stdout,放入队列。"""
        stdout = process.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                line = _sanitize_line(raw_line.rstrip("\n").rstrip("\r"))
                self._lines.put(line)
        except Exception:  # pragma: no cover
            _logger.exception("读取子进程 stdout 时发生错误")

    def stream(self, *, poll_interval: float = 0.1) -> Iterator[str]:
        """
        流式迭代子进程输出行。

        每当有新行产生时立即 yield;无新行时短暂阻塞,等待子进程结束。
        """
        if self._process is None:
            self.start()
        process = self._process
        assert process is not None

        while True:
            try:
                yield self._lines.get(timeout=poll_interval)
            except queue.Empty:
                if process.poll() is not None:
                    while True:
                        try:
                            yield self._lines.get_nowait()
                        except queue.Empty:
                            return

    def wait(self, timeout: Optional[float] = None) -> int:
        """等待子进程结束,返回退出码。"""
        if self._process is None:
            raise RuntimeError("子进程未启动")
        return self._process.wait(timeout=timeout)

    def terminate(self) -> None:
        """终止子进程(跨平台安全,幂等)。"""
        with self._terminate_lock:
            if self._process is None or self._terminated:
                return
            self._terminated = True
            process = self._process
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _logger.warning("子进程未在 5 秒内退出,强制 kill")
                process.kill()
                process.wait()
        except Exception:  # pragma: no cover
            _logger.exception("终止子进程时发生错误")

    def close(self) -> None:
        """释放资源"""
        self.terminate()
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=2)

    def __enter__(self) -> "WatcherRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
