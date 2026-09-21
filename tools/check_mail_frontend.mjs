#!/usr/bin/env node
/**
 * 邮件前端自检：守住 `renderMail()` 的**前端那半条 `mail_index` 契约**。
 *
 * 存在的意义：后端那半条由 pytest 守；本仓没有前端测试框架，`node --check` 又只查语法
 * —— 这里用最小 DOM 替身把 frontend/*.js 逐文件 runInContext 真跑一遍（不拼成一个脚本，
 * 保持浏览器的 <script> 边界）。它守的是两条容易被**静默**破坏的契约：
 *   1) 渲染结构：两级分组（课程 → 类型 → 条目）与按钮的 data-g/t/i/e 下标；
 *   2) 写入路径：点第 N 条 DDL 的按钮 → 确认弹层显示的必须是**那一条自己**，
 *      写进 /api/add_calendar_event（或 /api/add_reminder）的也必须是**那一条自己**。
 * 第 2 条最要紧：`data-e` 错位的写入是**静默**的，进的是**用户的真实日历**。
 * `mail_index` 是 `mailState.mails` 里的下标（后端 enumerate 赋的 `index`），**不是**条目
 * 序号 —— 夹具里两者刻意错开，靠条目序号取行会把整封邮件对错。
 *
 * 什么时候该跑：任何改动 frontend/app.js 里的 `renderMail` / `mailRenderGroups` /
 * `mailItemHtml` / `$("mailList")` 的点击写入路径 / `mailState` 结构之后。
 *
 * 零出网：`fetch` 是桩，只放行上面那两条写入端点并记账，其余 URL 一律 throw。
 * 全部夹具都是合成的，不含任何真实邮件内容、真实邮箱地址或 token：标签一律 `SYNTH-*`，
 * 发件地址用 RFC 2606 保留域（`s0@example.invalid` / `s1@example.invalid`，永不解析）。
 *
 * 用法：node tools/check_mail_frontend.mjs [变异开关…]
 *
 *   变异开关（用来证明上面的断言不是空转；不属于应用本身，只在改这份自检时手工跑）：
 *     --mutate-r2          R2 的并发修复改回修复前写法
 *     --mutate-meta        计数/错误卡互斥改回修复前写法
 *     --mutate-map         `mailState.mails[it.mail_index]` → `mailState.mails[ii]`
 *     --mutate-rm-filter   只把 reminders 那一路改成「先 filter 再 map」（下标重排）
 *     --mutate-no-esc      删掉 F4 那一行（Esc 不再关 confirmPrompt）
 *     --mutate-widx[=EXPR] 把取行写成 EXPR，默认 `arr[0]`（按下标取行 → 恒取第一条）
 *     --mutate             = r2 + meta + map + widx **四个一起上**，红 8 条：
 *                          B1 B2 B4 B5 C2 E3 H1 H2
 *   `--mutate-rm-filter` 与 `--mutate-no-esc` **不在** `--mutate` 里，必须各自单独跑。
 *   取行表达式带方括号，shell 下请加引号，否则会被当通配符：
 *     node tools/check_mail_frontend.mjs --mutate-widx='arr[1]'
 *     node tools/check_mail_frontend.mjs --mutate-widx='arr.at(-1)'
 *   每个变异锚点必须**恰好命中一次**；没命中、或替换后与原文一字不差（空转探针），
 *   都会直接抛错退出。替换成功时会在 stdout 打一行 `[mutate] …`，自证改到了哪一行。
 *
 * 退出码：0 = 全部断言通过；1 = 有断言未通过 / 变异开关用错（锚点没命中、命中不止一次、
 *   替换后与原文一字不差、`--mutate-widx` 的表达式为空）/ 变异后的源码加载不了 / 源码读不到。
 */
import vm from "node:vm";
import fs from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/* 源码相对**本脚本**解析（与 tools/check_dom_ids.mjs、check_i18n_keys.mjs 同款）：
   这样从任何 cwd 跑都读得到 frontend/，而不是依赖「恰好站在仓库根」。 */
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const nodes = {};
const docListeners = {};
/* 元素级监听器（与 docListeners 同形）。此前 `mkNode` 的 `addEventListener(){}` 是**空函数**，
   于是 app.js 里 `$("mailList").addEventListener("click", …)` 什么都没注册 —— 那个点击
   处理器（按钮显示哪条 / 写下去哪条）在整份自检里从未被执行过。 */
