"""图书馆学科领域清单。

⚠️ **这套分类没有 API。** 实测 `format=json` / `output=json` 都是假的
（照样返回 text/html），只能解析 HTML。这是本设计里唯一脆弱的依赖，
所以**拿不到就当没有**，绝不影响检索本身（spec §4.3）。

来源注意：是 `db_subject.php`。`db_subject.htm` 只有 63 字节，是一段
`window.location.href = "db_subject.php"` 的跳转桩，抓它等于什么都没抓到。
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from pathlib import Path

import requests

AREAS_URL = "https://app3.lib.cityu.edu.hk/ereslist/db_subject.php"

CACHE_TTL = 7 * 24 * 3600          # 7 天。领域分类极少变，抓太勤没意义还招人烦。
CACHE_FILE = Path.home() / ".canvbridge" / "library_areas.json"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanvBridge/1.0)"}

# <a href="//app3.../index.php?type=db&format=subj&subj=comp">Computer Science</a>
_AREA_LINK = re.compile(
    r'<a[^>]+href="[^"]*[?&]subj=([A-Za-z0-9_]+)"[^>]*>(.*?)</a>',
    re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def parse_areas(html: str) -> list[dict]:
    """解析出 `[{"code","name"}]`，保持页面顺序，同 code 去重留首条。

    解析不出任何一条 → 返回 `[]`（调用方当「拿不到」处理，**不抛错**）。
    页面改版、被拦、返回无关 HTML 都走这条路。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for code, inner in _AREA_LINK.findall(html or ""):
        code = (code or "").strip().lower()
        name = _WS.sub(" ", _html.unescape(_TAGS.sub("", inner))).strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
    return out


def _read_cache() -> list[dict]:
    """读缓存；任何问题（不存在 / 坏 JSON / 形状不对 / 过期）一律当没有。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        areas = raw.get("areas")
        fetched_at = float(raw.get("fetched_at") or 0)
    except Exception:
        return []
    if not isinstance(areas, list) or not areas:
        return []
    if not all(isinstance(a, dict) and a.get("code") and a.get("name") for a in areas):
        return []
    if time.time() - fetched_at > CACHE_TTL:
        return []          # 过期。调用方会重抓；重抓失败时再单独回退到它
    return areas


def _read_stale_cache() -> list[dict]:
    """不管过期与否都读出来 —— 只在重抓失败时当救兵。"""
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        areas = raw.get("areas")
    except Exception:
        return []
    if not isinstance(areas, list) or not areas:
        return []
    if not all(isinstance(a, dict) and a.get("code") and a.get("name") for a in areas):
        return []
    return areas


def _write_cache(areas: list[dict]) -> None:
    """写缓存失败不是错误（只读文件系统 / 没权限），下次再抓就是。"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps({"fetched_at": time.time(), "areas": areas}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def load_areas(*, force: bool = False, session=None) -> dict:
    """返回 `{"available": bool, "areas": [...]}`。

    `available=False` **不是错误** —— 前端据此**整个不渲染 area 选择器**
    （spec §4.3，用户裁定：拿不到就不显示）。绝不让它拖垮检索。
    """
    if not force:
        cached = _read_cache()
        if cached:
            return {"available": True, "areas": cached}

    own = session is None
    s = session or requests.Session()
    try:
        resp = s.get(AREAS_URL, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        areas = parse_areas(resp.text)
    except Exception:
        areas = []
    finally:
        if own:
            s.close()

    if not areas:
        stale = _read_stale_cache()      # 抓失败/改版了 → 旧缓存也比没有强
        if stale:
            return {"available": True, "areas": stale}
        return {"available": False, "areas": []}

    _write_cache(areas)
    return {"available": True, "areas": areas}
