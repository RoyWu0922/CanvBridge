#!/usr/bin/env node
/**
 * 邮件待办页（`mailtodo`）前端自检：守住这一页**最要紧的那条契约** ——
 * 勾选键必须由「邮件指纹 + 条目下标」拼成，**不能用 IMAP UID**。
 *
 * 存在的意义：`check_mail_frontend.mjs` 守的是邮件页，它的 DOM 替身是按**邮件页的 id 集合**
 * 装配的、`fetch` 桩只放行那三条端点 —— 待办页要打 `/api/mail/classify` 与 `/api/mail/extract`，
 * 直接并进去会让那个桩把待办页的请求当错误抛出；它的变异开关还是一份**逐条实测过红集**的
 * 固定清单。所以新开一个，只守这一页。
 *
 * 零出网：`fetch` 是桩，**只**接住 `/api/mail/extract`（回固定夹具、并记下每次请求的 `ids`），
 * 其余 URL 一律 throw。**挡住出网**这一半成立：throw 发生在真实 `fetch` 之前。
 * 但「响亮地死掉」只在**直接**调 `fetch` 的路径上成立 —— 产品路径走 `frontend/util.js` 的 `api()`，
 * 它把 fetch 的异常吞成 `{ok:false, error:t("status.backend_fail")}`（实测：让桩抛，
 * `api("mail/extract", …)` 回 `{"ok":false,"error":"无法连接后端，请确认服务已启动"}`），
 * 于是「桩没挂上」表现为**另一种红**：实测把桩改成对**所有** URL 都抛，红的正是 S10 那两行
 * —— `S10 正对照：第 1 次抽取把**两封**都发了出去（实得 ids 条数 null）` 与
 * `S10 请求过、响应里没给回来的信，第 2 次不再被请求（fetch 次数 0 → 0）` ——
 * 而 `自检不该发起 fetch` 这句话在输出里 **0 次**露面。
 * 全部夹具都是合成的：发件地址用 RFC 2606 保留域
 * （`s0@example.invalid` / `s1@example.invalid`，永不解析），不含任何真实邮件内容或 token。
 *
 * 已知覆盖缺口（**不是假绿**，本检查器没有声称守事件线）：DOM 替身的 `addEventListener`
 * 是空函数（`getElementById` 造出来的节点上是 `addEventListener(){}`，见下），所以
 * `$("mtTodo").addEventListener("change", …)` 这条事件路径在本文件里**零覆盖** ——
 * 勾选状态的持久化只由 S4/S5 直接调 `mtSetDone` 走到，没有一条断言经过真实事件。
 * 同目录的 `check_mail_frontend.mjs` 用 `elListeners` 把处理器捕获下来（`elListeners[id][type]`），
 * 本文件没有这一步；要补是另一件事。
 *
 * 装配照抄 `check_mail_frontend.mjs` 的做法：最小 DOM 替身 + **逐文件** `runInContext`
 * （保持浏览器那四个 `<script>` 的边界，**不拼成一个脚本** —— 拼接会改变顶层 `let`/`const`
 * 的可见性，从而看不见真实的 TDZ 问题）+ 变异锚点必须**恰好命中一次**。
 *
 * 用法：node tools/check_mailtodo_frontend.mjs [变异开关…]
 *
 *   变异开关（不属于应用本身，只在改这份自检时手工跑；用来证明断言不是空转）：
 *     --mutate-key-to-uid          把**指纹本身**退化成 `mtFold(m.id)`（「用 UID 当键」的最小表达）
 *     --mutate-drop-index          `mtDoneKey(fp, i)` 去掉下标（`return fp`）
 *     --mutate-sched-empty-shared  时间表空态改回与清单共用一句（`return mtEmptyMsg()`）
 *     --mutate-warn-count-shared   `mtWarnCount` 一律喂 `mtState.missing`（修复前的写法）
 *   认不出的开关一律抛错退出 —— 「开关被静默忽略」与「跑了且全绿」在 rc 上逐字相同。
 *
 * 退出码：0 = 全部断言通过；1 = 有断言未通过 / 变异开关用错（锚点没命中、命中不止一次、
 *   替换后与原文一字不差）/ 变异后的源码加载不了 / 源码读不到。
 */