const elListeners = {};
/* fetch 桩的账本：只记「写入类端点被调了几次、body 是什么」，供 H 段断言。 */
const fetchLog = [];
/* `hidden` 默认 **true**：index.html 里各弹层都带 hidden 属性。以前默认 false 是**主动误导** ——
   Esc 会被前面那条 `!$("loginPrompt").hidden` 分支截胡（实测：探针第一次跑就抛错），
   于是 G 段的 Esc 断言根本走不到 #confirmPrompt。`click()` 也是必需品：
   dismissLoginPrompt 走 `$("btnLoginPromptNo").click()`。 */
const mkNode = id => (nodes[id] ||= {
  id, innerHTML: "", textContent: "", hidden: true, value: "",
  classList: { add(){}, remove(){}, toggle(){} }, style: {}, dataset: {},
  /* `id` 在闭包里可用：mkNode 是 `id => (nodes[id] ||= {…})`，方法只会在对象建好之后
     才被调用，所以这里安全。 */
  addEventListener(type, fn){ ((elListeners[id] ||= {})[type] ||= []).push(fn); },
  focus(){}, remove(){}, removeAttribute(){}, click(){},
  querySelector(){ return mkNode("q"); },
  appendChild(){}, setAttribute(){}, getAttribute(){ return null; },
});
const sandbox = {
  console, setTimeout, clearTimeout,
  document: {
    getElementById: id => mkNode(id),
    querySelector: () => mkNode("q"),
    querySelectorAll: () => [],
    createElement: () => {
      const o = { _t: "", appendChild(){}, style: {}, classList: { add(){} }, dataset: {} };
      Object.defineProperty(o, "textContent", { set(v) { o._t = String(v == null ? "" : v); }, get() { return o._t; } });
      Object.defineProperty(o, "innerHTML", { get() { return o._t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); } });
      return o;
    },
    /* 捕获监听器：不捕获的话 `document.addEventListener` 是空函数，app.js:270 注册的 keydown
       处理器**从未被调用过**（F4 因此没有任何门禁看得见 —— 它的失败模式还是静默的：
       一次 Esc 之后所有写入确认被永久卡死）。 */
    documentElement: mkNode("html"), title: "",
    addEventListener(type, fn){ (docListeners[type] ||= []).push(fn); },
  },
  localStorage: { getItem: () => null, setItem(){}, removeItem(){} },
  matchMedia: () => ({ matches: false, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){} }),
  /* **这不是出网**，是桩：只记录 + 回一个最小成功响应。
     原意（「自检不该发起 fetch」）保留得不折不扣，只是把口径写准 —— 它说的从来是
     「不该发**别的** fetch」，不是「一律不许发」：`add_calendar_event` / `add_reminder`
     这两条写入端点是 H 段要观察的对象，必须放行并记账；**其余 URL 仍然 throw**。 */
  fetch: (url, opts) => {
    const u = String(url);
    if (u !== "/api/add_calendar_event" && u !== "/api/add_reminder")
      throw new Error("自检不该发起 fetch：" + u);
    fetchLog.push({ url: u, body: JSON.parse((opts && opts.body) || "{}") });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  },
  open: () => null,
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
vm.createContext(sandbox);

/* 变异开关：把修复各自改回修复前的写法，验证上面的判据不是空转。
   逐个跑（--mutate-r2 / --mutate-meta / --mutate-map / --mutate-widx）才能说清「哪条判据
   抓住了哪个变异」；`--mutate` = r2 + meta + map + **widx** 四个一起上（红集 8 条：
   B1 B2 B4 B5 C2 E3 H1 H2）。`--mutate-rm-filter` 与 `--mutate-no-esc` **不在** `--mutate`
   里 —— 它们各自单独跑（见文件头）。
   本仓吃过「空转探针」的亏：一个没真的改到东西的变异，什么都不能证明 —— 故替换走
   `mutate()`，锚点必须恰好命中一次、且替换后必须与原文不同，否则直接抛错。 */
const MUTATE      = process.argv.includes("--mutate");
const MUTATE_R2   = process.argv.includes("--mutate-r2")   || MUTATE;
const MUTATE_META = process.argv.includes("--mutate-meta") || MUTATE;
const MUTATE_MAP  = process.argv.includes("--mutate-map")  || MUTATE;
const MUTATE_RM_FILTER = process.argv.includes("--mutate-rm-filter");
const MUTATE_NO_ESC = process.argv.includes("--mutate-no-esc");
/* `--mutate-widx` 可带表达式：`--mutate-widx='arr[1]'`。默认 `arr[0]` 保持 `--mutate` 的
   红集不变（8 条）；表达式覆盖是**追加**的能力，用来在仓内自证「夹具钉住了常量下标」。 */
