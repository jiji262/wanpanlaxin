#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""夸克转存助手 (QuarkMover) - 本地 Studio Web 服务

功能：
  - 单条 / 批量提取夸克分享链接（支持无协议头、pan.qoark.cn、带提取码链接）
  - 自动转存到指定网盘目录并生成全新分享链接（支持公开免密或随机提取码、自定义有效期）
  - OpenAI 兼容接口 AI 推文二创改写 + 在线连通性测试
  - 扫码登录夸克（Headless Chrome 实时二维码 + 过期真刷新）与手动 Cookie 粘贴双通道
  - 本地历史记录持久化、多线程并发 HTTP 服务、端口自动让位
"""
import argparse
import json
import mimetypes
import random
import re
import string as _string
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from login_worker import QuarkLoginWorker
from utils import (
    app_root,
    browser_check,
    make_ai_client,
    make_quark_client,
    pick_free_port,
    setup_logger,
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# ============ Paths & Config ============
ROOT = app_root()
CONFIG_PATH = ROOT / "config" / "config.json"
COOKIES_PATH = ROOT / "config" / "cookies.txt"
HISTORY_PATH = ROOT / "config" / "history.json"
STATIC_DIR = ROOT / "static"
LOG_DIR = ROOT / "logs"

setup_logger(LOG_DIR)

DEFAULT_PROMPT = (
    "你是一位资深推文写手。请对下面的推文进行二次创作：保持原帖的风格、语气和核心信息，"
    "改写措辞和句式，使其读起来像一条全新的原创推文。不要包含任何网盘链接、URL 或提取码。"
    "直接输出改写后的推文正文，不要加解释、标题或引号。\n\n原文：\n{text}"
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "ai_api_key": "",
    "ai_base_url": "https://api.deepseek.com",
    "ai_model": "deepseek-chat",
    "port": 7788,
    "target_dir": "/QuarkMover",
    "share_use_passcode": False,
    "share_expired_type": 1,
    "rewrite_prompt": DEFAULT_PROMPT,
}

FAVICON_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" fill="none">
<rect width="32" height="32" rx="8" fill="#2563EB"/>
<path d="M10.5 19.5a4.5 4.5 0 0 1-.4-8.98 6 6 0 0 1 11.66-1.32A4 4 0 0 1 22 17.1" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M16 14v9m0 0-3.2-3.2M16 23l3.2-3.2" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def _normalize_target_dir(val: str) -> str:
    cleaned = (val or "").strip().replace("\\", "/")
    cleaned = re.sub(r"/+", "/", cleaned).rstrip("/")
    if not cleaned or cleaned == "/":
        return "/QuarkMover"
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned


def _random_passcode(length: int = 4) -> str:
    chars = _string.ascii_letters + _string.digits
    return "".join(random.choices(chars, k=length))


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"读取 config.json 失败，使用默认配置: {e}")
        data = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    merged["target_dir"] = _normalize_target_dir(str(merged.get("target_dir", "/QuarkMover")))
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CONFIG = load_config()
CONFIG_LOCK = threading.Lock()


def get_cfg(key: str, default: Any = None) -> Any:
    with CONFIG_LOCK:
        return CONFIG.get(key, default)


def update_cfg(patch: Dict[str, Any]) -> None:
    with CONFIG_LOCK:
        if "target_dir" in patch:
            patch["target_dir"] = _normalize_target_dir(str(patch["target_dir"]))
        if "share_expired_type" in patch:
            try:
                v = int(patch["share_expired_type"])
                patch["share_expired_type"] = v if v in (1, 2, 3, 4) else 1
            except Exception:
                patch["share_expired_type"] = 1
        if "share_use_passcode" in patch:
            patch["share_use_passcode"] = bool(patch["share_use_passcode"])
        CONFIG.update(patch)
        save_config(CONFIG)


# ============ History Manager ============
HISTORY_LOCK = threading.Lock()
MAX_HISTORY = 50


def load_history() -> List[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def append_history(entry: Dict[str, Any]) -> None:
    with HISTORY_LOCK:
        items = load_history()
        items.insert(0, entry)
        items = items[:MAX_HISTORY]
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)


def delete_history_item(item_id: str) -> None:
    with HISTORY_LOCK:
        items = [it for it in load_history() if str(it.get("id")) != str(item_id)]
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)


def clear_history() -> None:
    with HISTORY_LOCK:
        if HISTORY_PATH.exists():
            HISTORY_PATH.unlink(missing_ok=True)


# ============ Quark API (sync httpx, direct domestic) ============
QUARK_REQUIRED = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
QUARK_HEADERS_BASE = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
    "origin": "https://pan.quark.cn",
    "referer": "https://pan.quark.cn/",
    "content-type": "application/json;charset=UTF-8",
    "accept": "application/json, text/plain, */*",
}


def load_cookie() -> str:
    if not COOKIES_PATH.exists():
        return ""
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    return lines[0] if lines else ""


def quark_headers(cookie_override: Optional[str] = None) -> Dict[str, str]:
    h = dict(QUARK_HEADERS_BASE)
    ck = cookie_override if cookie_override is not None else load_cookie()
    if ck:
        h["cookie"] = ck
    return h


def quark_params(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p: Dict[str, Any] = dict(QUARK_REQUIRED)
    if extra:
        p.update(extra)
    p["__dt"] = random.randint(100, 9999)
    p["__t"] = int(time.time() * 1000)
    return p


class QuarkError(Exception):
    pass


class QuarkAuthError(QuarkError):
    pass


def _check_resp(r: httpx.Response, op: str) -> Dict[str, Any]:
    if r.status_code in (401, 403):
        _invalidate_login_cache()
        raise QuarkAuthError(f"{op}失败：未登录或登录态已过期")
    try:
        data = r.json()
    except Exception as e:
        raise QuarkError(f"{op}返回非 JSON 响应 (HTTP {r.status_code})") from e

    status = data.get("status")
    if status != 200:
        code = data.get("code")
        msg = str(data.get("message") or data)
        low = msg.lower()
        if (
            status in (401, 403)
            or code in (31001, 41001, 50051)
            or "login" in low
            or "st invalid" in low
            or "未登录" in msg
        ):
            _invalidate_login_cache()
            raise QuarkAuthError(f"{op}失败：登录态已过期，请重新扫码登录")
        raise QuarkError(f"{op}失败：{msg}")
    return data.get("data") or {}


def _format_bytes(size: int) -> str:
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(size)
    for u in units:
        if val < 1024 or u == "TB":
            return f"{val:.1f} {u}" if u != "B" else f"{int(val)} B"
        val /= 1024
    return f"{val:.1f} TB"


def quark_probe_session(client: httpx.Client, cookie_override: Optional[str] = None) -> Tuple[bool, Dict[str, str]]:
    """检测夸克登录状态，并尽量拉取昵称与网盘容量信息。"""
    headers = quark_headers(cookie_override)
    if not headers.get("cookie"):
        return False, {}
    try:
        r = client.get(
            "https://drive-pc.quark.cn/1/clouddrive/config",
            headers=headers,
            params=quark_params(),
            timeout=8,
        )
        if r.status_code != 200 or r.json().get("status") != 200:
            return False, {}
    except Exception:
        return False, {}

    account: Dict[str, str] = {}
    try:
        r_acc = client.get(
            "https://pan.quark.cn/account/info",
            headers=headers,
            params={"fr": "pc", "platform": "pc"},
            timeout=5,
        )
        if r_acc.status_code == 200:
            acc_data = r_acc.json().get("data") or {}
            nick = acc_data.get("nickname") or acc_data.get("mobile") or ""
            if nick:
                account["username"] = str(nick)
    except Exception:
        pass

    try:
        r_mem = client.get(
            "https://drive-pc.quark.cn/1/clouddrive/member",
            headers=headers,
            params=quark_params({"fetch_subscribe": "true", "_ch": "home"}),
            timeout=5,
        )
        if r_mem.status_code == 200 and r_mem.json().get("status") == 200:
            mdata = r_mem.json().get("data") or {}
            used = int(mdata.get("use_capacity") or 0)
            total = int(mdata.get("total_capacity") or 0)
            if total > 0:
                account["quota"] = f"{_format_bytes(used)} / {_format_bytes(total)}"
    except Exception:
        pass

    return True, account


def q_get_stoken(client: httpx.Client, pwd_id: str, passcode: str = "") -> str:
    r = client.post(
        "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
        headers=quark_headers(),
        params=quark_params(),
        json={"pwd_id": pwd_id, "passcode": passcode or ""},
    )
    data = _check_resp(r, "验证分享链接")
    stoken = data.get("stoken")
    if not stoken:
        raise QuarkError("未能获取到分享 stoken（链接可能已失效或需要提取码）")
    return stoken


def q_list_share_files(client: httpx.Client, pwd_id: str, stoken: str) -> List[Dict[str, Any]]:
    r = client.get(
        "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/detail",
        headers=quark_headers(),
        params=quark_params({
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "force": "0",
            "_page": 1,
            "_size": 100,
            "_fetch_banner": 0,
            "_fetch_share": 0,
            "_fetch_total": 1,
            "_sort": "file_type:asc,updated_at:desc",
        }),
    )
    data = _check_resp(r, "读取分享文件列表")
    return data.get("list") or []


def q_save_files(
    client: httpx.Client,
    pwd_id: str,
    stoken: str,
    fid_list: List[str],
    fid_token_list: List[str],
    to_pdir_fid: str,
) -> str:
    r = client.post(
        "https://drive.quark.cn/1/clouddrive/share/sharepage/save",
        headers=quark_headers(),
        params=quark_params(),
        json={
            "fid_list": fid_list,
            "fid_token_list": fid_token_list,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        },
    )
    data = _check_resp(r, "提交转存任务")
    task_id = data.get("task_id")
    if not task_id:
        raise QuarkError("转存接口未返回 task_id")
    return task_id


def q_wait_task(client: httpx.Client, task_id: str, progress_cb=None) -> Dict[str, Any]:
    for i in range(60):
        r = client.get(
            "https://drive-pc.quark.cn/1/clouddrive/task",
            headers=quark_headers(),
            params=quark_params({"task_id": task_id, "retry_index": i}),
        )
        data = _check_resp(r, "轮询转存进度")
        if data.get("status") == 2:
            return data
        if progress_cb:
            progress_cb(i)
        time.sleep(0.8)
    raise QuarkError("转存任务等待超时")


def q_get_path_fid(client: httpx.Client, file_path: str) -> Optional[str]:
    r = client.post(
        "https://drive-pc.quark.cn/1/clouddrive/file/info/path_list",
        headers=quark_headers(),
        params=quark_params(),
        json={"file_path": [file_path], "namespace": "0"},
    )
    data = _check_resp(r, "查询保存目录")
    if not isinstance(data, list) or not data:
        return None
    fid = data[0].get("fid")
    return str(fid) if fid is not None else None


def q_create_dir(client: httpx.Client, dir_path: str) -> str:
    r = client.post(
        "https://drive-pc.quark.cn/1/clouddrive/file",
        headers=quark_headers(),
        params=quark_params(),
        json={
            "pdir_fid": "0",
            "file_name": "",
            "dir_path": dir_path,
            "dir_init_lock": False,
        },
    )
    data = _check_resp(r, "创建保存目录")
    if not isinstance(data, dict) or data.get("fid") is None:
        raise QuarkError(f"创建目录 {dir_path} 返回异常")
    return str(data["fid"])


def q_ensure_target_dir(client: httpx.Client, target_dir: str) -> str:
    fid = q_get_path_fid(client, target_dir)
    if fid:
        return fid
    return q_create_dir(client, target_dir)


def q_find_recent_files(client: httpx.Client, names: List[str], pdir_fid: str) -> List[str]:
    r = client.get(
        "https://drive-pc.quark.cn/1/clouddrive/file/sort",
        headers=quark_headers(),
        params=quark_params({
            "pdir_fid": pdir_fid,
            "_page": 1,
            "_size": 100,
            "_fetch_total": 0,
            "_fetch_sub_dirs": 0,
            "_sort": "file_type:asc,updated_at:desc",
        }),
    )
    data = _check_resp(r, "定位新文件")
    items = data.get("list") or []
    name_set = set(names)
    fids: List[str] = []
    for it in items:
        fname = it.get("file_name", "")
        if fname in name_set:
            fids.append(str(it["fid"]))
            name_set.discard(fname)
        if not name_set:
            break
    # 若因重名被夸克自动加了后缀 (如 xxx(1))，尝试按前缀兜底匹配最新创建项
    if not fids and names and items:
        stem = names[0].rsplit(".", 1)[0]
        for it in items:
            if str(it.get("file_name", "")).startswith(stem):
                fids.append(str(it["fid"]))
                break
    return fids


def q_create_share(client: httpx.Client, fid_list: List[str], title: str) -> Tuple[str, str]:
    """返回 (share_id, passcode)。passcode 为空字符串表示公开免密。"""
    use_passcode = bool(get_cfg("share_use_passcode", False))
    expired_type = int(get_cfg("share_expired_type", 1))
    passcode = _random_passcode() if use_passcode else ""
    url_type = 2 if passcode else 1

    body: Dict[str, Any] = {
        "fid_list": fid_list,
        "title": title,
        "url_type": url_type,
        "expired_type": expired_type,
    }
    if passcode:
        body["passcode"] = passcode

    r = client.post(
        "https://drive-pc.quark.cn/1/clouddrive/share",
        headers=quark_headers(),
        params=quark_params(),
        json=body,
    )
    data = _check_resp(r, "创建分享任务")
    task_id = data.get("task_id")
    if not task_id:
        raise QuarkError("创建分享未返回 task_id")

    for i in range(60):
        r2 = client.get(
            "https://drive-pc.quark.cn/1/clouddrive/task",
            headers=quark_headers(),
            params=quark_params({"task_id": task_id, "retry_index": i}),
        )
        d2 = _check_resp(r2, "轮询分享任务")
        if d2.get("status") == 2:
            share_id = d2.get("share_id")
            if not share_id:
                raise QuarkError("分享完成但未返回 share_id")
            return str(share_id), passcode
        time.sleep(0.6)
    raise QuarkError("创建分享等待超时")


def q_get_share_url(client: httpx.Client, share_id: str) -> str:
    r = client.post(
        "https://drive-pc.quark.cn/1/clouddrive/share/password",
        headers=quark_headers(),
        params=quark_params(),
        json={"share_id": share_id},
    )
    data = _check_resp(r, "读取分享链接")
    share_url = data.get("share_url")
    if not share_url:
        raise QuarkError("生成的分享链接为空")
    return str(share_url)


# ============ AI Rewrite & Connectivity Test ============
def ai_rewrite(text: str) -> str:
    if not text.strip():
        return ""
    key = str(get_cfg("ai_api_key", "")).strip()
    if not key or key.startswith("sk-REPLACE"):
        raise RuntimeError("未配置 AI API Key")
    base_url = str(get_cfg("ai_base_url", "https://api.deepseek.com")).strip().rstrip("/")
    model = str(get_cfg("ai_model", "deepseek-chat")).strip() or "deepseek-chat"
    prompt_tpl = str(get_cfg("rewrite_prompt", DEFAULT_PROMPT)).strip() or DEFAULT_PROMPT
    if "{text}" in prompt_tpl:
        prompt = prompt_tpl.replace("{text}", text)
    else:
        prompt = f"{prompt_tpl}\n\n{text}"

    with make_ai_client(base_url, timeout=60.0) as client:
        r = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
            },
        )
        if r.status_code >= 400:
            detail = ""
            try:
                err_obj = r.json()
                detail = err_obj.get("error", {}).get("message") or str(err_obj)
            except Exception:
                detail = r.text[:200]
            raise RuntimeError(f"HTTP {r.status_code}: {detail}")
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()


def ai_test_connection(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ov = override or {}
    key = str(ov.get("ai_api_key") if "ai_api_key" in ov else get_cfg("ai_api_key", "")).strip()
    base_url = str(
        ov.get("ai_base_url") if "ai_base_url" in ov else get_cfg("ai_base_url", "https://api.deepseek.com")
    ).strip().rstrip("/")
    model = str(ov.get("ai_model") if "ai_model" in ov else get_cfg("ai_model", "deepseek-chat")).strip()

    if not key:
        return {"ok": False, "error": "请先填写 AI API 密钥"}
    if not base_url:
        return {"ok": False, "error": "请填写 AI Base URL"}
    if not model:
        return {"ok": False, "error": "请填写模型名称"}

    t0 = time.time()
    try:
        with make_ai_client(base_url, timeout=20.0) as client:
            r = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with OK"}],
                    "max_tokens": 8,
                    "temperature": 0,
                },
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code >= 400:
                detail = ""
                try:
                    detail = r.json().get("error", {}).get("message") or r.text[:160]
                except Exception:
                    detail = r.text[:160]
                return {"ok": False, "error": f"HTTP {r.status_code}: {detail}"}
            reply = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
            return {"ok": True, "latency_ms": ms, "model": model, "reply": reply[:40]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ============ Share Link Extraction & Pipeline ============
SHARE_URL_RE = re.compile(
    r"(?:https?://)?pan\.qu?o?ark\.cn/s/([A-Za-z0-9]+)(?:\?[^\s#\"'<>]*)?(?:#[^\s\"'<>]*)?",
    re.IGNORECASE,
)
PWD_QUERY_RE = re.compile(r"[?&]pwd=([A-Za-z0-9]{4,8})", re.IGNORECASE)
PWD_TEXT_RE = re.compile(r"(?:提取码|口令|密码)\s*[:：]?\s*([A-Za-z0-9]{4})\b")
QUARK_BOILERPLATE_RE = re.compile(
    r"(?:我用夸克网盘给你分享了[^\n]*|点击链接或复制整段内容[^\n]*|/~[^~\n]+~:/|"
    r"(?:链接|提取码|口令)\s*[:：]\s*[A-Za-z0-9:/?._=#-]*)",
    re.IGNORECASE,
)


def extract_shares(text: str) -> Tuple[List[Dict[str, str]], str]:
    """提取文本中所有不重复的夸克分享链接及对应提取码，并返回清理后的纯文案。"""
    shares: List[Dict[str, str]] = []
    seen: set[str] = set()

    for m in SHARE_URL_RE.finditer(text):
        full_match = m.group(0)
        pwd_id = m.group(1)
        if pwd_id in seen:
            continue
        seen.add(pwd_id)

        passcode = ""
        qm = PWD_QUERY_RE.search(full_match)
        if qm:
            passcode = qm.group(1)
        else:
            # 在链接后方 60 个字符内寻找提取码
            tail = text[m.end() : m.end() + 60]
            tm = PWD_TEXT_RE.search(tail)
            if tm:
                passcode = tm.group(1)

        shares.append({
            "pwd_id": pwd_id,
            "passcode": passcode,
            "raw_url": full_match if full_match.startswith("http") else f"https://{full_match}",
        })

    cleaned = SHARE_URL_RE.sub("", text)
    cleaned = QUARK_BOILERPLATE_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return shares, cleaned


def _format_quark_share_block(title: str, url: str, passcode: str = "") -> str:
    title_for_tpl = title or "精选资源"
    link_with_pwd = f"{url}?pwd={passcode}" if passcode else url
    lines = [
        f"我用夸克网盘给你分享了「{title_for_tpl}」，点击链接或复制整段内容，打开「夸克APP」即可获取。",
        f"链接：{link_with_pwd}",
    ]
    if passcode:
        lines.insert(1, f"提取码：{passcode}")
    return "\n".join(lines)


def run_pipeline(job: "Job", raw_text: str, mode: str = "reshare_only") -> None:
    """mode: 'full' = AI 二创 + 夸克转存；'reshare_only' = 仅转存换链"""
    started = time.time()
    try:
        shares, text_no_link = extract_shares(raw_text)
        logger.info(
            f"[job {job.id}] start mode={mode} shares={len(shares)} text_len={len(text_no_link)}"
        )

        rewritten = ""
        rewrite_err = ""
        quark_err = ""
        converted_shares: List[Dict[str, Any]] = []
        target_dir = _normalize_target_dir(str(get_cfg("target_dir", "/QuarkMover")))

        # --- Step 1: AI Rewrite (full mode only) ---
        if mode == "full" and text_no_link.strip():
            if _has_key():
                job.update(step="调用 AI 改写原创推文文案")
                try:
                    rewritten = ai_rewrite(text_no_link)
                except Exception as e:
                    rewrite_err = str(e)
                    logger.warning(f"[job {job.id}] ai rewrite failed: {e}")
            else:
                rewrite_err = "未配置 AI API Key，已跳过文案二创"
                logger.info(f"[job {job.id}] no ai key, skip rewrite")

        # --- Step 2: Quark Reshare (supports 1..N share links) ---
        if shares:
            with make_quark_client(timeout=30.0) as client:
                try:
                    job.update(step=f"准备网盘保存目录 {target_dir}")
                    target_dir_fid = q_ensure_target_dir(client, target_dir)
                except QuarkAuthError as e:
                    quark_err = str(e)
                    target_dir_fid = ""
                except Exception as e:
                    quark_err = str(e)
                    logger.exception(f"[job {job.id}] ensure target dir failed")
                    target_dir_fid = ""

                if target_dir_fid:
                    total = len(shares)
                    for idx, item in enumerate(shares, start=1):
                        prefix = f"[{idx}/{total}] " if total > 1 else ""
                        pwd_id = item["pwd_id"]
                        in_pwd = item["passcode"]
                        try:
                            job.update(step=f"{prefix}解析分享链接 ({pwd_id[:8]}…)", sub=None)
                            stoken = q_get_stoken(client, pwd_id, in_pwd)

                            job.update(step=f"{prefix}读取分享文件列表")
                            files = q_list_share_files(client, pwd_id, stoken)
                            if not files:
                                raise QuarkError("该分享链接中没有可转存的文件")

                            fid_list = [str(f["fid"]) for f in files]
                            fid_token_list = [str(f["share_fid_token"]) for f in files]
                            names = [str(f.get("file_name") or "") for f in files]
                            title = names[0] or "未命名资源"

                            job.update(step=f"{prefix}转存 {len(files)} 个文件至 {target_dir}")
                            task_id = q_save_files(
                                client, pwd_id, stoken, fid_list, fid_token_list, target_dir_fid
                            )

                            job.update(step=f"{prefix}等待云端转存完成")
                            task_data = q_wait_task(
                                client,
                                task_id,
                                progress_cb=lambda i, p=prefix: job.update(
                                    sub=f"云端同步中 ({i + 1}/60)"
                                ),
                            )

                            # 优先读取夸克任务直接返回的 save_as_top_fids，彻底规避同名文件冲突
                            save_as = task_data.get("save_as") or {}
                            top_fids = [str(x) for x in (save_as.get("save_as_top_fids") or []) if x]
                            if not top_fids:
                                time.sleep(0.4)
                                job.update(step=f"{prefix}定位已保存的新文件", sub=None)
                                top_fids = q_find_recent_files(client, names, target_dir_fid)
                            if not top_fids:
                                raise QuarkError("转存完成但未能定位到新文件")

                            job.update(step=f"{prefix}生成专属新分享链接", sub=None)
                            sid, out_pwd = q_create_share(client, top_fids, title)
                            share_url = q_get_share_url(client, sid)
                            final_url = f"{share_url}?pwd={out_pwd}" if out_pwd else share_url
                            qblock = _format_quark_share_block(title, share_url, out_pwd)

                            converted_shares.append({
                                "pwd_id": pwd_id,
                                "title": title,
                                "files": names,
                                "file_count": len(names),
                                "new_link": final_url,
                                "raw_share_url": share_url,
                                "passcode": out_pwd,
                                "quark_block": qblock,
                                "error": "",
                            })
                            logger.info(f"[job {job.id}] share ok -> {final_url}")
                        except QuarkAuthError as e:
                            quark_err = str(e)
                            converted_shares.append({
                                "pwd_id": pwd_id,
                                "title": pwd_id,
                                "files": [],
                                "file_count": 0,
                                "new_link": "",
                                "raw_share_url": "",
                                "passcode": "",
                                "quark_block": "",
                                "error": quark_err,
                            })
                            break
                        except Exception as e:
                            err_msg = str(e)
                            logger.warning(f"[job {job.id}] share {pwd_id} failed: {err_msg}")
                            converted_shares.append({
                                "pwd_id": pwd_id,
                                "title": pwd_id,
                                "files": [],
                                "file_count": 0,
                                "new_link": "",
                                "raw_share_url": "",
                                "passcode": "",
                                "quark_block": "",
                                "error": err_msg,
                            })
                            if not quark_err:
                                quark_err = err_msg
        elif mode == "reshare_only":
            quark_err = "未检测到夸克分享链接（支持 pan.quark.cn/s/xxx 格式）"

        ok_shares = [s for s in converted_shares if s.get("new_link")]
        first_ok = ok_shares[0] if ok_shares else {}

        parts: List[str] = []
        if rewritten:
            parts.append(rewritten)
        for s in ok_shares:
            if parts:
                parts.append("")
            parts.append(s["quark_block"])
        output = "\n".join(parts).strip()

        elapsed_s = round(time.time() - started, 1)
        result_payload: Dict[str, Any] = {
            "rewritten": rewritten,
            "rewrite_error": rewrite_err,
            "new_link": first_ok.get("new_link", ""),
            "all_links": [s["new_link"] for s in ok_shares],
            "share_passcode": first_ok.get("passcode", ""),
            "share_title": first_ok.get("title", ""),
            "quark_block": "\n\n".join(s["quark_block"] for s in ok_shares),
            "output": output,
            "had_link": bool(shares),
            "quark_error": quark_err if not ok_shares else (quark_err or ""),
            "files": first_ok.get("files", []),
            "shares": converted_shares,
            "mode": mode,
            "elapsed_seconds": elapsed_s,
        }

        if ok_shares or rewritten:
            append_history({
                "id": f"{int(time.time() * 1000)}-{job.id}",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "mode": mode,
                "title": first_ok.get("title") or (rewritten[:28] + "…" if len(rewritten) > 28 else rewritten) or "文案改写",
                "file_count": sum(int(s.get("file_count", 0)) for s in ok_shares),
                "new_link": first_ok.get("new_link", ""),
                "all_links": [s["new_link"] for s in ok_shares],
                "passcode": first_ok.get("passcode", ""),
                "output": output,
                "elapsed_seconds": elapsed_s,
            })

        job.finish(result_payload)
    except Exception as e:
        logger.exception(f"[job {job.id}] pipeline failed")
        job.fail(f"{type(e).__name__}: {e}")


# ============ Job Manager ============
class Job:
    def __init__(self, jid: str):
        self.id = jid
        self.status = "running"  # running | done | failed
        self.step = "正在初始化任务"
        self.sub: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.created_at = time.time()
        self._lock = threading.Lock()

    def update(self, step: Optional[str] = None, sub: Optional[str] = "__keep__") -> None:
        with self._lock:
            if step is not None:
                self.step = step
                logger.info(f"[job {self.id}] {step}")
            if sub != "__keep__":
                self.sub = sub

    def finish(self, result: Dict[str, Any]) -> None:
        with self._lock:
            self.status = "done"
            self.result = result
            self.step = "处理完成"
            self.sub = None

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = "failed"
            self.error = error
            self.step = "处理失败"
            self.sub = None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "step": self.step,
                "sub": self.sub,
                "result": self.result,
                "error": self.error,
            }


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def create(self) -> Job:
        with self._lock:
            self._counter += 1
            jid = str(self._counter)
            job = Job(jid)
            self._jobs[jid] = job
            if len(self._jobs) > 50:
                old = sorted(self._jobs.items(), key=lambda kv: kv[1].created_at)[:10]
                for k, _ in old:
                    self._jobs.pop(k, None)
            return job

    def get(self, jid: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(jid)


# ============ Session Cache & Worker ============
_LOGIN_CACHE: Dict[str, Any] = {"ts": 0.0, "ok": False, "account": {}}
_LOGIN_CACHE_LOCK = threading.Lock()


def _invalidate_login_cache() -> None:
    with _LOGIN_CACHE_LOCK:
        _LOGIN_CACHE["ts"] = 0.0
        _LOGIN_CACHE["ok"] = False
        _LOGIN_CACHE["account"] = {}


def _get_session_cached(force: bool = False) -> Tuple[bool, Dict[str, str]]:
    now = time.time()
    with _LOGIN_CACHE_LOCK:
        if not force and (now - _LOGIN_CACHE["ts"] < 30):
            return bool(_LOGIN_CACHE["ok"]), dict(_LOGIN_CACHE["account"])

    if not load_cookie():
        with _LOGIN_CACHE_LOCK:
            _LOGIN_CACHE["ts"] = now
            _LOGIN_CACHE["ok"] = False
            _LOGIN_CACHE["account"] = {}
        return False, {}

    with make_quark_client(timeout=8.0) as client:
        ok, account = quark_probe_session(client)

    with _LOGIN_CACHE_LOCK:
        _LOGIN_CACHE["ts"] = now
        _LOGIN_CACHE["ok"] = ok
        _LOGIN_CACHE["account"] = account
    return ok, account


def _has_key() -> bool:
    k = str(get_cfg("ai_api_key", "")).strip()
    return bool(k) and not k.startswith("sk-REPLACE")


JOB_MANAGER = JobManager()
LOGIN_WORKER = QuarkLoginWorker(COOKIES_PATH, on_login_success=_invalidate_login_cache)
SERVER_PORT = 7788


# ============ Threaded HTTP Server ============
class ThreadedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    _EXTRA_MIME = {
        ".webp": "image/webp",
        ".avif": "image/avif",
        ".woff2": "font/woff2",
        ".svg": "image/svg+xml",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP " + (format % args))

    @staticmethod
    def _is_client_disconnect(exc: BaseException) -> bool:
        return isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError))

    def _send(self, code: int, body: bytes, ctype: str, head_only: bool = False) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
        except OSError as e:
            if self._is_client_disconnect(e):
                logger.debug(f"client disconnected before response was sent: {e}")
                return
            raise

    def _send_json(self, obj: Any, code: int = 200, head_only: bool = False) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, raw, "application/json; charset=utf-8", head_only=head_only)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_static(self, rel_path: str, head_only: bool = False) -> None:
        safe = rel_path.replace("\\", "/").lstrip("/")
        if ".." in safe.split("/"):
            return self._send(403, b"forbidden", "text/plain", head_only=head_only)
        fpath = (STATIC_DIR / safe).resolve()
        if not str(fpath).startswith(str(STATIC_DIR.resolve())) or not fpath.is_file():
            return self._send(404, b"not found", "text/plain", head_only=head_only)
        ext = fpath.suffix.lower()
        ctype = self._EXTRA_MIME.get(ext) or mimetypes.guess_type(str(fpath))[0] or "application/octet-stream"
        with open(fpath, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, head_only=head_only)

    def do_HEAD(self) -> None:
        self._handle_get(head_only=True)

    def do_GET(self) -> None:
        self._handle_get(head_only=False)

    def _handle_get(self, head_only: bool = False) -> None:
        try:
            raw_path, _, query = self.path.partition("?")
            if raw_path in ("/", "/index.html"):
                return self._serve_static("index.html", head_only=head_only)
            if raw_path == "/favicon.ico":
                return self._send(200, FAVICON_SVG, "image/svg+xml", head_only=head_only)
            if raw_path == "/api/state":
                force = "force=1" in query
                logged_in, account = _get_session_cached(force=force)
                return self._send_json(
                    {
                        "logged_in": logged_in,
                        "account": account,
                        "has_key": _has_key(),
                        "ai_model": get_cfg("ai_model", "deepseek-chat"),
                        "ai_base_url": get_cfg("ai_base_url", "https://api.deepseek.com"),
                        "target_dir": get_cfg("target_dir", "/QuarkMover"),
                        "share_use_passcode": bool(get_cfg("share_use_passcode", False)),
                        "share_expired_type": int(get_cfg("share_expired_type", 1)),
                        "browser": browser_check(),
                        "port": SERVER_PORT,
                    },
                    head_only=head_only,
                )
            if raw_path == "/api/login/state":
                snap = LOGIN_WORKER.snapshot()
                if snap.get("state") == "logged_in":
                    _invalidate_login_cache()
                return self._send_json(snap, head_only=head_only)
            if raw_path == "/api/config":
                return self._send_json(
                    {
                        "ai_api_key": get_cfg("ai_api_key", ""),
                        "ai_base_url": get_cfg("ai_base_url", "https://api.deepseek.com"),
                        "ai_model": get_cfg("ai_model", "deepseek-chat"),
                        "target_dir": get_cfg("target_dir", "/QuarkMover"),
                        "rewrite_prompt": get_cfg("rewrite_prompt", DEFAULT_PROMPT),
                        "default_prompt": DEFAULT_PROMPT,
                        "share_use_passcode": bool(get_cfg("share_use_passcode", False)),
                        "share_expired_type": int(get_cfg("share_expired_type", 1)),
                    },
                    head_only=head_only,
                )
            if raw_path == "/api/history":
                return self._send_json({"items": load_history()}, head_only=head_only)
            if raw_path.startswith("/api/job/"):
                jid = raw_path.rsplit("/", 1)[-1]
                job = JOB_MANAGER.get(jid)
                if not job:
                    return self._send_json({"error": "job not found"}, 404, head_only=head_only)
                return self._send_json(job.snapshot(), head_only=head_only)
            if raw_path.startswith("/static/"):
                rel = raw_path[len("/static/") :]
                return self._serve_static(rel, head_only=head_only)
            return self._send(404, b"not found", "text/plain", head_only=head_only)
        except Exception as e:
            if self._is_client_disconnect(e):
                return
            logger.exception("GET handler error")
            try:
                self._send_json({"error": f"{type(e).__name__}: {e}"}, 500, head_only=head_only)
            except OSError as send_err:
                if self._is_client_disconnect(send_err):
                    return
                raise

    def do_POST(self) -> None:
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/config":
                body = self._read_json()
                allowed = {k: v for k, v in body.items() if k in DEFAULT_CONFIG}
                update_cfg(allowed)
                logger.info("config updated")
                return self._send_json({"ok": True, "target_dir": get_cfg("target_dir", "/QuarkMover")})
            if path == "/api/ai/test":
                body = self._read_json()
                return self._send_json(ai_test_connection(body))
            if path == "/api/login/start":
                LOGIN_WORKER.start()
                return self._send_json({"ok": True})
            if path == "/api/login/refresh":
                LOGIN_WORKER.request_refresh()
                return self._send_json({"ok": True})
            if path == "/api/login/stop":
                LOGIN_WORKER.stop()
                return self._send_json({"ok": True})
            if path == "/api/login/cookie":
                body = self._read_json()
                raw_cookie = str(body.get("cookie") or "").strip()
                if raw_cookie.lower().startswith("cookie:"):
                    raw_cookie = raw_cookie.split(":", 1)[1].strip()
                raw_cookie = " ".join(raw_cookie.splitlines()).strip()
                if not raw_cookie:
                    return self._send_json({"ok": False, "error": "请粘贴有效的 Cookie 字符串"})
                with make_quark_client(timeout=8.0) as client:
                    ok, account = quark_probe_session(client, cookie_override=raw_cookie)
                if not ok:
                    return self._send_json({"ok": False, "error": "Cookie 无效或已过期，请从 pan.quark.cn 重新复制"})
                COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(COOKIES_PATH, "w", encoding="utf-8") as f:
                    f.write(raw_cookie + "\n")
                _invalidate_login_cache()
                return self._send_json({"ok": True, "account": account})
            if path == "/api/login/logout":
                LOGIN_WORKER.stop()
                if COOKIES_PATH.exists():
                    COOKIES_PATH.unlink(missing_ok=True)
                _invalidate_login_cache()
                return self._send_json({"ok": True})
            if path == "/api/history/delete":
                body = self._read_json()
                delete_history_item(str(body.get("id", "")))
                return self._send_json({"ok": True})
            if path == "/api/history/clear":
                clear_history()
                return self._send_json({"ok": True})
            if path == "/api/generate":
                body = self._read_json()
                text = str(body.get("text") or "")
                mode = str(body.get("mode") or "reshare_only")
                if mode not in ("full", "reshare_only"):
                    mode = "reshare_only"
                if not text.strip():
                    return self._send_json({"ok": False, "error": "请先输入或粘贴分享链接/推文内容"})
                job = JOB_MANAGER.create()
                t = threading.Thread(target=run_pipeline, args=(job, text, mode), daemon=True)
                t.start()
                return self._send_json({"ok": True, "job_id": job.id})
            return self._send(404, b"not found", "text/plain")
        except Exception as e:
            if self._is_client_disconnect(e):
                return
            logger.exception("POST handler error")
            try:
                self._send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            except OSError as send_err:
                if self._is_client_disconnect(send_err):
                    return
                raise


def main() -> None:
    global SERVER_PORT
    parser = argparse.ArgumentParser(description="QuarkMover - 夸克转存助手")
    parser.add_argument("--port", type=int, default=0, help="指定监听端口")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = parser.parse_args()

    preferred = args.port or int(get_cfg("port", 7788))
    port = pick_free_port(preferred)
    SERVER_PORT = port
    if port != preferred:
        logger.warning(f"端口 {preferred} 已占用，自动切换至 {port}")
    url = f"http://127.0.0.1:{port}"
    logger.info(f"夸克转存助手 已启动: {url}")
    logger.info(f"日志目录: {LOG_DIR}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    server = ThreadedHTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("正在关闭服务…")
    finally:
        LOGIN_WORKER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