import vm from "node:vm";
import fs from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/* 源码相对**本脚本**解析（与 check_dom_ids / check_i18n_keys / check_mail_frontend 同款）：
   这样从任何 cwd 跑都读得到 frontend/。 */
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

/* ---- 最小 DOM 替身 ----------------------------------------------------------
   `getElementById` 对未登记的 id **按需造**（与 check_mail_frontend 同款），
   唯一的例外是 `absent` 这个黑名单 —— 见下面那段「加载完之后把 #mtTodo 摘掉」。 */
const nodes = {};
const absent = new Set();
const mkNode = id => (nodes[id] ||= {
  id, innerHTML: "", textContent: "", hidden: true, value: "",
  classList: { add(){}, remove(){}, toggle(){} }, style: {}, dataset: {},
  addEventListener(){}, removeAttribute(){}, focus(){}, remove(){}, click(){},
  appendChild(){}, setAttribute(){}, getAttribute(){ return null; },
  querySelector(){ return mkNode("q"); },
});

/* `fetch` 桩的账本：只记每次 `/api/mail/extract` 的 `ids`（凭据一概不记）。 */
const extractStub = {
  calls: [],
  reset(){ this.calls = []; },
  reply(body){
    const ids = Array.isArray(body.ids) ? body.ids : [];
    /* 只回**第一封**的 item —— 「请求过、响应里没给回来的信」正是 S10 的判别对象。 */
    const items = ids.length
      ? [{ id: ids[0], summary: "SYNTH-SUMMARY", reminders: [], calendar_events: [] }]
      : [];
    return { ok: true, items, warnings: [], dropped: 0, missing: 0 };
  },
};

/* localStorage 替身：**有真实存储**（Map 支撑），不是恒回 null 的哑桩 ——
   `mtSetDone` 写进去、`mtDoneSet` 读回来的那条往返要能被走到（勾选状态的持久化出口），
   S8 也要能往 `sc_mailRules` 里塞一段坏 JSON。 */
const store = new Map();
const sandbox = {
  console, setTimeout, clearTimeout,
  document: {
    getElementById: id => (absent.has(id) ? null : mkNode(id)),
    querySelector: () => null,
    querySelectorAll: () => [],
    /* `esc()`（frontend/util.js）正是走这条路：
       `const d=document.createElement("div"); d.textContent = …; return d.innerHTML;`
       ⇒ `innerHTML` **必须照真浏览器那样转义**。用「innerHTML 直接回吐输入」的假替身
       会让「转义有没有发生」永远测不到，而这一层正是本项目防注入的那一层。
       下面有 `esc("<b>")` 的自证。 */
    createElement: () => {
      const o = { _t: "", appendChild(){}, style: {}, classList: { add(){} }, dataset: {} };
      Object.defineProperty(o, "textContent", { set(v) { o._t = String(v == null ? "" : v); }, get() { return o._t; } });
      Object.defineProperty(o, "innerHTML", { get() { return o._t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); } });
      return o;
    },
    documentElement: mkNode("html"), title: "",
    addEventListener(){},
  },
  localStorage: {
    getItem: k => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: k => store.delete(k),
  },
  matchMedia: () => ({ matches: false, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){} }),
  fetch: (url, opts) => {
    const u = String(url);
    if (u !== "/api/mail/extract") throw new Error("自检不该发起 fetch：" + u);
    const body = JSON.parse((opts && opts.body) || "{}");
    extractStub.calls.push({ ids: (body.ids || []).slice() });
    const payload = extractStub.reply(body);
    return Promise.resolve({ ok: true, status: 200, json: async () => payload });
  },
  location: { href: "http://localhost/", origin: "http://localhost" },
  URL,
  open: () => null,
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
vm.createContext(sandbox);

/* ---- 变异开关 ---- */
const KNOWN = new Set(["--mutate-key-to-uid", "--mutate-drop-index",
                       "--mutate-sched-empty-shared", "--mutate-warn-count-shared"]);
for (const a of process.argv.slice(2)) {
  if (a.startsWith("-") && !KNOWN.has(a))
    throw new Error(`认不出的命令行开关：${JSON.stringify(a)}（已知：${[...KNOWN].join(" ")}）`);
}
const MUTATE_KEY_TO_UID    = process.argv.includes("--mutate-key-to-uid");
const MUTATE_DROP_INDEX    = process.argv.includes("--mutate-drop-index");
const MUTATE_SCHED_SHARED  = process.argv.includes("--mutate-sched-empty-shared");
const MUTATE_WARN_SHARED   = process.argv.includes("--mutate-warn-count-shared");