const WIDX_ARG  = process.argv.find(a => a === "--mutate-widx" || a.startsWith("--mutate-widx="));
const WIDX_EXPR = WIDX_ARG && WIDX_ARG.includes("=") ? WIDX_ARG.slice(WIDX_ARG.indexOf("=") + 1) : "arr[0]";
const MUTATE_WIDX  = !!WIDX_ARG || MUTATE;
if (MUTATE_WIDX && !WIDX_EXPR.trim()) throw new Error("--mutate-widx 的表达式为空");

const mutate = (src, anchor, repl, label) => {
  const hits = src.split(anchor).length - 1;
  if (hits !== 1) throw new Error(`[${label}] 变异锚点命中 ${hits} 次（应为 1）：${JSON.stringify(anchor)}`);
  /* **函数式替换**：`String.replace(anchor, repl)` 会把 repl 里的 `$&`/`$1` 当**替换模式**
     展开，于是真正写进去的东西与下一行打出的 witness 可以**不是一回事** —— 而这一行
     witness 正是「变异非空转」的证明本身，会撒谎的证明比没有证明更糟（本仓吃过空转探针
     的亏）。传函数则一律按**字面**替换，witness 与实际替换必然一致。 */
  const out = src.replace(anchor, () => repl);
  if (out === src) throw new Error(`[${label}] 变异未生效（替换后与原文一字不差）：${JSON.stringify(repl)}`);
  /* **回归护栏，当前结构下不会触发 —— 别把它当成活的断言。** 函数式替换下
     `out = A + repl + B`，故 `out.includes(repl)` **恒真**（复审按此断言模糊替换 30000 次，
     命中 0 次）。留着它是为了钉住「替换必须是**字面**替换」这件事：一旦有人把上面那行改回
     `src.replace(anchor, repl)`（字符串式），repl 里的 `$&`/`$1` 会被当替换模式展开，此时
     只要 repl 含有 `$&` 一类，这条就会立刻炸出来 —— 它守的是替换方式，不是某一次替换的结果。
     repl 为空（`--mutate-no-esc`）时由上面那条「替换后与原文一字不差」拦住。 */
  if (repl && !out.includes(repl))
    throw new Error(`[${label}] 实际替换与 witness 不一致：${JSON.stringify(repl)}`);
  /* witness 必须**如实**表示真正落下去的东西：单行且无控制字符时**原样**打（与以往逐字一致），
     否则 JSON 引号化 —— 新行/制表符等被转义、内容一概不截断。此前只打 `split("\n")[0]`，
     多行替换会打出**一行** witness 配**多行**插入，即 N2 说的「witness 撒谎」。 */
  const witness = /[\u0000-\u001F\u007F]/.test(repl) ? JSON.stringify(repl) : repl;
  console.log(`[mutate] ${label} → ${witness}`);
  return out;
};

for (const f of ["i18n.js", "util.js", "app.js", "shell.js"]) {
  let src = fs.readFileSync(join(ROOT, "frontend", f), "utf8");
  if (f === "app.js") {
    if (MUTATE_R2) {
      src = mutate(src, "if(confirmResolve) return Promise.resolve(false);",
        "if(confirmResolve) return confirmResolve;", "r2");  // R2 修复前
    }
    if (MUTATE_META) {
      src = mutate(src, "(mailState.loading || mailState.error || !mailState.mails.length)",
        "(mailState.loading || !mailState.mails.length)", "meta");  // 缺陷修复前
    }
    if (MUTATE_MAP) {
      src = mutate(src, "mailState.mails[it.mail_index]",
        "mailState.mails[ii]", "map");                        // 错配成条目序号
    }
    if (MUTATE_NO_ESC) {
      /* 回归成「Esc 不关 confirmPrompt」：F4 那一行整行删掉。
         锚点末尾的换行是刻意的 —— 只删这一行本身，不留空行。 */
      src = mutate(src, `  if (!$("confirmPrompt").hidden){ $("btnConfirmNo").onclick(); return; }\n`,
        "", "no-esc");
    }
    if (MUTATE_RM_FILTER) {
      /* 只把 reminders 那一路改成「先 filter 再 map」：越界/空元素被丢掉后**重新编号**，
         而点击处理器仍按原始下标取行 —— 按钮显示的与写下去的会是两条不同的 DDL。 */
      src = mutate(src, `const rms = (it.reminders || []).map((e, ei) => {
    if(!e || typeof e !== "object") return "";`,
        `const rms = (it.reminders || []).filter(e => e && typeof e === "object").map((e, ei) => {`,
        "rm-filter");
    }
    if (MUTATE_WIDX) {
      /* 取行不再按下标：「点第 2 条的按钮、弹出并写入第 1 条」—— 错位的写入是静默的，
         写进去的还是用户的真实日历。没有这个开关，H 段的断言无法双向证明自己非恒真。
         表达式可覆盖：四种常量下标变异体（0 / 1 / at(-1) / length-1）都要能被钉住。 */
      src = mutate(src, "const row = arr[Number(b.dataset.e)];",
        `const row = ${WIDX_EXPR};`, "widx");
    }
  }
  try {
    vm.runInContext(src, sandbox, { filename: f });
  } catch (err) {
    /* 变异把代码改成了语法错误（例如表达式里带了 `$&` 一类）时，要让失败说清是谁干的，
       而不是抛一个看不出与变异有关的裸 SyntaxError。 */
    throw new Error(`加载 frontend/${f} 失败（跑过变异开关的话，多半是它把代码改坏了）：${err.message}`);
  }
}

