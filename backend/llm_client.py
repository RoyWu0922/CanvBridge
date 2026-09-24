"""OpenAI 兼容 chat completions 客户端，用于公告总结与日程提取。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

_SYSTEM = (
    "You extract structured schedule information from course announcements. "
    "Respond only with the requested JSON object, never with markdown."
)

_SYSTEM_TEXT = (
    "You are a helpful academic assistant. Respond with concise plain text, "
    "never markdown, never JSON."
)

def _schema_instructions(language: str) -> str:
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        '  "course_name": "<course_name>",\n'
        f'  "summaries": ["<{lang} summary of the 1st announcement>", '
        '"<{lang} summary of the 2nd announcement>", ...],\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summaries: write in {lang}; an ARRAY with EXACTLY ONE entry per announcement, '
        'in the same order the announcements are listed above (entry 1 ↔ announcement [1], '
        'entry 2 ↔ announcement [2], and so on). Each entry summarizes ONLY its own '
        'announcement in a few sentences.\n'
        '- calendar_events: ONLY items with a concrete date/time (a class, review session, '
        'exam, office hours). If only a date is given, use 23:59:00 as the end time. '
        'Location in English if mentioned, else "".\n'
        '- reminders: ONLY deadlines/due dates without a start/end period. due_date is the '
        'deadline; default to 23:59:00 if only a date is given.\n'
        '- Titles and notes in English, verbatim from the announcements where possible.\n'
        '- Return [] for calendar_events or reminders if there are none. Never invent events.\n'
        'Return ONLY the JSON object.'
    )


def _build_prompt(course_name: str, announcements: list[dict], language: str = "zh") -> str:
    lines = [
        f'You are an academic assistant. Below are announcements from the Canvas course named "{course_name}".',
        "",
    ]
    for i, a in enumerate(announcements, 1):
        lines.append(f"[{i}] {a.get('posted_at', '')} — {a.get('title', '')}")
        lines.append(a.get("message", ""))
        lines.append("")
    lines.append(_schema_instructions(language))
    return "\n".join(lines)


# DeepSeek 第一方 API。官方 2026-07-24 停用旧模型名 deepseek-chat / deepseek-reasoner，
# 需改叫 deepseek-v4-flash；V4 思考模式默认开启会让 temperature 失效、json_object 不稳
# （可空内容），结构化提取宜显式关闭。第三方网关（OpenRouter 等）目录名不在其列，原样保留。
_DEEPSEEK_HOST = "api.deepseek.com"
_LEGACY_DS_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def _is_deepseek(base_url: str) -> bool:
    return _DEEPSEEK_HOST in (base_url or "").lower()


def _effective_model(base_url: str, model: str) -> str:
    model = (model or "").strip()
    if _is_deepseek(base_url) and model in _LEGACY_DS_MODELS:
        return "deepseek-v4-flash"
    return model


def _call_chat(base_url: str, api_key: str, model: str, prompt: str,
               json_mode: bool = True) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    model = _effective_model(base_url, model)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM if json_mode else _SYSTEM_TEXT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if _is_deepseek(base_url):
        # 显式关掉思考，保持旧 deepseek-chat 的非思考语义，采样参数与 json_object 才可靠。
        payload["thinking"] = {"type": "disabled"}
    resp = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {api_key}"}, timeout=120,
    )
    if not resp.ok:
        # 把上游真实原因带进报错：400 多为模型别名被退役 / 参数不被支持，正文有说明
        body = (resp.text or "")[:400]
        raise requests.HTTPError(f"{resp.status_code} {resp.reason} from {url} — {body}")
    resp.raise_for_status()
    data = resp.json()
    # 形状守卫：模型完全可能回一段**合法 JSON 但顶层是 list**（或 message 不是对象）。
    # 原写法 `data.get("choices")` 在这种情况下抛 AttributeError，而 AttributeError
    # **不在**归一化层那个 `except (requests.RequestException, ValueError, KeyError,
    # TypeError, RuntimeError)` 元组里 —— 它会穿出去，被端点的宽 except 兜住，
    # 于是用户看到的是 `'list' object has no attribute 'get'` 这种解释器内部字符串。
    # 抛 **TypeError** 而不是 ValueError：这里拿到的是合法 JSON，说「无法解析为 JSON」
    # 不诚实；TypeError 落进归一化层「AI 返回内容不可用」那一支，对「形状不对」正好准确，
    # 且不需要新增异常类或分支。
    if not isinstance(data, dict):
        raise TypeError("AI 响应顶层不是 JSON 对象")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TypeError("AI 响应缺少 choices")
    msg_obj = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(msg_obj, dict):
        raise TypeError("AI 响应 message 形状不对")
    return msg_obj.get("content") or ""


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _per_summaries(announcements: list[dict]) -> list[str]:
    """逐条回退摘要：每条公告一行「标题: 原文摘录」，无公告给占位。"""
    out = [f"- {a.get('title', '')}: {a.get('message', '')[:300]}" for a in announcements]
    return out or ["(无公告)"]


def extract_course_summary(base_url: str, api_key: str, model: str,
                           course_name: str, announcements: list[dict],
                           language: str = "zh") -> dict:
    """返回 {course_name, summaries, calendar_events, reminders}。

    summaries 与 announcements 逐条一一对应（顺序相同）；calendar_events/reminders
    仍按整门课聚合。输出无法解析时（重试一次后）回退到公告原文摘录并附 warning。
    """
    fallback = {
        "course_name": course_name,
        "summaries": _per_summaries(announcements),
        "calendar_events": [],
        "reminders": [],
        "warning": "总结失败，已展示公告原文",
    }
    prompt = _build_prompt(course_name, announcements, language)
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt)
            parsed = _parse_json(content)
            if not isinstance(parsed, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            parsed.setdefault("course_name", course_name)
            parsed.setdefault("calendar_events", [])
            parsed.setdefault("reminders", [])
            # summaries 需与公告对齐：模型漏条/非字符串时用原文摘录补齐
            base = _per_summaries(announcements)
            got = parsed.get("summaries")
            if isinstance(got, list) and announcements:
                parsed["summaries"] = [
                    got[i].strip() if (i < len(got) and isinstance(got[i], str) and got[i].strip())
                    else base[i]
                    for i in range(len(announcements))
                ]
            else:
                parsed["summaries"] = base
            return parsed
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError):
            continue
    return fallback


_MAX_SYLLABUS = 20000


def _syllabus_schema(language: str) -> str:
    """syllabus 提取的结构化指令：要点总结 + 可写日历的日程/截止事项。"""
    lang = "Chinese" if language == "zh" else "English"
    return (
        'Produce a JSON object with EXACTLY this structure:\n'
        '{\n'
        f'  "summary": "<concise {lang} bullet-point summary>",\n'
        '  "calendar_events": [{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS", '
        '"end": "YYYY-MM-DDTHH:MM:00", "location": "...", "notes": "..."}],\n'
        '  "reminders": [{"title": "...", "due_date": "YYYY-MM-DDTHH:MM:SS", "notes": "..."}]\n'
        '}\n'
        'Rules:\n'
        f'- summary: in {lang}, as concise bullet points. Cover course objectives, grading '
        'scheme, key deadlines and assessments, and anything a student must know.\n'
        '- calendar_events: ONLY items with a concrete date/time or a single dated event '
        '(exam dates, special sessions, reading-week holidays). If only a date is given, '
        'use 09:00:00 as start and 23:59:00 as end. Location if mentioned, else "".\n'
        '- reminders: ONLY deadlines and due dates without a start/end period (assignment '
        'due dates, registration or add/drop deadlines). due_date is the deadline; default '
        'to 23:59:00 if only a date is given.\n'
        '- Titles and notes verbatim from the syllabus where possible.\n'
        '- Return [] for calendar_events or reminders if there are none. Never invent dates '
        'that are not in the syllabus.\n'
        'Return ONLY the JSON object.'
    )


def summarize_syllabus(base_url: str, api_key: str, model: str,
                       course_name: str, syllabus_text: str,
                       language: str = "zh") -> dict:
    """返回结构化结果 {summary, calendar_events, reminders}。

    summary 为要点总结（目标语言）；calendar_events/reminders 是从 syllabus
    提取的可写日历事项。syllabus 过长截断防 token 超限；输出需能解析为 JSON，
    失败重试一次，仍失败抛异常（由端点转 ok:false，不静默降级成原文）。
    """
    if len(syllabus_text) > _MAX_SYLLABUS:
        syllabus_text = syllabus_text[:_MAX_SYLLABUS] + "\n…(已截断)"
    prompt = (
        f'You are an academic assistant. Below is the syllabus for "{course_name}". '
        f"Extract a summary and any dated schedule items exactly as instructed.\n\n"
        f"Syllabus:\n{syllabus_text}\n\n"
        + _syllabus_schema(language)
    )
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt, json_mode=True)
            parsed = _parse_json(content)
            if not isinstance(parsed, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            return {
                "summary": parsed.get("summary", ""),
                "calendar_events": parsed.get("calendar_events") or [],
                "reminders": parsed.get("reminders") or [],
            }
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Syllabus 总结失败: {last_err}") from last_err


# ---------------------------------------------------------------------------
# 邮件 AI 总结（课程 × 类型 两级）
# ---------------------------------------------------------------------------

_MAX_BODY = 4000          # 单封正文截断
_MAX_MAIL_TOTAL = 20000   # 全部正文的总预算（对齐 _MAX_SYLLABUS 的先例）
_MIN_BODY = 400           # 每封的下限：宁可超预算，也不把邮件压成 0 字


def trim_bodies(mails: list[dict], budget: int = _MAX_MAIL_TOTAL) -> list[dict]:
    """单封截断 + 总量预算。**条数永远不变**（对齐靠 index，丢条会整体错位）。

    **下限优先于总额**：`len(mails) > 50` 时 `budget // n < _MIN_BODY`，`max()` 取到下限，
    于是实际总额是 `n × _MIN_BODY` —— **随 n 线性增长，没有上界**（取信路径
    `list_mail` / `select_scope` 都不限条数，只由最近 `DEFAULT_DAYS` 天内的全部邮件数
    兜着；旧实现是 `未读 ∪ 最近 N 天`，删掉「未读」这个析取支只可能让保留集**变小**
    ⇒ 这个界只会**更紧**，不会更松 —— 窗口判据的键同期从 `received` 换成了
    `_internaldate`（见 `select_scope` 自述，两者不等），故此处**不**声称新旧集合的
    包含关系）：
    n=500 时约 10×（400 × 500 = 20 万），n=1000 时约 20×。**「10×」是 n=500 那一点，
    不是上界。** 这是有意的：宁可超预算，也不把邮件压成 0 字。
    """
    n = max(1, len(mails))
    cap = min(_MAX_BODY, max(_MIN_BODY, budget // n))
    return [{**m, "body": (m.get("body") or "")[:cap]} for m in mails]


def _excerpt(m: dict) -> str:
    """模型没给可用摘要时的回退 —— 用原文摘录，不编造。"""
    text = (m.get("body") or "").strip() or (m.get("subject") or "").strip()
    return text[:200]


_MAIL_SCHEMA_ZH = """只输出如下 JSON，不要任何解释、不要 markdown 代码块：
{"groups":[{"course":"课程代码或「学校行政」","types":[
  {"type":"作业|考试|通知|行政|其他","items":[
    {"mail_index":<输入里的邮件编号，整数>,
     "summary":"一句话中文摘要",
     "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}],
     "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}]}]}]}]}
规则：
- 邮件里**没有**明确截止时间时，calendar_events 与 reminders 都留空数组，绝不臆造日期。
- 一封邮件只能出现一次。所有输入邮件都必须被覆盖到。
- 归不进具体课程的（校方通知、图书馆、IT 等）course 填「学校行政」。"""

_MAIL_SCHEMA_EN = """Output only the following JSON, no prose, no markdown fences:
{"groups":[{"course":"course code or \\"Administration\\"","types":[
  {"type":"assignment|exam|notice|admin|other","items":[
    {"mail_index":<the mail number from the input, integer>,
     "summary":"one-sentence summary",
     "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}],
     "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}]}]}]}]}
Rules:
- If a mail states **no** explicit deadline, leave calendar_events and reminders empty. Never invent dates.
- Each mail appears exactly once. Every input mail must be covered.
- Non-course mail goes under course "Administration"."""


def _build_mail_prompt(mails: list[dict], language: str = "zh") -> str:
    if language == "en":
        head = ("You are triaging a CityU student's school mailbox. Group the mails "
                "below by course then type, with a summary and any deadlines.\n")
        schema = _MAIL_SCHEMA_EN
    else:
        head = ("你在帮一名香港城市大学学生整理学校邮箱。下面是本次要处理的邮件，"
                "请按「课程 → 类型」两级归类，并给出摘要与截止事项。\n")
        schema = _MAIL_SCHEMA_ZH
    parts = [head, schema, "\n---\n"]
    for m in mails:
        parts.append(
            f"### 邮件 #{m['index']}\n"
            f"主题：{m.get('subject', '')}\n"
            f"发件人：{m.get('sender', '')}\n"
            f"时间：{m.get('received', '')}\n"
            f"正文：\n{m.get('body', '')}\n\n")
    return "".join(parts)


def _normalize_groups(data: dict, mails: list[dict], language: str) -> dict:
    """把模型输出收敛成可信结构：越界 index 丢弃、坏字段回退。

    留痕的确切保证：越界 / 非整数 mail_index 的条目被丢弃时留痕；未被任何条目引用的
    邮件留痕。其余畸形形状（如某组 items 全是非 dict）只丢弃、不留痕 —— 不承诺「全程留痕」。
    """
    other = "Other" if language == "en" else "其他"
    warnings: list[str] = []
    dropped = 0
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return {"groups": [], "warnings": [
            "AI 返回的结构不可用，未生成分组" if language == "zh"
            else "AI returned an unusable structure; no groups produced"]}
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        types_out = []
        for ty in (g.get("types") or []):
            if not isinstance(ty, dict):
                continue
            items_out = []
            for it in (ty.get("items") or []):
                if not isinstance(it, dict):
                    continue
                idx = it.get("mail_index")
                # 越界 / 非整数一律丢：绝不能把 AI 编的内容挂到真实邮件上
                if isinstance(idx, bool) or not isinstance(idx, int) \
                        or not (0 <= idx < len(mails)):
                    dropped += 1
                    continue
                summary = it.get("summary")
                items_out.append({
                    "mail_index": idx,
                    "summary": summary if isinstance(summary, str) and summary.strip()
                               else _excerpt(mails[idx]),
                    "calendar_events": it.get("calendar_events")
                                       if isinstance(it.get("calendar_events"), list) else [],
                    "reminders": it.get("reminders")
                                 if isinstance(it.get("reminders"), list) else [],
                })
            if items_out:
                types_out.append({"type": str(ty.get("type") or other), "items": items_out})
        if types_out:
            out.append({"course": str(g.get("course") or other), "types": types_out})
    # 覆盖检查：prompt 要求每封邮件都被归类，没被任何条目引用的邮件必须留痕 ——
    # 否则「AI 返回垃圾」与「邮箱是空的」在 UI 上无法区分（docstring 的「绝不静默降级」）。
    placed = {it["mail_index"] for g in out for ty in g["types"] for it in ty["items"]}
    missing = len(mails) - len(placed)
    if missing > 0:
        warnings.append(
            f"AI 未覆盖 {missing} 封邮件，结果可能不完整" if language == "zh"
            else f"AI did not cover {missing} mail(s); the result may be incomplete")
    if dropped:
        warnings.append(
            f"AI 返回的 {dropped} 条结果指向了不存在的邮件，已丢弃" if language == "zh"
            else f"Dropped {dropped} AI item(s) pointing at nonexistent mail")
    return {"groups": out, "warnings": warnings}


def summarize_mail(base_url: str, api_key: str, model: str,
                   mails: list[dict], language: str = "zh") -> dict:
    """对邮件做「课程 × 类型」两级归类 + 摘要 + DDL/待办抽取。

    `mails` 每项：{index, subject, sender, received, body}。
    返回 {"groups": [...], "warnings": [...]}；整体失败时 groups 为空、warnings 有话说，
    **绝不静默降级**。
    """
    if not mails:
        return {"groups": [], "warnings": []}
    trimmed = trim_bodies(mails)
    prompt = _build_mail_prompt(trimmed, language)
    data = None           # 首次就抛异常时，循环后的 isinstance 守卫要有值可查
    last_exc = None
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt, json_mode=True)
            data = _parse_json(content)
            if not isinstance(data, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            break
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError) as exc:
            last_exc = exc
            # 防御性重置：值正确性上冗余（下一轮赋值会覆盖），与上面那行合起来才防住 UnboundLocalError
            data = None
            continue
    if not isinstance(data, dict):
        # 按异常类型给原因，而不是一律说「解析失败」：401/5xx/网络错误由 _call_chat 抛出，
        # 报成「无法解析」会把用户指向错误的方向（去查提示词而不是查 API key）。
        # JSONDecodeError 判在前面：requests 的 JSONDecodeError 同时继承 RequestException 与 json.JSONDecodeError，
        # 落进这一支才诚实 —— 「响应体本身不是 JSON」而不是「鉴权/网络故障」。
        if isinstance(last_exc, json.JSONDecodeError):
            msg = ("AI 总结失败：返回内容无法解析为 JSON" if language == "zh"
                   else "AI summary failed: response was not parsable JSON")
        elif isinstance(last_exc, requests.RequestException):
            msg = ("AI 总结失败：请求模型服务出错（网络或鉴权）" if language == "zh"
                   else "AI summary failed: model request error (network or auth)")
        else:
            msg = ("AI 总结失败：AI 返回内容不可用" if language == "zh"
                   else "AI summary failed: model returned unusable content")
        return {"groups": [], "warnings": [msg]}
    # 归一化刻意放在重试循环之外：归一自身的缺陷不该被吞成「无法解析为 JSON」
    return _normalize_groups(data, trimmed, language)


# ---------------------------------------------------------------------------
# 抽取路径：只抽「待办 + 带日期的日程」，不做任何分组
# ---------------------------------------------------------------------------

_TODOS_SCHEMA_ZH = """只输出如下 JSON，不要任何解释、不要 markdown 代码块：
{"items":[{"mail_index":<输入里的邮件编号，整数>,
  "summary":"一句话中文摘要",
  "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}],
  "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}]}]}
规则：
- 邮件里**没有**明确截止时间时，calendar_events 与 reminders 都留空数组，绝不臆造日期。
- 只列**要做的事**。纯告知类邮件（讲座宣传、系统维护）没有待办就留空，可以不出现在 items 里。
- 每封输入邮件最多出现一次。"""

_TODOS_SCHEMA_EN = """Output only the following JSON, no prose, no markdown fences:
{"items":[{"mail_index":<the mail number from the input, integer>,
  "summary":"one-sentence summary",
  "reminders":[{"title":"","due_date":"YYYY-MM-DDTHH:MM:SS","notes":""}],
  "calendar_events":[{"title":"","start":"YYYY-MM-DDTHH:MM:SS","end":"YYYY-MM-DDTHH:MM:SS","location":"","notes":""}]}]}
Rules:
- If a mail states **no** explicit deadline, leave calendar_events and reminders empty. Never invent dates.
- List only **action items**. Purely informational mail may be omitted entirely.
- Each input mail appears at most once."""


def _build_extract_prompt(mails: list[dict], language: str = "zh") -> str:
    if language == "en":
        head = "Extract action items and dated events from the mails below.\n"
        schema = _TODOS_SCHEMA_EN
    else:
        head = "从下面的邮件里抽出待办事项与带日期的日程。\n"
        schema = _TODOS_SCHEMA_ZH
    parts = [head, schema, "\n---\n"]
    for m in mails:
        parts.append(
            f"### 邮件 #{m['index']}\n"
            f"主题：{m.get('subject', '')}\n"
            f"发件人：{m.get('sender', '')}\n"
            f"时间：{m.get('received', '')}\n"
            f"正文：\n{m.get('body', '')}\n\n")
    return "".join(parts)


def _keep_dicts(v) -> list:
    """只留对象元素；非列表一律回空数组。

    形状坏掉时给**空**，绝不把字符串塞进渲染层 —— 前端对它做 `.map()`，
    一个字符串会让整页白屏而不是少几行。
    """
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _normalize_extract(data: dict, mails: list[dict], language: str) -> dict:
    """收敛抽取结果。越界 / 非整数 mail_index 一律丢 —— 绝不能把 AI 编的内容
    挂到真实邮件上（与 `_normalize_groups` 同一条纪律）。

    **留痕的确切保证**：越界 / 非整数 `mail_index`
    的条目计入 `dropped` 并报 `ai_dropped`；**其余畸形形状只丢弃、不留痕** ——
    `items` 里的非对象项、以及 `_keep_dicts` 过滤掉的非对象 `reminders` /
    `calendar_events` 项，都不计数也不报警。理由是**文案比这窄**：前端把 `ai_dropped`
    渲染成「AI 有 N 条结果指向了不存在的邮件」，把形状坏掉的项数进去那句话就成了假话。

    警告出**码**不出句子：前端按 `mailtodo.warn.<code>` 取双语，出句子会让英文界面冒中文。
    """
    items_in = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items_in, list):
        return {"items": [], "warnings": ["ai_unusable"], "dropped": 0}
    out: list[dict] = []
    dropped = 0
    for it in items_in:
        if not isinstance(it, dict):
            continue
        idx = it.get("mail_index")
        # `isinstance(True, int)` 为真 ⇒ 布尔必须**显式**挡在前面，否则被当成下标 1
        if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < len(mails)):
            dropped += 1
            continue
        summary = it.get("summary")
        out.append({
            "mail_index": idx,
            "summary": summary if isinstance(summary, str) and summary.strip()
                       else _excerpt(mails[idx]),
            "reminders": _keep_dicts(it.get("reminders")),
            "calendar_events": _keep_dicts(it.get("calendar_events")),
        })
    warnings = ["ai_dropped"] if dropped else []
    return {"items": out, "warnings": warnings, "dropped": dropped}


def extract_todos(base_url: str, api_key: str, model: str,
                  mails: list[dict], language: str = "zh") -> dict:
    """从邮件正文里抽出待办与带日期的日程。**这是本设计里 AI 唯一的活。**

    `mails` 每项：{index, subject, sender, received, body}。
    返回 {"items": [...], "warnings": [码], "dropped": int}；**整体失败**（取不到 /
    不是 JSON 对象 / `items` 不是数组）时 items 为空且 warnings 有话说。
    **「绝不静默降级」只担保到这一层** —— 形状坏掉的**单个元素**静默跳过
    （不计入 `dropped`、不报警），确切边界见 `_normalize_extract` 的 docstring。
    """
    if not mails:
        return {"items": [], "warnings": [], "dropped": 0}
    trimmed = trim_bodies(mails)
    prompt = _build_extract_prompt(trimmed, language)
    data = None           # 首次就抛异常时，循环后的 isinstance 守卫要有值可查
    for _attempt in range(2):
        try:
            content = _call_chat(base_url, api_key, model, prompt, json_mode=True)
            data = _parse_json(content)
            if not isinstance(data, dict):
                raise ValueError("LLM 返回非 JSON 对象")
            break
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError):
            # 与 `summarize_mail` 同一写法：值正确性上冗余（下一轮赋值会覆盖），
            # 与后面那行 isinstance 守卫合起来才防住 UnboundLocalError
            data = None
            continue
    if not isinstance(data, dict):
        return {"items": [], "warnings": ["ai_unusable"], "dropped": 0}
    return _normalize_extract(data, trimmed, language)