/* 与 check_mail_frontend 同款：锚点必须**恰好命中一次**；替换用**函数式** ——
   `String.replace(anchor, repl)` 会把 repl 里的 `$&`/`$1` 当替换模式展开，
   于是真正写进去的东西与打出的 witness 可以不是一回事（会撒谎的证明比没有证明更糟）。 */
const mutate = (src, anchor, repl, label) => {
  const hits = src.split(anchor).length - 1;
  if (hits !== 1) throw new Error(`[${label}] 变异锚点命中 ${hits} 次（应为 1）：${JSON.stringify(anchor)}`);
  const out = src.replace(anchor, () => repl);
  if (out === src) throw new Error(`[${label}] 变异未生效（替换后与原文一字不差）：${JSON.stringify(repl)}`);
  const witness = /[\u0000-\u001F\u007F]/.test(repl) ? JSON.stringify(repl) : repl;
  console.log(`[mutate] ${label} → ${witness}`);
  return out;
};

for (const f of ["i18n.js", "util.js", "app.js", "shell.js"]) {
  let src = fs.readFileSync(join(ROOT, "frontend", f), "utf8");
  if (f === "app.js") {
    if (MUTATE_KEY_TO_UID) {
      /* 「用 UID 当键」的**最小**表达：把唯一的指纹定义退化成 `m.id`。
         （改调用点 `const key = mtDoneKey(fp, i);` 是**打在冗余上**的变异 ——
           S3 直接拿指纹调 mtDoneKey、S4 比的是同一封信的两个下标，两条都照绿。） */
      src = mutate(src,
        "  return [mtFold(m.subject), mtFold(m.sender), mtFold(m.received)].join(MT_SEP1);",
        "  return mtFold(m.id);", "key-to-uid");
    }
    if (MUTATE_DROP_INDEX) {
      src = mutate(src,
        "function mtDoneKey(fp, i){ return fp + MT_SEP2 + String(i); }",
        "function mtDoneKey(fp, i){ return fp; }", "drop-index");
    }
    if (MUTATE_SCHED_SHARED) {
      /* 时间表空态改回与清单共用一句（修复前的行为）。 */
      src = mutate(src,
        `  if(mtHasExtractedContent()) return t("mailtodo.sched_none_dated");
  return mtHasItems() ? t("mailtodo.extract_none") : t("mailtodo.need_extract");
}`,
        "  return mtEmptyMsg();\n}", "sched-empty-shared");
    }
    if (MUTATE_WARN_SHARED) {
      /* 每个码一律喂 `missing`（修复前的行为）—— 「按码取数」与「一律喂 missing」只有在
         两个数**恰好相等**时才碰巧不错。 */
      src = mutate(src,
        `  if(code === "ai_dropped") return mtState.dropped;
  if(code === "ids_missing") return mtState.missing;
  return 0;
}`,
        "  return mtState.missing;\n}", "warn-count-shared");
    }
  }
  try {
    vm.runInContext(src, sandbox, { filename: f });
  } catch (err) {
    throw new Error(`加载 frontend/${f} 失败（跑过变异开关的话，多半是它把代码改坏了）：${err.message}`);
  }
}

/* 加载完成，四个文件都已求值（app.js 顶层那次 `applyLang()` 也跑过了，它经 i18n.js 的钩子
   调过一次 `renderMailTodo()`，那时 `#mtTodo` 还在）。
   现在把 `#mtTodo` 从替身里**摘掉**：`renderMailTodo()` 的第一句是
   `const box = $("mtTodo"); if(!box) return;`（frontend/app.js），
   ⇒ 整片渲染面（mtScheduleHtml / mtTodoHtml / 勾选键 / 时间表卡）在 S10 里整条短路。
   **这是为了让 S10 只测账本、不测渲染**，不是藏起失败 —— 摘除必须发生在加载**之后**：
   app.js 顶层有一句 `$("mtTodo").addEventListener("change", …)`，加载时就摘会让它抛 TypeError、
   整个 app.js 求值失败。 */
absent.add("mtTodo");