const run = code => vm.runInContext(code, sandbox);

/* 变异后的实现可能真的挂住（R2 改回旧写法时，第二问拿到第一问那个永不落地的 promise）。
   挂住也是「红」，不该让整轮自检吊死，故给 await 套一个限时。 */
const settle = async p => {
  let t;
  const guard = new Promise(r => { t = setTimeout(() => r("SYNTH-TIMEOUT"), 500); });
  const v = await Promise.race([p, guard]);
  clearTimeout(t);
  return v;
};

let fails = 0, total = 0;
const ok = (cond, label) => { total++; console.log((cond ? "✓ " : "✗ ") + label); if (!cond) fails++; };

/* ---- 合成数据（全为占位串，绝不含真实邮件内容） ---- */
run(`
  mailState.mails = [
    { index: 0, subject: "SYNTH-SUBJ-0", sender: "s0@example.invalid", received: "2026-09-01T00:00:00Z" },
    { index: 1, subject: "SYNTH-SUBJ-1", sender: "s1@example.invalid", received: "2026-09-02T00:00:00Z" },
  ];
  mailState.groups = [
    { course: "SYNTH-COURSE", types: [
      { type: "SYNTH-TYPE", items: [
        /* 两条的 mail_index 与条目序号**刻意错开**（条目 0→第 1 封，条目 1→第 0 封）：
           若实现按条目序号取行，两封会被整体对调，而「某串在不在」照旧为真。 */
        { mail_index: 1, summary: "SYNTH-SUM-A",
          calendar_events: [{ title: "SYNTH-EV-A", start: "2026-09-10T09:00", end: "2026-09-10T10:00" }],
          reminders: [{ title: "SYNTH-RM-A", due_date: "2026-09-11T23:59" }] },
        { mail_index: 0, summary: "SYNTH-SUM-B",
          calendar_events: [{ title: "SYNTH-EV-B", start: "2026-09-12T09:00", end: "2026-09-12T10:00" }],
          reminders: [{ title: "SYNTH-RM-B", due_date: "2026-09-13T23:59" }] },
        { mail_index: 99, summary: "SYNTH-OUT-OF-RANGE" },
      ] },
    ] },
  ];
  mailState.loading = false; mailState.error = "";
  renderMail();
  globalThis.__out = document.getElementById("mailList").innerHTML;
  globalThis.__meta = document.getElementById("mailMeta").textContent;
`);
const out = sandbox.__out;
ok(out.includes('class="card mail-group"') && out.includes("SYNTH-COURSE"), "A1 渲染出课程分组");
ok(out.includes("SYNTH-TYPE"), "A2 渲染出类型小节");
/* 按 <div class="mail-item"> 切开，逐块看「这一块的摘要配的是不是这一封」。
   断言必须**同时**要求该在的在场、不该在的缺席，只查在场等于没查。 */
const items = out.split('<div class="mail-item">').slice(1);
const pair = (blk, subj, sum, ev, rm) =>
  !!blk && blk.includes(subj) && blk.includes(sum) && blk.includes(ev) && blk.includes(rm);
ok(items.length === 2, `B0 越界条目不成卡（实得 ${items.length} 块）`);
ok(pair(items[0], "SYNTH-SUBJ-1", "SYNTH-SUM-A", "SYNTH-EV-A", "SYNTH-RM-A"),
   "B1 条目 0 的摘要/DDL 与 mails[1] 同框（按 mail_index 取行）");
ok(items[0] && !items[0].includes("SYNTH-SUBJ-0") && !items[0].includes("SYNTH-SUM-B"),
   "B2 条目 0 的框里没有第 0 封、也没有条目 1 的摘要");
