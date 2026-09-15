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


# ---------- R13-a：缓存条目**形状**校验（合法 JSON + 非空 list 还不够） ----------
#
# 触发条件：缓存文件被人手改、或跨版本 schema 漂移（截断只会产生坏 JSON，
# 已被上面的 except 兜住）。`{"areas": [1,2,3]}` 是**合法 JSON、也是非空 list**，
# 旧实现会原样返回 ⇒ `load_areas()` 回 `{"available": True, "areas": [1,2,3]}`，
# **违反对外契约 `areas: [{"code","name"}]`**，前端据此渲染出畸形选择器。

def _write_cache(cache, areas, *, age=0):
    import json, time
    cache.write_text(json.dumps({"fetched_at": time.time() - age, "areas": areas}),
                     encoding="utf-8")


def test_load_areas_rejects_malformed_cache_entries(tmp_path, monkeypatch):
    """未过期的畸形缓存（非空 list，但条目不是 `{"code","name"}`）**不得**当成命中。"""
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    _write_cache(cache, [1, 2, 3])

    s = _Session(text=_HTML)
    r = library_areas.load_areas(session=s)
    assert s.calls == 1                                    # 畸形缓存没有被当成命中
    assert r["available"] is True
    assert r["areas"] == library_areas.parse_areas(_HTML)  # 拿到的是真抓的那份
    assert all(isinstance(a, dict) and a.get("code") and a.get("name") for a in r["areas"])


def test_load_areas_rejects_malformed_stale_cache_entries(tmp_path, monkeypatch):
    """走 **stale 分支**那一份：畸形缓存 + 重抓失败 ⇒ 当没有（available=False）。

    不能只用过期缓存来测：过期本身就会让 `_read_cache` 返回 []，那样评的是过期
    而不是形状。这里用**未过期**的畸形缓存，逼 `_read_cache` 因形状退回 []、
    于是真的去重抓；重抓失败后才轮到 `_read_stale_cache` —— 把它的形状校验单独隔出来。
    """
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    _write_cache(cache, [1, 2, 3])                         # 未过期

    s = _Session(boom=True)
    r = library_areas.load_areas(session=s)
    assert s.calls == 1                                    # 真的重抓过（没被畸形缓存骗过去）
    assert r == {"available": False, "areas": []}          # 坏条目**没有**借 stale 通道流出去


# ---------- R13-b：4 个防御分支的绑定 ----------
#
# 四处变异原先都存活（实现正确，只是改坏了没有用例会红）。四条分支在本文件里
# **全部**钉住（比要求的两条多，因为逐条写下来成本极低且各自判别性明确）：
#   ① `code.strip().lower()` 的 `lower`   → test_parse_areas_normalizes_code_...
#   ② `isinstance(areas, list)` 形状守卫  → test_load_areas_survives_non_list_cache_shape
#   ③ `html or ""` 守卫                   → test_parse_areas_tolerates_none_input
#   ④ `name` 的 `.strip()`                → test_parse_areas_normalizes_code_...

def test_parse_areas_normalizes_code_case_and_name_whitespace():
    """`subj=LAW` 里的 code 归一成小写；链接文本两侧的空白被 strip 掉。

    两处都是**归一化**而不是装饰：
      - code 是选择器 value 与 `parsed.areas` 的比对键（LLM 侧一律小写），
        大小写不一致会让「用户选的 comp」与「页面给的 COMP」永远匹配不上；
      - name 直接显示给用户，两侧留白会变成选择器里肉眼可见的空隙。
    `_WS.sub(" ", ...)` 只把连续空白**压缩成一个空格**，两端的那个空格要靠
    `.strip()` 去掉 —— 所以这条确实钉的是 strip，不是 sub。
    """
    html = ('<a href="/ereslist/index.php?type=db&format=subj&subj=LAW">'
            '  Law  </a>')
    assert library_areas.parse_areas(html) == [{"code": "law", "name": "Law"}]


def test_parse_areas_tolerates_none_input():
    """`html or ""` 守卫：上游拿到 None（而不是 ""）时必须返回空列表，不是 TypeError。

    没有这条守卫时 `findall(None)` 抛 `TypeError: expected string or bytes-like
    object`，会越过调用方那句「解析不出任何一条 → 返回 []」的约定。
    """
    assert library_areas.parse_areas(None) == []


def test_load_areas_survives_non_list_cache_shape(tmp_path, monkeypatch):
    """`isinstance(areas, list)` 形状守卫：`{"areas": 5}`（合法 JSON、真值）不得炸。

    没有这条守卫时 `all(... for a in 5)` 抛 `TypeError: 'int' object is not
    iterable` —— 而那行在 `try` 之外，异常会一路冒到端点（HTTP 500），
    违反「任何问题一律当没有」的文档字符串。这条同时隔着 `_read_stale_cache`
    那一份（重抓失败后仍不得炸）。
    """
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(library_areas, "CACHE_FILE", cache)
    _write_cache(cache, 5)                                 # 非 list 真值

    s = _Session(text=_HTML)
    r = library_areas.load_areas(session=s)
    assert s.calls == 1                                    # 当没有 ⇒ 重抓
    assert len(r["areas"]) == 4

    _write_cache(cache, 5)
    r2 = library_areas.load_areas(session=_Session(boom=True))   # 重抓失败 → stale 分支
    assert r2 == {"available": False, "areas": []}