const run = code => vm.runInContext(code, sandbox);
/* 把沙箱里的值取回 Node 侧：`run()` 返回的是另一个 realm 的原语，走 JSON 往返最省心。 */
const val = (expr, fallback) => {
  try {
    const s = run(`JSON.stringify((() => (${expr}))())`);
    return s === undefined ? fallback : JSON.parse(s);
  } catch (e) { return fallback; }
};
/* 变异后的实现可能真的挂住（例如账本退回去记「只记回来的信」时，第二次抽取会真的再发一次请求）。
   挂住也是「红」，不该让整轮自检吊死。 */
const settle = async p => {
  let t;
  const guard = new Promise(r => { t = setTimeout(() => r("SYNTH-TIMEOUT"), 500); });
  const v = await Promise.race([p, guard]);
  clearTimeout(t);
  return v;
};

let fails = 0, total = 0;
const ok = (cond, label) => { total++; console.log((cond ? "✓ " : "✗ ") + label); if (!cond) fails++; };
/* 「实得 …」这段见证**必须由断言体自己在量完之后写入 `seen`**，不能写在 label 里：
   `okTry(fn, label)` 的实参在 `fn()` 跑**之前**就求值完了，写在 label 里的 `实得`
   读到的是**上一条**断言留下的状态 —— 那段见证看起来在作证，其实在说别的东西
   （实测过：S7 的 witness 印的是 S6 留下的那个数组、S12 的印的是空串）。
   本仓记过「探针的标签也会说谎」，这一处就是同一个形状。 */
let seen = "";
/* 一律 JSON 引号化：指纹里带 `\u0001`/`\u0002` 两个分隔符，**原样**打进日志的是一串
   看不见的字节（读日志的人与 grep 都看不见它们），引号化后至少是 `\u0001` 这样的可读转义。
   顺带把多行的 HTML 压成单行，一条断言占一行。 */
const why = v => "（实得 " + JSON.stringify(v) + "）";
/* 断言体自己抛（夹具落错分支、变异把某个函数改坏）时，那是**红**，不是**截断** ——
   不包的话它后面那截断言一条都不执行，只看 stdout 的用法读到的会是「跑完了」。 */
const okTry = (fn, label) => {
  seen = "";
  try { ok(!!fn(), label + seen); }
  catch (e) { ok(false, label + seen + ` ⇒ 抛了：${e && e.message}`); }
};

/* ---- 合成夹具（RFC 2606 保留域 / 占位串；不含任何真实邮件内容或 token） ---- */
const M1 = { id: "SYNTH-MAIL-1", subject: "SYNTH-SUBJ-1", sender: "s0@example.invalid", received: "2026-09-01T08:00:00Z" };
const M2 = { id: "SYNTH-MAIL-2", subject: "SYNTH-SUBJ-2", sender: "s1@example.invalid", received: "2026-09-02T08:00:00Z" };

const resetState = (mails = []) => run(
  `mtState = { mails: ${JSON.stringify(mails)}, labels: {}, loading: false, error: "", warnings: [],
               items: {}, extracting: false, extractWarnings: [], missing: 0, dropped: 0 };`);

/* ============================ 装配自证 ============================ */
run(`document.getElementById("llmBaseUrl").value = "http://127.0.0.1:9/v1";
     document.getElementById("llmApiKey").value  = "TEST-KEY-NOT-REAL";
     document.getElementById("llmModel").value   = "TEST-MODEL-NOT-REAL";`);
run(`localStorage.setItem("sc_lang", "zh");`);

okTry(() => { const got = val(`esc("<b>")`); seen = why(got); return got === "&lt;b&gt;"; },
  "装配自证：DOM 替身的 createElement 真的转义（esc(\"<b>\") 不回吐 <b>）");
okTry(() => val(`$("mtTodo") === null`) === true,
  "装配自证：#mtTodo 在替身里**缺席**（renderMailTodo 的第一句早退因此生效）");
okTry(() => {
  /* 正对照：证明「#mtTodo 缺席 ⇒ 短路」是真的短路，不是「renderMailTodo 恰好什么都不做」。
     临时把它放回去，看它**确实会**写 mtTodo 的 innerHTML。 */
  absent.delete("mtTodo");
  run(`renderMailTodo();`);
  const wrote = String(nodes["mtTodo"].innerHTML || "");
  absent.add("mtTodo");
  seen = why(wrote);
  return wrote.includes("mail-empty");
}, "装配自证（正对照）：把 #mtTodo 放回去时 renderMailTodo **确实会**渲染");