ok(pair(items[1], "SYNTH-SUBJ-0", "SYNTH-SUM-B", "SYNTH-EV-B", "SYNTH-RM-B"),
   "B4 条目 1 的摘要/DDL 与 mails[0] 同框");
ok(items[1] && !items[1].includes("SYNTH-SUBJ-1") && !items[1].includes("SYNTH-SUM-A"),
   "B5 条目 1 的框里没有第 1 封、也没有条目 0 的摘要");
ok(out.includes('data-w="ev" data-g="0" data-t="0" data-i="0" data-e="0"'), "A3 日历按钮下标齐全 g/t/i/e");
ok(out.includes('data-w="rm" data-g="0" data-t="0" data-i="0" data-e="0"'), "A4 提醒按钮下标齐全 g/t/i/e");
ok(out.includes('data-i="1" data-e="0"'), "A6 第二条的按钮下标独立（不串号）");
ok(out.includes("SYNTH-SUM-A") && out.includes("SYNTH-EV-A") && out.includes("SYNTH-RM-A"), "A5 摘要与两条 DDL 都渲染出来");
ok(!out.includes("SYNTH-OUT-OF-RANGE"), "B3 mail_index 越界的条目被跳过且不抛错");
ok(sandbox.__meta === "共 2 封", `C1 正常态计数正确（实得 ${JSON.stringify(sandbox.__meta)}）`);

/* C2：错误态下计数必须为空 —— Task 7 遗留缺陷（错误卡旁边挂着过期计数） */
run(`
  mailState.error = "SYNTH-ERR";
  renderMail();
  globalThis.__meta2 = document.getElementById("mailMeta").textContent;
  globalThis.__out2 = document.getElementById("mailList").innerHTML;
`);
ok(sandbox.__meta2 === "", `C2 错误态下计数为空（实得 ${JSON.stringify(sandbox.__meta2)}）`);
ok(sandbox.__out2.includes("SYNTH-ERR") && !sandbox.__out2.includes("SYNTH-SUBJ-1"), "C3 错误态只出错误卡");

/* D：还没点过 AI 总结（groups 为空）→ 退回平铺列表 */
run(`
  mailState.error = ""; mailState.groups = [];
  renderMail();
  globalThis.__out3 = document.getElementById("mailList").innerHTML;
`);
ok(sandbox.__out3.includes("mail-row") && !sandbox.__out3.includes("mail-group"), "D1 groups 为空时退回平铺列表");

/* E：askConfirm 的并发语义（裁定 R2）—— 第二问绝不许搭便车拿到第一问的答案 */
run(`
  globalThis.__p1 = askConfirm("TITLE-1", "BODY-1");
  globalThis.__p2 = askConfirm("TITLE-2", "BODY-2");
  globalThis.__titleAfterSecond = document.getElementById("confirmPromptTitle").textContent;
  globalThis.__hidden = document.getElementById("confirmPrompt").hidden;
`);
ok(sandbox.__titleAfterSecond === "TITLE-1", "E1 并发时只开一个弹窗（标题仍是第一问的）");
ok(sandbox.__hidden === false, "E2 弹窗处于打开态");
const p2v = await settle(sandbox.__p2);
ok(p2v === false, `E3 第二问立刻拿到 false，不搭第一问的便车（实得 ${JSON.stringify(p2v)}）`);

/* 把第一问按「确认」关掉：它该拿到 true，且状态归零以便下一次询问 */
run(`
  document.getElementById("btnConfirmYes").onclick();
  globalThis.__hidden2 = document.getElementById("confirmPrompt").hidden;
`);
const p1v = await settle(sandbox.__p1);
ok(p1v === true, `E4 第一问按确认后拿到 true（实得 ${JSON.stringify(p1v)}）`);
ok(sandbox.__hidden2 === true, "E5 确认后弹窗关闭");

/* E6：状态归零 —— 下一问必须能正常开（不能被残留的 promise 永久锁死） */
run(`
  globalThis.__p3 = askConfirm("TITLE-3", "BODY-3");
  globalThis.__hidden3 = document.getElementById("confirmPrompt").hidden;
`);
ok(sandbox.__hidden3 === false, "E6 上一问结束后，新一问能正常打开");
run(`document.getElementById("btnConfirmNo").onclick();`);
ok(await settle(sandbox.__p3) === false, "E7 按取消拿到 false");

/* F：未经校验的模型元素（F1）。`null` 占位曾会让 `e.title` 抛 TypeError，把 renderMail
   变成静默的未处理 rejection（groups 已赋值 ⇒ 之后每次 renderMail 都跟着炸）。
   两条要点：非空兄弟照常渲染；**下标不许重排**（过滤后再 map 会让 data-e 变成过滤后的
   序号，而点击处理器按原始下标取行 —— 按钮显示的与它写下去的会是两条不同的 DDL）。 */
