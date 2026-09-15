"""领域清单测试。用合成 HTML —— 真实页面结构见 spec §4。"""
import pytest

from backend import library_areas


@pytest.fixture(autouse=True)
def _never_touch_real_home(tmp_path, monkeypatch):
    """安全网：默认把 CACHE_FILE 指向 tmp_path。

    模块常量指向 `~/.canvbridge/library_areas.json`，那是**用户真实文件**，
    测试绝不允许写进去。下面每个涉及缓存的用例都显式 monkeypatch 了，
    这个 autouse fixture 是防止日后新增用例漏写时把用户的缓存覆盖掉。
    """
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "guard" / "areas.json")
    monkeypatch.setattr(library_areas, "AREAS_URL", "https://areas.invalid/test")


# 真实页面（db_subject.php）的形态：<a href="//app3.../index.php?type=db&format=subj&subj=comp">Computer Science</a>
_HTML = """
<ul>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=comp">Computer Science</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=law">Law</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=busgen">Business</a></li>
  <li><a href="//app3.lib.cityu.edu.hk/ereslist/index.php?type=db&format=subj&subj=human">Arts &amp; Humanities</a></li>
</ul>
"""


def test_parse_areas_extracts_code_and_name():
    areas = library_areas.parse_areas(_HTML)
    assert {"code": "comp", "name": "Computer Science"} in areas
    assert {"code": "law", "name": "Law"} in areas
    assert len(areas) == 4


def test_parse_areas_unescapes_html_entities():
    """页面里写的是 'Arts &amp; Humanities'，显示给用户必须是 'Arts & Humanities'。"""
    names = {a["code"]: a["name"] for a in library_areas.parse_areas(_HTML)}
    assert names["human"] == "Arts & Humanities"


def test_parse_areas_dedupes_keeping_first_order():
    """同一 code 出现多次（页面有重复链接）只留一条，且保持页面顺序。"""
    html = _HTML + _HTML
    areas = library_areas.parse_areas(html)
    assert [a["code"] for a in areas] == ["comp", "law", "busgen", "human"]


def test_parse_areas_returns_empty_on_redesign():
    """改版 / 被拦 / 拿回一坨无关 HTML → 空列表，**不抛错**。
    调用方据此返回 available=False，前端整个不渲染选择器。"""
    assert library_areas.parse_areas("<html><body>nothing here</body></html>") == []
    assert library_areas.parse_areas("") == []


def test_parse_areas_skips_links_with_empty_text():
    html = '<a href="/ereslist/index.php?type=db&format=subj&subj=x"></a>'
    assert library_areas.parse_areas(html) == []


# ---------- load_areas：缓存与降级 ----------

class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, text=None, boom=False):
        self.text, self.boom, self.calls = text, boom, 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("network down")
        return _Resp(self.text)

    def close(self):
        pass


def test_load_areas_fetches_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    s = _Session(text=_HTML)
    r = library_areas.load_areas(session=s)
    assert r["available"] is True and len(r["areas"]) == 4
    assert s.calls == 1

    # 第二次走缓存，不再打网络
    s2 = _Session(text=_HTML)
    r2 = library_areas.load_areas(session=s2)
    assert r2["available"] is True and s2.calls == 0


def test_load_areas_force_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    library_areas.load_areas(session=_Session(text=_HTML))
    s = _Session(text=_HTML)
    library_areas.load_areas(force=True, session=s)
    assert s.calls == 1


def test_load_areas_returns_unavailable_on_network_failure(tmp_path, monkeypatch):
    """网络挂了 → available=False，**不抛错**。搜索本身不受影响。"""
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    r = library_areas.load_areas(session=_Session(boom=True))
    assert r == {"available": False, "areas": []}


def test_load_areas_falls_back_to_stale_cache_on_failure(tmp_path, monkeypatch):
    """有旧缓存 + 网络挂了 → 用旧的（标 available=True）。总比让选择器消失好。"""
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    library_areas.load_areas(session=_Session(text=_HTML))

    import json, time
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["fetched_at"] = time.time() - library_areas.CACHE_TTL - 10   # 弄成过期
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    s = _Session(boom=True)
    r = library_areas.load_areas(session=s)
    # 过期 ⇒ 必须真的重抓过（否则测的是「缓存没过期」，回退链根本没走到）
    assert s.calls == 1
    assert r["available"] is True and len(r["areas"]) == 4


def test_load_areas_unavailable_when_parse_yields_nothing(tmp_path, monkeypatch):
    """抓到了但解析不出东西（改版）→ 当拿不到，别给个空选择器。"""
    monkeypatch.setattr(library_areas, "CACHE_FILE", tmp_path / "areas.json")
    r = library_areas.load_areas(session=_Session(text="<html>nothing</html>"))
    assert r == {"available": False, "areas": []}


def test_load_areas_survives_corrupt_cache(tmp_path, monkeypatch):
    """缓存文件被写坏不能炸 —— 当没有，重新抓。"""
    cache = tmp_path / "areas.json"
    cache.write_text("{{{ not json", encoding="utf-8")
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    r = library_areas.load_areas(session=_Session(text=_HTML))
    assert r["available"] is True and len(r["areas"]) == 4