/* ============================ S1–S3：指纹与勾选键 ============================ */
okTry(() => {
  const a = val(`mtFingerprint(${JSON.stringify(M1)})`), b = val(`mtFingerprint(${JSON.stringify(M1)})`);
  seen = why(a === b ? a : [a, b]);
  /* 守卫，与 S2/S3 同一道、同一写法：`val()` 把沙箱里的异常**吞掉**并返回未传的 `fallback`
     ⇒ 被测对象抛了时拿到的是 `undefined`，`undefined === undefined` **照绿**，而同一行的见证
     当场印着 `实得 undefined` —— 判决与自己贴出的见证互相矛盾。所以先要求拿到的是
     **非空真字符串**，再比相等。 */
  if (typeof a !== "string" || !a) return false;
  return a === b;
}, "S1 mtFingerprint 对同一封邮件两次调用返回相同串");

okTry(() => {
  /* **三封邮件的 `id` 刻意相同** —— 这条断言问的是「主题/发件人/收信时间任一不同是否改变指纹」，
     只有把 id 钉成同一个，「指纹随字段变」才与「指纹随 id 变」区分得开。
     若三封各给不同 id，`--mutate-key-to-uid`（指纹退化成 m.id）下它们**照样互不相同**，
     这条断言就不承重了。 */
  const base = { id: "SYNTH-SAME-ID", subject: "SYNTH-S", sender: "s0@example.invalid", received: "2026-09-01T08:00:00Z" };
  const vs = [base, { ...base, subject: "SYNTH-S-2" },
                    { ...base, sender: "s1@example.invalid" },
                    { ...base, received: "2026-09-05T08:00:00Z" }];
  const fps = vs.map(m => val(`mtFingerprint(${JSON.stringify(m)})`));
  seen = why(fps);
  if (fps.some(x => typeof x !== "string" || !x)) return false;
  for (let i = 0; i < fps.length; i++)
    for (let j = i + 1; j < fps.length; j++) if (fps[i] === fps[j]) return false;
  return true;
}, "S2 主题/发件人/收信时间任一不同 ⇒ 指纹不同（同 id 的四个变体两两不同）");

okTry(() => {
  const fp = val(`mtFingerprint(${JSON.stringify(M1)})`);
  /* 同一道守卫（理由同 S1）：`fp` 是 `undefined` 时，下面 `JSON.stringify(fp)` 会把字面量
     `undefined` 直接拼进表达式 ⇒ 比的是 `"undefined0"` 与 `"undefined1"` 两个**退化出来的**
     字符串，性质（下标不同 ⇒ 键不同）仍为真，却没有任何东西证明它拿到过真指纹。 */
  if (typeof fp !== "string" || !fp) { seen = why(fp); return false; }
  const k0 = val(`mtDoneKey(${JSON.stringify(fp)}, 0)`);
  const k1 = val(`mtDoneKey(${JSON.stringify(fp)}, 1)`);
  seen = why([k0, k1]);
  return typeof k0 === "string" && k0 !== k1;
}, "S3 mtDoneKey(fp,0) !== mtDoneKey(fp,1)（同一封信的两条待办不共用键）");

/* ============================ S4：勾一条不连带 ============================ */
okTry(() => {
  store.delete("sc_mailDone");
  resetState([M1]);
  run(`mtState.items[mtFingerprint(mtState.mails[0])] =
         { id: mtState.mails[0].id, reminders: [{title:"R0", due_date:""}, {title:"R1", due_date:""}],
           calendar_events: [] };`);
  run(`mtSetDone(mtDoneKey(mtFingerprint(mtState.mails[0]), 0), true);`);   // 勾第 0 条
  const html = run(`mtTodoHtml()`);                                          // 重渲（每次都重读存储）
  const checked0 = run(`mtTodoHtml().includes('data-done="' + escAttr(mtDoneKey(mtFingerprint(mtState.mails[0]), 0)) + '" checked')`);
  const checked1 = run(`mtTodoHtml().includes('data-done="' + escAttr(mtDoneKey(mtFingerprint(mtState.mails[0]), 1)) + '" checked')`);
  seen = why({ 第0条勾: checked0, 第1条勾: checked1, 两行都在场: String(html).includes("R0") && String(html).includes("R1") });
  return checked0 === true && checked1 === false && String(html).includes("R0") && String(html).includes("R1");
}, "S4 勾第 0 条后第 1 条仍未勾（勾一条不连带；且两行都在场）");

