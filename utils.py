#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用辅助: Chrome 路径查找、安全 HTTP 客户端（免疫 NO_PROXY IPv6 解析崩溃）、端口探测、日志初始化"""
import os
import platform
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger


def find_chrome_path() -> Optional[str]:
    """跨平台查找本机 Chrome / Edge / Brave / Chromium。找不到返回 None。"""
    system = platform.system()
    candidates: list[str] = []

    if system == "Windows":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env, "")
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
                candidates.append(os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"))
                candidates.append(os.path.join(base, "Chromium", "Application", "chrome.exe"))
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env, "")
            if base:
                candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    elif system == "Darwin":
        home_apps = str(Path.home() / "Applications")
        for root in ("/Applications", home_apps):
            candidates.extend([
                f"{root}/Google Chrome.app/Contents/MacOS/Google Chrome",
                f"{root}/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                f"{root}/Brave Browser.app/Contents/MacOS/Brave Browser",
                f"{root}/Chromium.app/Contents/MacOS/Chromium",
            ])
    else:  # Linux
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
            "/usr/bin/microsoft-edge-stable",
            "/usr/bin/brave-browser",
            "/snap/bin/chromium",
        ]

    for p in candidates:
        if p and os.path.exists(p):
            return p

    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


def browser_check() -> Dict[str, Any]:
    """返回浏览器检测结果，供 API 调用。"""
    path = find_chrome_path()
    if path:
        low = path.lower()
        if "chrome" in low:
            name = "Chrome"
        elif "edge" in low:
            name = "Edge"
        elif "brave" in low:
            name = "Brave"
        else:
            name = "Chromium"
        return {"found": True, "path": path, "name": name}
    return {"found": False, "path": "", "name": ""}


def _detect_safe_proxy(target_url: str) -> Optional[str]:
    """安全提取环境变量代理地址，避免 httpx 在解析含裸 IPv6 (如 ::1) 的 NO_PROXY 时崩溃。"""
    try:
        parsed = urlparse(target_url)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local"):
            return None
        scheme = (parsed.scheme or "https").lower()
        keys = (
            ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
            if scheme == "https"
            else ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
        )
        for k in keys:
            val = (os.environ.get(k) or "").strip()
            if val and "://" in val:
                # httpx supports http://, https://, socks5://
                if val.startswith("socks5h://"):
                    val = "socks5://" + val[len("socks5h://"):]
                return val
    except Exception:
        return None
    return None


def make_quark_client(timeout: float = 30.0) -> httpx.Client:
    """创建直连夸克国内服务的 HTTP 客户端（禁用 trust_env 规避 NO_PROXY IPv6 崩溃与海外代理干扰）。"""
    return httpx.Client(trust_env=False, timeout=timeout, follow_redirects=True)


def make_ai_client(base_url: str, timeout: float = 60.0) -> httpx.Client:
    """创建 AI 接口客户端：禁用 httpx 原生有缺陷的 trust_env 解析，安全按需注入代理。"""
    proxy = _detect_safe_proxy(base_url)
    if proxy:
        try:
            return httpx.Client(trust_env=False, proxy=proxy, timeout=timeout, follow_redirects=True)
        except Exception as e:
            logger.warning(f"代理 {proxy} 初始化失败，回退直连: {e}")
    return httpx.Client(trust_env=False, timeout=timeout, follow_redirects=True)


def pick_free_port(preferred: int = 7788, max_tries: int = 20) -> int:
    """从 preferred 开始尝试，找到第一个可用端口。"""
    for port in range(preferred, preferred + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if sys.platform == "win32":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"未找到可用端口（从 {preferred} 起试了 {max_tries} 个）")


def setup_logger(log_dir: Path) -> None:
    """配置 loguru：stderr 彩色 + logs/YYYY-MM-DD.log 文件轮转"""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> <level>{level: <7}</level> <cyan>{name}</cyan> {message}",
    )
    logger.add(
        str(log_dir / "{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="1 day",
        retention="14 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{function}:{line} | {message}",
    )


def app_root() -> Path:
    """支持 PyInstaller 打包：运行时项目根目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()
