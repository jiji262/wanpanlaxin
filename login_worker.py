#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuarkLoginWorker - 后台线程托管 headless DrissionPage 扫码登录流程：
  - 懒加载 DrissionPage，缺失依赖或无浏览器时给出清晰引导
  - 抓取夸克登录页二维码（canvas / img → DataURL）
  - 定期或按需刷新页面重新获取二维码（避免旧 canvas 过期不更新）
  - 使用直连 httpx 客户端（trust_env=False）轮询登录状态并获取账号信息
  - 登录成功后保存 cookies 并触发缓存失效回调
"""
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger

from utils import find_chrome_path, make_quark_client

QUARK_DOMAINS = (".quark.cn", "quark.cn", "pan.quark.cn", "drive-pc.quark.cn", "drive.quark.cn")
QUARK_CHECK_URL = "https://drive-pc.quark.cn/1/clouddrive/config?pr=ucpro&fr=pc&uc_param_str="
QUARK_ACCOUNT_URL = "https://pan.quark.cn/account/info?fr=pc&platform=pc"
QUARK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

QR_JS = """
const el = document.querySelector('.qrcode-display canvas')
      || Array.from(document.querySelectorAll('canvas')).find(c => c.width >= 100 && c.height >= 100);
if (el) return el.toDataURL('image/png');
const img = document.querySelector('.qrcode-display img, img[src*="qrcode"]');
if (img && img.src) return img.src;
return null;
"""


class QuarkLoginWorker:
    """单例后台扫码登录管理器。状态机:
      idle → starting → waiting_scan → logged_in
                                     ↘ failed
    """

    def __init__(
        self,
        cookies_path: Path,
        poll_interval: float = 2.5,
        qr_refresh_interval: float = 65.0,
        timeout: float = 300.0,
        on_login_success: Optional[Callable[[], None]] = None,
    ):
        self.cookies_path = cookies_path
        self.poll_interval = poll_interval
        self.qr_refresh_interval = qr_refresh_interval
        self.timeout = timeout
        self.on_login_success = on_login_success

        self._lock = threading.Lock()
        self._state = "idle"
        self._qr_data_url: Optional[str] = None
        self._error: Optional[str] = None
        self._account: Optional[Dict[str, str]] = None
        self._started_at: float = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()

    # ---- public read API ----
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            remaining = 0
            if self._state in ("starting", "waiting_scan") and self._started_at > 0:
                remaining = max(0, int(self.timeout - (time.time() - self._started_at)))
            return {
                "state": self._state,
                "qr_data_url": self._qr_data_url,
                "error": self._error,
                "account": self._account,
                "remaining_seconds": remaining,
            }

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- public control API ----
    def start(self) -> None:
        with self._lock:
            if self.is_running():
                logger.info("login worker already running, skip start")
                return
            self._state = "starting"
            self._qr_data_url = None
            self._error = None
            self._account = None
            self._started_at = time.time()
            self._stop_event.clear()
            self._refresh_event.clear()
            self._thread = threading.Thread(target=self._run, name="QuarkLoginWorker", daemon=True)
            self._thread.start()

    def request_refresh(self) -> None:
        """请求立即刷新二维码；若未运行则直接启动。"""
        if not self.is_running():
            self.start()
            return
        self._refresh_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            if self._state != "logged_in":
                self._state = "idle"

    # ---- worker thread ----
    def _set_state(
        self,
        state: str,
        qr: Optional[str] = None,
        error: Optional[str] = None,
        account: Optional[Dict[str, str]] = None,
    ) -> None:
        with self._lock:
            self._state = state
            if qr is not None:
                self._qr_data_url = qr
            if error is not None:
                self._error = error
            if account is not None:
                self._account = account

    def _run(self) -> None:
        logger.info("login worker started")
        page = None
        try:
            page = self._launch_page()
            logger.info("chrome launched, navigating to pan.quark.cn")
            page.get("https://pan.quark.cn/")

            # 等待登录二维码渲染（最多等待 6 秒）
            qr_ok = False
            for _ in range(12):
                if self._stop_event.is_set():
                    break
                time.sleep(0.5)
                if self._extract_and_store_qr(page):
                    qr_ok = True
                    break

            if not qr_ok and not self._stop_event.is_set():
                logger.warning("initial QR extract empty, retrying after page refresh")
                page.refresh()
                time.sleep(2.0)
                self._extract_and_store_qr(page)

            self._set_state("waiting_scan")

            start = time.time()
            last_qr_refresh = start
            attempt = 0

            with make_quark_client(timeout=8.0) as client:
                while not self._stop_event.is_set() and (time.time() - start) < self.timeout:
                    attempt += 1

                    # 手动或定时刷新二维码（真正刷新页面以获取新二维码）
                    need_refresh = self._refresh_event.is_set() or (
                        time.time() - last_qr_refresh > self.qr_refresh_interval
                    )
                    if need_refresh:
                        self._refresh_event.clear()
                        try:
                            page.refresh()
                            for _ in range(8):
                                time.sleep(0.4)
                                if self._extract_and_store_qr(page):
                                    break
                            last_qr_refresh = time.time()
                            logger.debug(f"QR refreshed at attempt {attempt}")
                        except Exception as e:
                            logger.warning(f"QR refresh failed: {e}")

                    # 检查登录状态
                    try:
                        cookies = self._extract_quark_cookies(page)
                        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                        if cookie_str and self._is_logged_in(client, cookie_str):
                            logger.info(f"login detected at attempt {attempt} ({len(cookies)} cookies)")
                            self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(self.cookies_path, "w", encoding="utf-8") as f:
                                f.write(cookie_str + "\n")
                            acct = self._fetch_account_info(client, cookie_str)
                            self._set_state("logged_in", account=acct)
                            if self.on_login_success:
                                try:
                                    self.on_login_success()
                                except Exception:
                                    pass
                            return
                    except Exception as e:
                        logger.warning(f"login check failed: {e}")

                    self._stop_event.wait(self.poll_interval)

            if self._stop_event.is_set():
                logger.info("login worker stopped by user")
                self._set_state("idle")
            else:
                logger.warning("login worker timeout")
                self._set_state("failed", error=f"超过 {int(self.timeout)} 秒未扫码，请点击刷新重试")
        except Exception as e:
            logger.exception("login worker crashed")
            self._set_state("failed", error=f"{e}")
        finally:
            if page is not None:
                try:
                    page.quit()
                except Exception:
                    pass
            logger.info("login worker exited")

    # ---- helpers ----
    def _launch_page(self):
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
            from DrissionPage.errors import BrowserConnectError
        except ImportError as e:
            raise RuntimeError(
                "缺少 DrissionPage 依赖，请运行 pip install -r requirements.txt（或切换至「手动粘贴 Cookie」登录）"
            ) from e

        chrome = find_chrome_path()
        if not chrome:
            raise RuntimeError(
                "未检测到本机 Chrome / Edge 浏览器，请安装 Chrome，或使用「手动粘贴 Cookie」方式登录"
            )

        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                co = ChromiumOptions()
                co.auto_port()
                co.headless(True)
                co.set_argument("--no-first-run")
                co.set_argument("--no-default-browser-check")
                co.set_argument("--disable-background-networking")
                co.set_argument("--disable-component-update")
                co.set_argument("--disable-default-apps")
                co.set_argument("--disable-sync")
                co.set_argument("--metrics-recording-only")
                co.set_argument("--no-proxy-server")
                co.set_argument("--window-size=1280,900")
                co.set_browser_path(chrome)

                logger.info(f"launching headless browser ({chrome}) attempt {attempt}/3")
                return ChromiumPage(co)
            except BrowserConnectError as e:
                last_error = e
                logger.warning(f"browser connect failed on attempt {attempt}/3: {e}")
                if attempt < 3:
                    time.sleep(1.5)

        assert last_error is not None
        raise last_error

    def _extract_and_store_qr(self, page) -> bool:
        try:
            data_url = page.run_js(QR_JS)
            if data_url:
                with self._lock:
                    self._qr_data_url = data_url
                return True
        except Exception:
            pass
        return False

    def _extract_quark_cookies(self, page) -> Dict[str, str]:
        raw = page.cookies(all_domains=True, all_info=False)
        out: Dict[str, str] = {}
        for c in raw:
            domain = (c.get("domain") or "").lstrip(".")
            if not any(domain == d.lstrip(".") or domain.endswith(d) for d in QUARK_DOMAINS):
                continue
            name = c.get("name")
            value = c.get("value", "")
            if name and name not in out:
                out[name] = value
        return out

    def _is_logged_in(self, client, cookie_str: str) -> bool:
        try:
            r = client.get(
                QUARK_CHECK_URL,
                headers={
                    "user-agent": QUARK_UA,
                    "origin": "https://pan.quark.cn",
                    "referer": "https://pan.quark.cn/",
                    "cookie": cookie_str,
                    "accept": "application/json, text/plain, */*",
                },
            )
            if r.status_code != 200:
                return False
            return r.json().get("status") == 200
        except Exception:
            return False

    def _fetch_account_info(self, client, cookie_str: str) -> Dict[str, str]:
        try:
            r = client.get(
                QUARK_ACCOUNT_URL,
                headers={
                    "user-agent": QUARK_UA,
                    "origin": "https://pan.quark.cn",
                    "referer": "https://pan.quark.cn/",
                    "cookie": cookie_str,
                    "accept": "application/json, text/plain, */*",
                },
            )
            if r.status_code == 200:
                data = r.json().get("data") or {}
                nickname = data.get("nickname") or data.get("mobile") or ""
                if nickname:
                    return {"username": str(nickname)}
        except Exception:
            pass
        return {}