run(`
  mailState.error = ""; mailState.loading = false;
  mailState.groups = [
    { course: "SYNTH-F-COURSE", types: [
      { type: "SYNTH-F-TYPE", items: [
        { mail_index: 0, summary: "SYNTH-F-SUM",
          calendar_events: [null, { title: "SYNTH-F-EV", start: "2026-09-20T09:00" }],
          /* reminders 的 null 必须在**最前**：放末尾时，只把这一路改成 filter-first 的
             变异体全套断言全绿（复审实测，本文件复核过）—— 那就是「绿着的断言下面是坏的」。 */
          reminders: [null, { title: "SYNTH-F-RM", due_date: "2026-09-21T23:59" }] },
      ] },
    ] },
  ];
  renderMail();
  globalThis.__fout = document.getElementById("mailList").innerHTML;
`);
const fout = sandbox.__fout;
const fcount = s => fout.split(s).length - 1;
ok(fcount('data-w="ev"') === 1 && fcount('data-w="rm"') === 1,
   `F1 null 元素不炸渲染，同组非空元素照常出按钮（实得 ev=${fcount('data-w="ev"')} rm=${fcount('data-w="rm"')}）`);
ok(fout.includes("SYNTH-F-EV") && fout.includes("SYNTH-F-RM"), "F2 非空元素照常渲染");
ok(fout.includes('data-w="ev" data-g="0" data-t="0" data-i="0" data-e="1"')
   && fout.includes('data-w="rm" data-g="0" data-t="0" data-i="0" data-e="1"'),
   "F3 两路都被 null 跳过后 data-e 仍是**原始**下标（未重排；reminders 那一路也钉住）");

/* G：F4 的 Esc 分支（回归守卫）。此前 stub 里 `document.addEventListener` 是空实现 ⇒ app.js:270
   注册的 keydown 处理器**从未被执行过**，F4 的正确性全靠推理，而它的失败模式是**静默的**：
   一次 Esc 之后所有写入确认被永久卡死（promise 悬着、confirmResolve 不清）。
   载荷证明（控制者实测，非推断）：`--mutate-no-esc`（删掉 F4 那一行）→ **G2–G6 变红、exit 1**。
   **G1 与 G7 仍绿是对的**：G1 是前提断言、不依赖 F4；G7 断言「弹层关着时不许碰出口」，删掉这一行反而**更**满足它。
   其中 **G2/G3 是该变异的直接命中点**，G4 是同一删除的独立症状（promise 落到 SYNTH-TIMEOUT），**G5/G6 是级联**——「Esc 不落地 ⇒ 下一问被砖化」。**级联红不算独立判别力，故写明在此。** */
const keydowns = docListeners.keydown || [];
const noBtn = () => nodes["btnConfirmNo"];
const pressEscape = () => keydowns.forEach(fn => fn({ key: "Escape", preventDefault(){}, stopPropagation(){} }));
let noCalls = 0;
const wrapNo = () => {                       // askConfirm 每次都重装 onclick，故按之前重新包一层
  const cur = noBtn().onclick;
  noBtn().onclick = (...a) => { noCalls++; return cur.apply(noBtn(), a); };
};
ok(keydowns.length >= 1, `G0 捕获到 document 上的 keydown 处理器（实得 ${keydowns.length} 个）`);
run(`globalThis.__esc1 = askConfirm("SYNTH-ESC-1", "SYNTH-ESC-B1");`);
ok(nodes["confirmPrompt"].hidden === false, "G1 前提：第一问已打开");
noCalls = 0; wrapNo(); pressEscape();
ok(nodes["confirmPrompt"].hidden === true, "G2 弹层开着时按 Esc → 关闭");
ok(noCalls === 1, `G3 Esc 走的是 #btnConfirmNo 的出口（与 ✕/取消/遮罩同门，实测 ${noCalls} 次）`);
ok((await settle(sandbox.__esc1)) === false, "G4 Esc 的语义是**取消**：promise 落地为 false");
/* 反卡死（这条才是要害）：Esc 若不落地，后续每一次写入确认都会拿不到答案 */
run(`globalThis.__esc2 = askConfirm("SYNTH-ESC-2", "SYNTH-ESC-B2");`);
ok(nodes["confirmPrompt"].hidden === false && nodes["confirmPromptTitle"].textContent === "SYNTH-ESC-2",
   "G5 反卡死：Esc 之后再问一次仍能打开（⇒ confirmResolve 已清空，未被残留 promise 砖化）");