/* ============================ S5：多标签共享键 ============================ */
okTry(() => {
  store.delete("sc_mailDone");
  resetState([M1]);
  run(`mtState.labels["${M1.id}"] = ["SYNTH-RULE-A", "SYNTH-RULE-B"];`);
  run(`mtState.items[mtFingerprint(mtState.mails[0])] =
         { id: mtState.mails[0].id, reminders: [{title:"R0", due_date:""}], calendar_events: [] };`);
  const k0 = val(`mtDoneKey(mtFingerprint(mtState.mails[0]), 0)`);
  const attr = JSON.stringify(`data-done="` + k0 + `"`);
  const rowsBefore = run(`mtTodoHtml().split(${attr}).length - 1`);        // 一行一首
  run(`mtSetDone(mtDoneKey(mtFingerprint(mtState.mails[0]), 0), true);`);   // 勾其中一处
  const checkedAfter = run(`mtTodoHtml().split(${JSON.stringify(`data-done="` + k0 + `" checked`)}).length - 1`);
  seen = why({ 勾前这一键出现几次: rowsBefore, 勾后这一键带checked几次: checkedAfter });
  return rowsBefore === 2 && checkedAfter === 2;
}, "S5 一封命中两个分类 ⇒ 两处各一行；勾其中一处 ⇒ 两处都变勾（共享键）");

/* ============================ S6/S7：时间表 ============================ */
okTry(() => {
  resetState([M1]);
  run(`mtState.items[mtFingerprint(mtState.mails[0])] =
         { id: mtState.mails[0].id, summary: "",
           reminders: [{title:"看完论文", due_date:""}], calendar_events: [] };`);
  const noDate = run(`mtScheduleRows().length`);
  /* 正对照：给同一个 reminder 补上日期后**必须**进表 —— 否则上面那个 0 可能只是夹具落错了地方。 */
  run(`mtState.items[mtFingerprint(mtState.mails[0])].reminders[0].due_date = "2026-09-04T09:00:00Z";`);
  const withDate = run(`mtScheduleRows().length`);
  seen = why({ 没有due_date时行数: noDate, 补上due_date后行数: withDate });
  return noDate === 0 && withDate === 1;
}, "S6 抽不出日期的 reminder 不进 mtScheduleRows()（正对照：补上日期后进表 1 行）");

okTry(() => {
  resetState([M1, M2]);
  run(`mtState.items[mtFingerprint(mtState.mails[0])] =
         { id: mtState.mails[0].id, summary: "",
           reminders: [{title:"R1", due_date:"2026-09-02T09:00:00Z"}],
           calendar_events: [{title:"E1", start:"2026-09-03T09:00:00Z"}, {title:"E2", start:"2026-09-01T09:00:00Z"}] };`);
  const whens = val(`mtScheduleRows().map(r => r.when)`);
  /* 夹具自己的顺序**量出来**放进见证：上一版这里是手写的字面量 —— 见证里混进了一句没人量的
     断言（本仓记过「探针的标签也会说谎」）。读的是夹具那两个数组本身，按 events → reminders 拼。 */
  const fixtureOrder = val(`(() => { const it = mtState.items[mtFingerprint(mtState.mails[0])];
      return [...it.calendar_events.map(e => e.start), ...it.reminders.map(r => r.due_date)]; })()`);
  const fixtureAsc = Array.isArray(fixtureOrder) && fixtureOrder.every(
    (s, i) => i === 0 || new Date(fixtureOrder[i - 1]).getTime() < new Date(s).getTime());
  seen = why({ 夹具原始顺序: fixtureOrder, 夹具原始顺序已是升序: fixtureAsc, 实得when: whens });
  if (!Array.isArray(whens) || whens.length !== 3) return false;
  const ms = whens.map(s => new Date(s).getTime());
  if (ms.some(x => !isFinite(x))) return false;
  /* 夹具原始顺序本身**不是**升序（E1 09-03 → E2 09-01 → R1 09-02，见上面 seen 里量出来的
     `夹具原始顺序已是升序: false`）⇒ 「结果升序」不是恒真：不排序的实现会原样回吐那个非升序的数组。 */
  for (let i = 1; i < ms.length; i++) if (!(ms[i - 1] < ms[i])) return false;
  return new Set(whens).size === 3;
}, "S7 mtScheduleRows() 按时间升序（夹具顺序非升序）");

/* ============================ S8：坏 JSON 的规则 ============================ */
okTry(() => {
  /* 正对照先行：合法 JSON 必须能往返 —— 否则下面那个 `[]` 可能只是 localStorage 替身坏了。 */
  store.set("sc_mailRules", JSON.stringify([{ id: "r-1", name: "SYNTH-RULE", senders: ["s0@example.invalid"], keywords: [], search_body: false }]));
  const good = val(`mtLoadRules()`);
  const goodOk = Array.isArray(good) && good.length === 1 && good[0].name === "SYNTH-RULE";
  store.set("sc_mailRules", "{{{ 这不是 JSON");
  /* 包一层 try：`mtLoadRules` 抛的话要变成**红**，不能让整份自检在这里截断。 */
  const bad = JSON.parse(run(`JSON.stringify((() => { try { return { v: mtLoadRules() }; }
                                                        catch(e){ return { threw: String(e) }; } })())`));
  store.delete("sc_mailRules");
  seen = why({ 合法JSON时: good, 坏JSON时: bad });
  return goodOk && !("threw" in bad) && Array.isArray(bad.v) && bad.v.length === 0;
}, "S8 mtLoadRules() 在 localStorage 是坏 JSON 时返回 [] 且不抛（正对照：合法 JSON 正常往返）");

/* ============================ S9：空规则判无效 ============================ */
okTry(() => {
  const cases = [
    [{ id: "a", name: "",        senders: ["s0@example.invalid"], keywords: [] }, false, "名称为空"],
    [{ id: "b", name: "SYNTH",   senders: [],                    keywords: [] }, false, "两个列表都空"],
    [{ id: "c", name: "SYNTH",   senders: ["  "],                keywords: [] }, false, "只有空白项"],
    [{ id: "d", name: "SYNTH",   senders: [],                    keywords: ["  "] }, false, "只有空白关键词"],
    [{ id: "e", name: "SYNTH",   senders: ["s0@example.invalid"], keywords: [] }, true,  "正对照：名称 + 非空发件人"],
    [{ id: "f", name: "SYNTH",   senders: [],                    keywords: ["论文"] }, true, "正对照：名称 + 非空关键词"],
  ];
  const got = cases.map(([r]) => val(`mtRuleValid(${JSON.stringify(r)})`));
  seen = why(cases.map(([r, want], i) => `${r.name || "(无名)"}/${JSON.stringify(r.senders)}/${JSON.stringify(r.keywords)} 期望${want} 实得${got[i]}`));
  return cases.every(([, want], i) => got[i] === want);
}, "S9 空规则（名称为空 / 两个列表都空）⇒ mtRuleValid 为 false（含两条正对照）");

/* ============================ S10：抽取账本 ============================ */
okTry(() => {
  /* 前提自检**问实际状态**，不写 `return true`：恒真的「前提」会虚增一条断言，
     还会在夹具没落地时让后面两条看起来是「账本坏了」而不是「夹具坏了」。 */
  resetState([M1, M2]);
  extractStub.reset();
  const n = val(`mtState.mails.length`);
  const items = val(`Object.keys(mtState.items || {}).length`);
  seen = why({ 窗口内邮件数: n, 账本条目数: items, 桩已记的调用次数: extractStub.calls.length });
  return n === 2 && items === 0 && extractStub.calls.length === 0;
}, "S10 前提：两封合成信已就位、账本为空、桩的调用次数已清零");
await (async () => {
  let firstIds = null, callsAfterFirst = null, callsAfterSecond = null;
  try {
    await settle(run(`mtExtract()`));
    firstIds = extractStub.calls.length ? extractStub.calls[0].ids.slice() : null;
    callsAfterFirst = extractStub.calls.length;
    await settle(run(`mtExtract()`));
    callsAfterSecond = extractStub.calls.length;
  } catch (e) {
    ok(false, `S10 抽取账本 ⇒ 抛了：${e && e.message}`);
    return;
  }
  ok(firstIds !== null && firstIds.length === 2,
    `S10 正对照：第 1 次抽取把**两封**都发了出去（实得 ids 条数 ${firstIds ? firstIds.length : "null"}）`);
  ok(callsAfterFirst === 1 && callsAfterSecond === 1,
    `S10 请求过、响应里没给回来的信，第 2 次不再被请求（fetch 次数 ${callsAfterFirst} → ${callsAfterSecond}）`);
})();