run(`document.getElementById("btnConfirmYes").onclick();`);
ok((await settle(sandbox.__esc2)) === true, "G6 反卡死：新一问仍能拿到 true（写入确认没被砖化）");
noCalls = 0; wrapNo(); pressEscape();
ok(noCalls === 0, `G7 弹层关着时 Esc 不触碰 #btnConfirmNo（不许凭空 resolve 一个不存在的 promise，实测 ${noCalls} 次）`);

/* H：元素级点击处理器的**写入路径**（回归守卫）。
   A/B/F 钉的是「按钮**显示**了哪条」—— 没有任何东西钉过「按钮**写下去**哪条」。
   而 `mkNode` 的元素级 `addEventListener(){}` 曾是空函数 ⇒ `$("mailList")` 上的点击
   处理器在整份自检里从未被执行过（实测：把取行改成 `arr[0]`，34 条全绿、exit 0）。
   这条洞与 G 组补的 document 级那条同类，失败模式同样**静默**：点第 2 条的按钮，
   弹层和写入都是第 1 条 —— 写进去的还是用户的真实日历。

   **为什么夹具要 3 条 event、要点两个下标**（Low-1）：只放 2 条、只点 `data-e="1"` 时，
   这段只钉住了「**不是** `arr[0]`」，钉不住「按 `dataset.e` 取行」—— 恒取下标 1、恒取
   最后一条这些**同样会静默错位写入**的变异体全部漏网（实测：`arr[1]` / `arr.at(-1)` /
   `arr[arr.length-1]` 三种全绿 exit 0）。故夹具放 3 条 event（EV0/EV1/EV2），**点 1 与 2
   两个下标**，每次各自断言「弹层显示的是那一条自己」+「写下去的是那一条自己」。
   这样四种常量下标变异体（0 / 1 / at(-1) / length-1）都会被至少一次点击抓住：
   `arr[0]`→第一发错、`arr[1]`→第二发错、`arr.at(-1)`/`arr[arr.length-1]`→第一发就错。
   `--mutate-widx=<表达式>` 就是把这四种在仓内自证一遍用的。
   载荷证明（本次实测）：`--mutate-widx=arr[0]|arr[1]|arr.at(-1)|arr[arr.length-1]`
   四种全部 exit 1（H1/H2 变红）；`--mutate-widx=arr[0]` 即 `--mutate` 里的那一发，
   `--mutate` 的红集仍是 8 条。 */
run(`
  mailState.error = ""; mailState.loading = false;
  mailState.groups = [
    { course: "SYNTH-H-COURSE", types: [
      { type: "SYNTH-H-TYPE", items: [
        { mail_index: 0, summary: "SYNTH-H-SUM",
          calendar_events: [
            { title: "SYNTH-H-EV0", start: "2026-09-30T09:00", end: "2026-09-30T10:00" },
            { title: "SYNTH-H-EV1", start: "2026-10-01T09:00", end: "2026-10-01T10:00" },
            { title: "SYNTH-H-EV2", start: "2026-10-02T09:00", end: "2026-10-02T10:00" }],
          reminders: [
            { title: "SYNTH-H-RM0", due_date: "2026-10-03T23:59" },
            { title: "SYNTH-H-RM1", due_date: "2026-10-04T23:59" }] },
      ] },
    ] },
  ];
  /* 目标日历必须非空：否则处理器在校验那一步就早退了，H 的断言会变成空转。 */
  document.getElementById("selCalendar").value = "SYNTH-CAL";
  document.getElementById("selList").value = "SYNTH-LIST";
  renderMail();
  globalThis.__hout = document.getElementById("mailList").innerHTML;
  globalThis.__hgrpBefore = JSON.stringify(mailState.groups);
`);
/* 自检的 innerHTML 是**字符串**、没有 DOM 解析，所以按钮的 data-* 必须自己从串里解。 */
const hParse = html => [...html.matchAll(/<button[^>]*data-w="(ev|rm)"[^>]*>/g)].map(m2 => {
  const at = k => (m2[0].match(new RegExp(`data-${k}="([^"]*)"`)) || [])[1];
  return { w: at("w"), g: at("g"), t: at("t"), i: at("i"), e: at("e") };
});
const hbtns = hParse(sandbox.__hout);
const hbtn = hbtns.find(b => b.w === "ev" && b.e === "1");
ok(!!hbtn, `H0 渲染串里有 data-e="1" 的日历按钮（实得 ${JSON.stringify(hbtns)}）`);
/* 第二个点击目标（Low-1）：同样从渲染串里解，不硬编码。 */
const hbtn2 = hbtns.find(b => b.w === "ev" && b.e === "2");
ok(!!hbtn2, `H0c 渲染串里有 data-e="2" 的日历按钮（实得 ${JSON.stringify(hbtns)}）`);
const clickers = (elListeners["mailList"] || {}).click || [];
ok(clickers.length === 1, `H0b 捕获到 #mailList 上的点击处理器（实得 ${clickers.length} 个）`);