/* ============================ S11：时间表空态三分支 ============================ */
const schedFixture = (item) => {
  resetState([M1]);
  if (item) run(`mtState.items[mtFingerprint(mtState.mails[0])] = ${JSON.stringify(item)};`);
};
okTry(() => {
  /* 三个夹具**先全跑完**、把 `seen` 写好，再做比较 ——
     写成「边比边早退」的话，红的正是**第一条不相等**那里，而 `seen` 还没赋值：
     最需要见证的那一次反而没有见证（本轮实测到过：`--mutate-sched-empty-shared` 下
     这一行起初只印出断言名，一个数都没有）。 */
  /* A：抽过、清单上有「看完论文」，但它没有日期 ⇒ 时间表空态必须是**自己那句**。 */
  schedFixture({ id: M1.id, summary: "", reminders: [{ title: "看完论文", due_date: "" }], calendar_events: [] });
  const msgA = val(`mtSchedEmptyMsg()`);
  /* B：抽过、什么都没抽到（账本里有空条目）。 */
  schedFixture({ id: M1.id, summary: "", reminders: [], calendar_events: [] });
  const msgB = val(`mtSchedEmptyMsg()`);
  /* C：从没抽过（账本空）。 */
  schedFixture(null);
  const msgC = val(`mtSchedEmptyMsg()`);
  const wantA = val(`t("mailtodo.sched_none_dated")`);
  const other = val(`t("mailtodo.extract_none")`);
  const need  = val(`t("mailtodo.need_extract")`);
  seen = why({ A_有reminder无日期: msgA, B_抽过但空条目: msgB, C_从没抽过: msgC,
               三句原文: [wantA, other, need] });
  return msgA === wantA && msgB === other && msgC === need && msgA !== msgB && msgB !== msgC;
}, "S11 时间表空态三分支各说各的（A/B/C 各自对上 sched_none_dated / extract_none / need_extract，且三句互不相同）");

okTry(() => {
  /* 夹具 A + 渲染断言：空态那个 div 里印的必须是时间表那句话，不是清单那句话。 */
  schedFixture({ id: M1.id, summary: "", reminders: [{ title: "看完论文", due_date: "" }], calendar_events: [] });
  const html = String(run(`mtScheduleHtml()`));
  const escSched  = val(`esc(t("mailtodo.sched_none_dated"))`);
  const escExtract = val(`esc(t("mailtodo.extract_none"))`);
  seen = why({ 渲染出的空态: html, 含时间表那句: html.includes(escSched), 含清单那句: html.includes(escExtract) });
  return html.includes(escSched) && !html.includes(escExtract);
}, "S11（渲染）mtScheduleHtml() 的空态印的是时间表那句话、不含清单那句话");

/* ============================ S12：警告条按码取数 ============================ */
okTry(() => {
  resetState([M1]);
  run(`mtState.extractWarnings = ["ai_dropped", "ids_missing"];
       mtState.dropped = 3;
       mtState.missing = 5;`);
  run(`renderMailTodoWarnings();`);
  const text = String(nodes["mtWarn"].textContent);
  const wantDropped = val(`t("mailtodo.warn.ai_dropped", { n: 3 })`);
  const wantMissing = val(`t("mailtodo.warn.ids_missing", { n: 5 })`);
  /* 两个数**故意取不同的值**：取同一个数时，「按码取数」与「一律喂 missing」分不出来，
     这条断言就变成恒真。 */
  if (wantDropped === wantMissing) throw new Error("两个期望串相同 ⇒ 夹具失去分辨力");
  seen = why({ 警告条实得: text, 含ai_dropped那句: text.includes(wantDropped), 含ids_missing那句: text.includes(wantMissing) });
  return text.includes(wantDropped) && text.includes(wantMissing);
}, "S12 警告条按码取数（ai_dropped 用 dropped=3、ids_missing 用 missing=5）");

console.log(fails ? `\n✗ ${fails} 条未通过（共 ${total} 条断言）` : `\n✓ 全部通过（共 ${total} 条断言）`);
process.exit(fails ? 1 : 0);