/* 点击目标**一律取自渲染串解出来的那个按钮**（Low-2）：`g/t/i/e` 全部来自那一行按钮
   自己的 data-*，不再硬编码一个 `{g:"0",t:"0",i:"0",e:"1"}` —— 渲染串里的下标与夹具若
   不自洽（例如 g/t/i 串号、或 e 不是它自己），处理器会在 `if(!it)` / `if(!row)` 早退，
   H1b 立刻变红。 */
const hstub = b => ({ dataset: { ...b }, disabled: false, id: "SYNTH-H-BTN-e" + b.e });

/* 两发点击，各自断言「弹层显示那一条自己」+「写下去那一条自己」。
   两发之间的状态复位（实现说明）：**换元素**，而不是把同一个按钮重新启用 ——
   app.js 的防重入（裁定 R5）在写入成功后**保持该按钮置灰**（H3 观测到的就是这件事），
   真实 DOM 里每一行 DDL 本来就是各自的 `<button>`，第二发点的是另一个从未被置灰的元素。
   `mailState` 不参与复位：处理器不写它（H5 钉住这一点），故第二发的期望与第一发同源。 */
const hRounds = [
  { btn: hbtn,  e: "1", title: "SYNTH-H-EV1" },
  { btn: hbtn2, e: "2", title: "SYNTH-H-EV2" },
];
const hObs = [];
for (const r of hRounds) {
  const before = fetchLog.length;
  const stub = hstub(r.btn);
  /* 派发**不要 await**：处理器要先等确认。askConfirm 在挂起前就写好了弹层文本，
     故这里立刻就能读到；放行确认后再 await 处理器返回的 promise（不靠 setTimeout 猜时序）。 */
  const p = clickers[0]({ target: { closest: sel => (sel === "button[data-w]" ? stub : null) } });
  const opened = nodes["confirmPrompt"].hidden === false;
  const shown  = nodes["confirmPromptBody"].textContent;
  if (opened) nodes["btnConfirmYes"].onclick();     // 早退分支下不点，免得 resolve 一个不存在的 promise
  await settle(p);
  hObs.push({ ...r, stub, opened, shown,
    posts: fetchLog.slice(before).filter(f => f.url === "/api/add_calendar_event"),
    writes: fetchLog.length - before });
}
ok(hObs.every(o => o.shown.includes(o.title)),
   `H1 每次点击后，确认弹层显示的都是**被点那一条自己**的标题（实得 ${JSON.stringify(hObs.map(o => `e=${o.e}:${o.shown}`))}）`);
ok(hObs.every(o => o.opened),
   `H1b 确认弹层已打开（处理器没有在早退分支里空转；两发都算，实得 ${JSON.stringify(hObs.map(o => o.opened))}）`);
ok(hObs.every(o => o.posts.length === 1 && o.posts[0].body.title === o.title),
   `H2 写下去的也是**被点那一条自己**的标题（实得 ${JSON.stringify(hObs.map(o => o.posts.map(f => f.body.title)))}）`);
ok(hObs.every(o => o.writes === 1),
   `H2b 每发只发一次写入请求，且其余 URL 一律没碰（实测每发新增 ${JSON.stringify(hObs.map(o => o.writes))} 次）`);
/* H3：R5 的观测 —— 写成之后那一发按钮保持置灰（故第二发必须换元素，见上面注释）。 */
ok(hObs[0] && hObs[0].stub.disabled === true,
   `H3 写入成功后它自己的按钮保持置灰（R5：写成 → 保持置灰；实得 disabled=${JSON.stringify(hObs[0] && hObs[0].stub.disabled)}）`);
/* H5：两发之间 `mailState` 逐字未变 —— 复位不需要动状态，第二发的期望与第一发同源。 */
run(`globalThis.__hgrpAfter = JSON.stringify(mailState.groups);`);
ok(sandbox.__hgrpBefore === sandbox.__hgrpAfter,
   "H5 两发点击之间 mailState.groups 逐字未变（写入处理器不污染状态）");

console.log(fails ? `\n✗ ${fails} 条未通过（共 ${total} 条断言）` : `\n✓ 全部通过（共 ${total} 条断言）`);
process.exit(fails ? 1 : 0);
