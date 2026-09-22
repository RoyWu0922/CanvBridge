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
 * `mailItemHtml` / `$("mailList")` 的点击写入路径 / `mailState` 结构之后，
 * 以及改动**详情弹层**那条链之后（`mailRowAttr` / `mailRowTarget` / `openMailDetail` /
 * `renderMailDetail` / `mailLinkify` / `fmtBytes` / `mailAttsHtml` —— K/L/M 段守它们）。
 *
 * 零出网：`fetch` 是桩，只放行三条端点（两条写入端点 + `/api/mail/detail`）并记账，
 * 其余 URL 一律 throw。`/api/mail/detail` 的响应**按用例可控**（成功 / 失败 / 挂起，
 * 见 `detailStub`）—— 详情失败与「响应在途」这两种进入方式要能被驱动到。
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
 *     --mutate-mail-relogin[=SITE]  邮件失败路径改回「发 AIMS 手动登录问询」
 *                          （SITE ∈ init / refresh / summarize，默认 all 三处一起）
 *     --mutate-kmap        行上的 data-m 从 mail_index 改成条目序号 ii（渲染侧错位）
 *     --mutate-kread       取行不再读 data-m、恒取第 0 封（读取侧错位）
 *     --mutate-kkey        注释掉 #mailList keydown 里那行「行内交互元素不劫持」守卫
 *                          （缺陷 1 复发：键盘激活写入按钮被吞）
 *     --mutate-kguard      去掉 mailRowTarget 的 `/^\d+$/` 守卫（空 data-m 被当成第 0 封）
 *     --mutate-kdid        去掉 renderMailDetail 早退里的 `detailId !== id` 那一项
 *                          （详情缓存不再校验属于哪一封）
 *     --mutate             = r2 + meta + map + widx **四个一起上**，红 8 条：
 *                          B1 B2 B4 B5 C2 E3 H1 H2
 *   新增的 K/L/M 段（详情弹层，24+9+5 条）在 --mutate 下**必须全绿** —— 那四个变异
 *   都不碰详情路径（Task 11 实测：--mutate 下红的是上面那 8 条，K/L/M 一条不红）。
 *   若 --mutate 的红集变了（多了或少了），先查清是谁干的，别改红集去迁就。
 *   `--mutate-rm-filter`、`--mutate-no-esc`、`--mutate-mail-relogin` 以及上面那五个
 *   `--mutate-k*` **都不在** `--mutate` 里（新开关一律不写 `|| MUTATE` —— 一写就会
 *   把 K/L/M 段的红混进那 8 条里），必须各自单独跑。实测红集（由 Task 11 逐条跑出来，
 *   原命令与原始输出见 `task-11-report.md`）：
 *     --mutate-kmap   → K0
 *     --mutate-kread  → K2 K3 K4 K5 K6 K7 K8 K9 K13 K16 ＋ L1 L6 L8
 *                       （brief 的 Step 6 只写了 K2 K3 K4 K13 K16，**实测是它的超集**。
 *                        多出来的 K5–K9 是**同一错位的级联**，不是断言在测别的东西：
 *                        恒取第 0 封 ⇒ 两发点击打开的是**同一封** ⇒ `mailDetailState.id`
 *                        不再能区分它们 ⇒ 先点那一发的**晚到响应不再被丢弃**、照画，
 *                        弹层里于是留下第 0 封的正文与附件（A-BODY / 没有附件），
 *                        而那正是 `__kBody`/`__kAtts` 五条断言的判别对象 ⇒ K5–K9 跟着红。
 *                        K10/K12 仍绿也是对的：K10 对「没有附件」平凡成立、
 *                        K12 只断言 index（brief 自己也这么写）。L1/L6/L8 同理，
 *                        它们都是读取侧断言，本该跟着红。）
 *     --mutate-kkey   → L7
 *     --mutate-kguard → L8
 *     --mutate-kdid   → M3 M4（对照组 M1 必须仍绿）
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
/* `/api/mail/detail` 的响应**按用例可控**（Task 11 附录一·2）：默认成功（回一份空详情），
   用例可以换成「后端失败」或「挂起」两种进入方式 —— Task 10 的缓存身份守卫要在这两种
   进入方式上各自被验一遍（`openMailDetail` 的失败分支从不写缓存、在途期间也还没写，
   两种情况下缓存里留的都是**上一封**）。
   挂起态返回一个**永不自动落地**的 promise，resolve 句柄压进 `pending` 留给用例；
   用例不落地它，也不会吊死整份自检 —— 末尾 `process.exit` 不等待任何未决 promise
   （它是纯 promise，不是定时器/handle，不会吊住事件循环）。**其余 URL 仍然 throw**，
   「自检不该发起别的 fetch」这条口径一个字没放宽。 */
const detailStub = {
  mode: "ok",                                  // "ok" | "fail" | "pending"
  payload: { ok: true, detail: { id: "", body: "", attachments: [] } },
  pending: [],
  reset(){
    this.mode = "ok";
    this.payload = { ok: true, detail: { id: "", body: "", attachments: [] } };
    this.pending = [];
  },
  reply(){
    if (this.mode === "fail")
      return Promise.resolve({ ok: true, status: 200,
        json: async () => ({ ok: false, error: "SYNTH-DETAIL-ERR" }) });
    if (this.mode === "pending") return new Promise(res => { this.pending.push(res); });
    const payload = this.payload;
    return Promise.resolve({ ok: true, status: 200, json: async () => payload });
  },
};
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
    if (u !== "/api/add_calendar_event" && u !== "/api/add_reminder" && u !== "/api/mail/detail")
      throw new Error("自检不该发起 fetch：" + u);
    fetchLog.push({ url: u, body: JSON.parse((opts && opts.body) || "{}") });
    if (u === "/api/mail/detail") return detailStub.reply();
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  },
  /* `location` 与 `URL` 只被 app.js 那条 **document 级 `a[href]` 捕获委托**读
     （`new URL(href, location.href)` 与 `location.origin`；另一处同形的 `d.baseURI`
     变体在 app.js 的另一个点击委托里，本自检不驱动它）。`location` 与 `URL`
     在 `vm` 沙箱里**都不存在**（`URL` 不是 JS 内建，是宿主提供的全局）⇒ 那条委托
     一进去就抛 ReferenceError、被自己的 `catch` 吞掉后静默 `return`，于是「详情正文
     里的外链有没有被**既有**委托接住」根本无法观测 —— 探针只会看到「什么都没发生」
     的假绿。补上这两个名字，L2 才真的驱动到那段既有代码。两者都不在 frontend 的
     加载路径上、也没有别的读取点（已按符号名核过）。 */
  location: { href: "http://localhost/", origin: "http://localhost" },
  URL,
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
/* `--mutate-mail-relogin[=SITE]`：把邮件失败路径的 `offerManualLogin` 加回去（F4）。
   逐 SITE 跑才能证明 I 段每条断言的判别对象是哪一处：只变异一处时，红的是它自己那一条，
   另两处的断言必须仍绿 —— 否则「断言红」可能只是被别处的变异带红的。
   三个锚点互不重叠，故 `all` 也只是三次替换的合取。 */
const RELOGIN_ARG = process.argv.find(a => a === "--mutate-mail-relogin" || a.startsWith("--mutate-mail-relogin="));
const RELOGIN_SITE = RELOGIN_ARG && RELOGIN_ARG.includes("=")
  ? RELOGIN_ARG.slice(RELOGIN_ARG.indexOf("=") + 1) : "all";
const MUTATE_MAIL_RELOGIN = !!RELOGIN_ARG;
if (MUTATE_MAIL_RELOGIN && !["all", "init", "refresh", "summarize"].includes(RELOGIN_SITE))
  throw new Error(`--mutate-mail-relogin 只认 all/init/refresh/summarize，实得 ${JSON.stringify(RELOGIN_SITE)}`);
const relogin = site => MUTATE_MAIL_RELOGIN && (RELOGIN_SITE === "all" || RELOGIN_SITE === site);

/* Task 11 新增的五个开关：**一律不写 `|| MUTATE`** —— 一写就会把 K/L/M 段的红混进
   `--mutate` 那 8 条里（那份红集是钉死的，多一条少一条都要先查清是谁干的）。
   前两个（kmap/kread）证明 K 段不是恒真；后三个（kkey/kguard/kdid）是附录三要的
   「另两条交互路径 + 缓存身份」那三道门禁的变异对照，都必须能**双向**跑通。 */
const MUTATE_KMAP   = process.argv.includes("--mutate-kmap");
const MUTATE_KREAD  = process.argv.includes("--mutate-kread");
const MUTATE_KKEY   = process.argv.includes("--mutate-kkey");
const MUTATE_KGUARD = process.argv.includes("--mutate-kguard");
const MUTATE_KDID   = process.argv.includes("--mutate-kdid");

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
    /* `--mutate-kmap`：行上的 `data-m` 从「`mail_index`」改成「条目序号 `ii`」
       —— 与 `--mutate-map` 同一个错位形态，只**反方向**（渲染侧 vs 读取侧）。
       `ii` 在 `mailItemHtml(it, m, gi, ti, ii)` 的作用域内，故这条替换是合法 JS。 */
    if (MUTATE_KMAP) {
      src = mutate(src, "mailRowAttr(it.mail_index)", "mailRowAttr(ii)", "kmap");
    }
    /* `--mutate-kread`：取行不再读 `data-m`，恒取第 0 封 —— 证明 K2/K3/K4 不是恒真。 */
    if (MUTATE_KREAD) {
      src = mutate(src, "const raw = el.dataset.m;", 'const raw = "0";', "kread");
    }
    /* `--mutate-kkey`：注释掉 #mailList keydown 里那行守卫 —— 缺陷 1 复发：键盘激活
       行内的写入按钮会被本监听器劫持（吞掉合成 click、同时开详情）。L7 必须变红。 */
    if (MUTATE_KKEY) {
      src = mutate(src,
        '  if(e.target && e.target.closest && e.target.closest("button, a, input, select, textarea")) return;\n',
        "  /* --mutate-kkey：这行守卫被注释掉（缺陷 1 复发） */\n", "kkey");
    }
    /* `--mutate-kguard`：去掉 `mailRowTarget` 的「必须是纯数字」守卫 —— 空 `data-m` 会被
       `Number("")` 静默当成第 0 封。L8 必须变红。
       锚点带 `return null;` 后缀：`#mailDetailAtts` 那个附件处理器里有一条同形的
       守卫（`... ) return;`），不带后缀会命中两次（`mutate()` 会直接抛错）。 */
    if (MUTATE_KGUARD) {
      src = mutate(src, 'if(!/^\\d+$/.test(raw || "")) return null;', "", "kguard");
    }
    /* `--mutate-kdid`：去掉 `renderMailDetail` 早退里的 `detailId !== id` 那一项，
       其余三项（`!d` / `!el` / `el.hidden`）保留 —— Task 10 那个缺陷复发：缓存属于
       上一封时，无参入口照画（把上一封的正文挂在现打开这一封的标题下）。 */
    if (MUTATE_KDID) {
      src = mutate(src,
        "if(!d || !el || el.hidden || mailDetailState.detailId !== mailDetailState.id) return;",
        "if(!d || !el || el.hidden) return;", "kdid");
    }
    /* F4 回归：把邮件失败路径的「发 AIMS 手动登录问询」加回去。三处各一个锚点，
       逐 SITE 跑 ⇒ 红的必须是 I 段里对应那一条。 */
    if (relogin("init")) {
      /* 变异体把 promise **返回**出去（修复前是浮动续延）—— 只为让探针能确定地等到它
         跑完；它复现的仍是同一件事：取信失败去开 AIMS 登录窗。 */
      src = mutate(src,
        "  return loadMail();\n}",
        "  return loadMail().then(ok => { if(!ok) return offerManualLogin(mailState.error); });\n}",
        "mail-relogin/init");
    }
    if (relogin("refresh")) {
      src = mutate(src,
        `  await withBusy(t("mail.refresh"), $("btnMailRefresh"), loadMail);\n}`,
        `  let err = "";\n  await withBusy(t("mail.refresh"), $("btnMailRefresh"), async () => {\n`
        + `    if(!await loadMail()) err = mailState.error;\n  });\n  if(err) offerManualLogin(err);\n}`,
        "mail-relogin/refresh");
    }
    if (relogin("summarize")) {
      src = mutate(src,
        "的问题，指去重登 AIMS 是另一种「指错方向」（同 memory 里 AIMS 那条坑）。 */\n}",
        "的问题，指去重登 AIMS 是另一种「指错方向」（同 memory 里 AIMS 那条坑）。 */\n"
        + "  if(failReason === \"mail\") await offerManualLogin(failMsg);\n}",
        "mail-relogin/summarize");
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

/* 捕获到的处理器**一律经这里调用**（K/L 段）。缘由是实测过的失败形态：处理器没被捕获到时
   `list[0](…)` 抛的是**未捕获的 TypeError** ⇒ 没有汇总行，其后那截断言一条都不执行。
   （实测三个调用点各自截掉的条数：附件点击 26、列表 keydown 22、L 段那条 8 —— 越靠前截得越多。）
   那不是「红」，是**截断** —— 只看 stdout 上断言计数的用法读到的会是「跑完了」。
   守卫成**干净的红**：不调用，让下游断言各自变红（调用点旁边那条「捕获到…」的计数断言
   是正例锚，会指出是谁没被捕获到）。处理器若是 async，返回值照旧交回给 `await settle(…)`。 */
const callCaptured = (list, ev) => (list.length ? list[0](ev) : undefined);

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
/* 按 <div class="mail-item" …> 切开（**属性容忍**）：Task 9 之后这一行会带上
   `data-m`，写死 `<div class="mail-item">` 会在功能完全正常时变红。 */
const items = out.split(/<div class="mail-item"[^>]*>/).slice(1);
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

/* I：邮件失败的**补救方向**（F4 回归守卫）。
   邮件取信走 Gmail IMAP，**没有浏览器、不依赖 AIMS 登录态**；spec §11 要求认证失败时
   「文案直接指向设置页重填；不重试」。而修复前三条邮件失败路径都去 `offerManualLogin()`
   —— 弹「要打开 Chrome 窗口手动登录吗」→ `banweb/open_login`，把用户指向一个对邮件
   无效的动作，还开出一个本设计刻意消灭的 AIMS 窗口。此前没有任何断言守着这件事。

   三条路径各驱动一遍（首次进页 / 刷新 / AI 总结的取信阶段），每条都查两件事：
     **正的**：后端那条 error 被呈现到用户看得见的地方（文案直达才是 spec 要的补救）；
     **负的**：`#loginPrompt` 没被打开（没发手动登录问询）。
   然后**再驱动 AIMS 那条路径**，断言它**仍然**发问询 —— `offerManualLogin` 被两条线
   共用，只证明「邮件侧不问了」是不够的：把共用出口整个拆掉同样能让上面全绿。
   最后一并断言整段探针期间没有任何 `banweb/open_login`。

   **判据不是「没抛错」**：`offerManualLogin` 会先开弹层再 fetch，故 `loginPrompt.hidden`
   是最直接的观测点；`banweb/open_login` 在下面的桩里**故意放行并记账**（不 throw），
   这样「它没被调用」由账本断言直接给出，而不是由某个异常间接暗示。
   载荷证明见文件头 `--mutate-mail-relogin[=SITE]`。 */
const realFetch = sandbox.fetch;
const ROUTED = {
  "/api/mail/list":         { ok: false, error: "SYNTH-MAIL-AUTH-ERR" },
  "/api/mail/summarize":    { ok: false, reason: "mail", error: "SYNTH-MAIL-AUTH-ERR" },
  "/api/banweb/auto_login": { ok: false, error: "SYNTH-AIMS-AUTO-ERR" },
  "/api/banweb/status":     { ok: false, error: "SYNTH-AIMS-STATUS-ERR" },
  "/api/banweb/open_login": { ok: true },     // 放行 + 记账（见上）
};
const iLogStart = fetchLog.length;
sandbox.fetch = (url, opts) => {
  const u = String(url);
  fetchLog.push({ url: u, body: JSON.parse((opts && opts.body) || "{}") });
  if (!(u in ROUTED)) throw new Error("自检不该发起 fetch：" + u);
  return { ok: true, status: 200, json: async () => ROUTED[u] };
};
const iLog = () => fetchLog.slice(iLogStart).map(f => f.url);

/* 每发探针之前把弹层状态清干净。**这不是洁癖**：`offerManualLogin` 会一直挂到用户作答，
   一发探针若把弹层留在打开态，`confirmManualLogin` 的并发语义（同一 promise）会让下一发
   复用它 —— 那时下一发的负例断言就是被**上一发**带红的，逐 SITE 跑时分不清是谁干的
   （`--mutate-mail-relogin=refresh` 会连带把 I7 也弄红）。故每发结束都按「取消」落地。 */
const iDismiss = () => {
  if (nodes["btnLoginPromptNo"] && nodes["btnLoginPromptNo"].onclick) nodes["btnLoginPromptNo"].onclick();
};

/* 前提断言：`mailState.error` 必须真的是**后端桩那条**（而不是 `status.backend_fail` 之类）。
   少了它，桩没接上时下面几条会因为「压根没走到失败路径」而全绿。 */
run(`mailState.mails = []; mailState.error = ""; mailState.groups = [];`);
await settle(run(`globalThis.__iInit = initMailTab();`));
run(`globalThis.__iErr1 = mailState.error;`);
ok(sandbox.__iErr1 === "SYNTH-MAIL-AUTH-ERR",
   `I1 前提：首次进页取信失败确实走了失败路径（实得 ${JSON.stringify(sandbox.__iErr1)}）`);
ok(nodes["mailList"].innerHTML.includes("SYNTH-MAIL-AUTH-ERR"),
   "I2 首次进页：后端那条文案呈现给了用户（spec §11 文案直达）");
ok(nodes["loginPrompt"].hidden === true, "I3 首次进页取信失败**没有**发 AIMS 手动登录问询");
iDismiss();

run(`mailState.mails = []; mailState.error = "";`);
await settle(run(`globalThis.__iRefresh = refreshMail();`));
run(`globalThis.__iErr2 = mailState.error;`);
ok(sandbox.__iErr2 === "SYNTH-MAIL-AUTH-ERR",
   `I4 前提：刷新失败确实走了失败路径（实得 ${JSON.stringify(sandbox.__iErr2)}）`);
ok(nodes["loginPrompt"].hidden === true, "I5 刷新失败**没有**发 AIMS 手动登录问询");
iDismiss();

run(`
  mailState.error = ""; mailState.groups = [];
  mailState.mails = [{ index: 0, subject: "SYNTH-MAIL-S", sender: "s0@example.invalid",
                       received: "2026-09-01T00:00:00Z" }];
`);
await settle(run(`globalThis.__iSum = mailSummarize();`));
ok(nodes["status"].textContent.includes("SYNTH-MAIL-AUTH-ERR"),
   `I6 AI 总结的取信阶段失败也把后端文案呈现给用户（实得 ${JSON.stringify(nodes["status"].textContent)}）`);
ok(nodes["loginPrompt"].hidden === true, "I7 AI 总结的取信阶段失败**没有**发 AIMS 手动登录问询");
iDismiss();

/* AIMS 侧：**仍然**发问询（同一条 `offerManualLogin`，改一处必须证明另一处没坏）。
   走 `#btnBanwebLogin` 那条：它不经过 runAutoLogin 的成功分支，故不会碰 setInterval
   （本沙箱没提供 setInterval —— 加了反而会把一条与本题无关的依赖引进来）。 */
await settle(run(`globalThis.__iAims = document.getElementById("btnBanwebLogin").onclick();`));
ok(nodes["loginPrompt"].hidden === false,
   "I8 AIMS 失败路径**仍然**发手动登录问询（共用出口没被一起拆掉）");
iDismiss();

ok(!iLog().includes("/api/banweb/open_login"),
   `I9 整段探针期间没有任何 banweb/open_login（实得 ${JSON.stringify(iLog())}）`);
sandbox.fetch = realFetch;

/* J：主题格在 subject 为空时回退到哪条文案（终审 M-4 的常驻门禁）。
   缺陷形态是**标签与它标注的值不符**：主题格原先回退到 `mail.no_body`（「（无正文）」）
   —— 正文的回退文案被贴在**主题**格上。两处槽位（平铺列表的 mailRowHtml、分组列表的
   mailItemHtml）**逐字同错**，故两处都钉：只钉一处的话，另一处改回旧文案不会被任何
   断言发现 —— 「同一处错有两份，改一处不等于改一类」正是这一轮要防的形态。

   判据**正负成对**，缺一不可：
     · 正半：新文案必须在场。**这是承重的一半** —— 把槽位改回 `t("mail.no_body")`
       （或改回旧文案字面量）时，红的就是它；
     · 负半：旧文案必须不在场。正半只证明新文案出现，**不排除旧文案同时出现**
       （两处文案拼进同一格、或有人把旧键的值重新加回字典再用上）。
   旧文案只能写**字面量**：`mail.no_body` 已随「失去全部消费者」一并从字典删除
   （改回旧键会渲染成裸键名，那种退化由 tools/check_i18n_keys.mjs 的悬空引用检查另管）。

   本段在 `sandbox.fetch` 已还原之后跑，只驱动渲染、不发任何请求（仍是零出网）。 */
const J_SUBJECT = "（无主题）";    // = mail.no_subject 的 zh 值，由下面 J0 钉住
const J_BODY    = "（无正文）";    // = 已删除的 mail.no_body 的 zh 值（旧文案，见上）
run(`
  globalThis.__jT = t("mail.no_subject");
  mailState.loading = false; mailState.error = "";
  mailState.mails = [{ index: 0, subject: "", sender: "s0@example.invalid",
                       received: "2026-09-01T00:00:00Z" }];
  mailState.groups = [];                       // groups 空 → 平铺列表（mailRowHtml 那条槽位）
  renderMail();
  globalThis.__jRow = document.getElementById("mailList").innerHTML;
  mailState.groups = [{ course: "SYNTH-COURSE-J", types: [
    { type: "SYNTH-TYPE-J", items: [{ mail_index: 0, summary: "SYNTH-SUM-J" }] }] }];
  renderMail();                                // groups 非空 → 分组列表（mailItemHtml 那条槽位）
  globalThis.__jItem = document.getElementById("mailList").innerHTML;
`);
/* 前提：字典里 `t("mail.no_subject")` 必须就是下面那个字面量。少了它，键值被改过之后
   下面两条会以「字面量过期」的形态莫名其妙地红（键被删掉时 t() 原样返回键名，也会红）。 */
ok(sandbox.__jT === J_SUBJECT,
   `J0 前提：t("mail.no_subject") == ${JSON.stringify(J_SUBJECT)}（实得 ${JSON.stringify(sandbox.__jT)}）`);
ok(sandbox.__jRow.includes(J_SUBJECT) && !sandbox.__jRow.includes(J_BODY),
   `J1 平铺列表（mailRowHtml）：subject 为空时主题格是「${J_SUBJECT}」、且不含「${J_BODY}」（实得 ${JSON.stringify(sandbox.__jRow)}）`);
ok(sandbox.__jItem.includes(J_SUBJECT) && !sandbox.__jItem.includes(J_BODY),
   `J2 分组列表（mailItemHtml）：subject 为空时主题格是「${J_SUBJECT}」、且不含「${J_BODY}」（实得 ${JSON.stringify(sandbox.__jItem)}）`);

/* K：详情弹层 —— 打开的是哪一封、正文怎么变成 HTML、附件按钮带回哪个 index、
   迟到响应不许顶掉后打开的那一封。这三条**都不在** A–J 的覆盖里，而前两条的失败模式
   是**静默**的：① B 的标题配 A 的正文；② 下下来的不是那一个附件。
   与 I 段同款：本段自带 `sandbox.fetch` 桩、跑完还原，其余 URL 一律 throw（仍是零出网）。
   全部夹具为合成串（`SYNTH-*` / `example.invalid`），不含任何真实邮件内容。
   载荷证明（**实测于 Task 11**，原始命令与输出见 `task-11-report.md`）：
     · `--mutate-kmap`（渲染侧：`data-m` 取自条目序号而非 `mail_index`）→ **K0 红**。
       K1–K4 仍绿是**对的**：`kClickRow` 喂的是**写死的** `"0"`/`"1"`，
       不读 DOM，故渲染侧的错位碰不到它 —— 这正是「渲染侧」这条变异的作用边界。
     · `--mutate-kread`（读取侧：取行恒取第 0 封）→ **K2 K3 K4 K5 K6 K7 K8 K9 K13 K16 红**
       （另加 L1/L6/L8：它们也是读取侧断言，本该跟着红）。K2/K3/K4 是直接命中（拿错邮件）；
       **K13 与 K16 是同一错位的独立症状**：下载按钮与 Enter 都按
       `mailDetailState.id`/`mailRowTarget` 取那一封，取错之后它们跟着错；
       **K5–K9 是级联**（两发点击打开同一封 ⇒ 晚到响应不再被丢弃、照画，弹层里留下的是
       第 0 封的正文与附件）—— 级联红不算独立判别力，故写明在此。
       **K10/K12 仍绿也是对的** —— K10 对「没有附件」平凡成立、K12 只断言 index 不看 id。 */
const kBaseFetch = sandbox.fetch;
const kLog = [];
const kPending = [];
const K_DETAIL = {
  "SYNTH-K-ID-0": { ok: true, detail: { id: "SYNTH-K-ID-0", body: "A-BODY", attachments: [] } },
  "SYNTH-K-ID-1": { ok: true, detail: { id: "SYNTH-K-ID-1",
    body: 'B-BODY 见 https://example.invalid/a. <img src=x onerror=SYNTH-XSS> javascript:SYNTH-ALERT',
    attachments: [{ index: 0, name: "SYNTH-ATT-0.pdf", size: 2048 },
                  { index: 1, name: "SYNTH-ATT-1.bin", size: null }] } },
};
sandbox.fetch = (url, opts) => {
  const u = String(url);
  kLog.push({ url: u, body: JSON.parse((opts && opts.body) || "{}") });
  /* `/api/mail/detail` 的响应**手动落地**：这一段的要点之一就是「先点的晚回」，
     自动 resolve 的桩造不出那个顺序。 */
  if (u === "/api/mail/detail") return new Promise(res => kPending.push(res));
  if (u === "/api/mail/attachment")
    return Promise.resolve({ ok: true, status: 200,
      json: async () => ({ ok: true,
        path: "/tmp/SYNTH-DIR/学校邮件/2026-09-17_SYNTH-ATT-1.bin" }) });
  throw new Error("自检不该发起 fetch：" + u);
};
const kAnswer = (i, payload) => kPending[i]({ ok: true, status: 200, json: async () => payload });
/* 行的点击**一律过 `#mailList` 那个唯一的 click 处理器**（H0b 钉着它只有 1 个），
   不在探针里直接调 openMailDetail —— 那样就绕过了「点行能不能打开」这条契约。 */
const kClickRow = m => clickers[0]({ target: {
  closest: sel => (sel === "[data-m]" ? { dataset: { m: String(m) } } : null) } });

run(`
  mailState.loading = false; mailState.error = "";
  mailState.mails = [
    { id: "SYNTH-K-ID-0", subject: "SYNTH-K-SUBJ-0", sender: "s0@example.invalid", received: "2026-09-01T00:00:00Z" },
    { id: "SYNTH-K-ID-1", subject: "SYNTH-K-SUBJ-1", sender: "s1@example.invalid", received: "2026-09-02T00:00:00Z" },
  ];
  mailState.groups = [{ course: "SYNTH-K-COURSE", types: [
    { type: "SYNTH-K-TYPE", items: [{ mail_index: 1, summary: "SYNTH-K-SUM" }] }] }];
  renderMail();
  globalThis.__kList = document.getElementById("mailList").innerHTML;
`);
/* 前提断言（正例锚）：`data-m` 必须取自 `mail_index`（=1），不是条目下标（=0）。
   少了它，下面凡是用 `data-m` 取行的地方都会因为「属性压根不在」而红得莫名其妙。 */
ok(sandbox.__kList.includes('data-m="1"') && !sandbox.__kList.includes('data-m="0"'),
   `K0 前提：分组条目的 data-m 取自 mail_index（实得 ${JSON.stringify(sandbox.__kList.slice(0, 300))}）`);

const kOpenA = kClickRow(0);           // 先点第 0 封（响应挂起）
const kOpenB = kClickRow(1);           // 再点第 1 封（响应挂起）
kAnswer(1, K_DETAIL["SYNTH-K-ID-1"]);  // 后点的**先**回
await settle(kOpenB);
kAnswer(0, K_DETAIL["SYNTH-K-ID-0"]);  // 先点的**晚**回 —— 不许顶掉上面那一封
await settle(kOpenA);
run(`globalThis.__kTitle = document.getElementById("mailDetailTitle").textContent;
     globalThis.__kBody  = document.getElementById("mailDetailBody").innerHTML;
     globalThis.__kAtts  = document.getElementById("mailDetailAtts").innerHTML;`);
const kDetailLog = kLog.filter(f => f.url === "/api/mail/detail");
ok(kDetailLog.length === 2, `K1 两次点击各发一次 /api/mail/detail（实得 ${kDetailLog.length} 次）`);
ok(kDetailLog[0].body.id === "SYNTH-K-ID-0" && kDetailLog[1].body.id === "SYNTH-K-ID-1",
   `K2 取的是点中那一封自己的 id（实得 ${JSON.stringify(kDetailLog.map(f => f.body.id))}）`);
ok(sandbox.__kTitle === "SYNTH-K-SUBJ-1",
   `K3 弹层标题是后点开的那一封（实得 ${JSON.stringify(sandbox.__kTitle)}）`);
ok(sandbox.__kBody.includes("B-BODY") && !sandbox.__kBody.includes("A-BODY"),
   "K4 迟到的那一封没有顶掉后打开的这一封（B 的正文在场、A 的不在场）");
ok(sandbox.__kBody.includes("&lt;img src=x onerror=SYNTH-XSS&gt;") && !sandbox.__kBody.includes("<img"),
   "K5 正文里的标签被转义成文本，不成为 DOM");
ok(sandbox.__kBody.includes('<a href="https://example.invalid/a" rel="noreferrer">https://example.invalid/a</a>'),
   `K6 https:// 成为链接，且句末的句号没被吃进 href（实得 ${JSON.stringify(sandbox.__kBody)}）`);
ok(!/href="javascript:/i.test(sandbox.__kBody) && sandbox.__kBody.includes("javascript:SYNTH-ALERT"),
   "K7 javascript: 不成为链接（原样当正文）");
ok(sandbox.__kAtts.includes('data-att="0"') && sandbox.__kAtts.includes('data-att="1"'),
   "K8 附件按钮各自带自己的 index");
ok(sandbox.__kAtts.includes("SYNTH-ATT-0.pdf") && sandbox.__kAtts.includes("2.0 KB"),
   `K9 附件名与人类可读大小（实得 ${JSON.stringify(sandbox.__kAtts)}）`);
ok(!sandbox.__kAtts.includes("0 B"), "K10 size 为 null 时留白，绝不显示「0 B」");

/* 附件下载：**点中的那个按钮**决定 index。 */
const kAttClickers = (elListeners["mailDetailAtts"] || {}).click || [];
ok(kAttClickers.length === 1, `K11 捕获到 #mailDetailAtts 上的点击处理器（实得 ${kAttClickers.length} 个）`);
const kAttStub = att => ({ dataset: { att }, disabled: false });
await settle(callCaptured(kAttClickers, { target: {
  closest: sel => (sel === "button[data-att]" ? kAttStub("1") : null) } }));
const kAttLog = kLog.filter(f => f.url === "/api/mail/attachment");
ok(kAttLog.length === 1 && kAttLog[0].body.index === 1,
   `K12 下载的是按钮自己那一个 index（实得 ${JSON.stringify(kAttLog.map(f => f.body))}）`);
ok(kAttLog.length === 1 && kAttLog[0].body.id === "SYNTH-K-ID-1"
   && kAttLog[0].body.download_dir === sandbox.downloadDir(),
   "K13 下载请求带上那一封的 id 与设置页的下载目录");
ok(nodes["status"].textContent.includes("SYNTH-DIR"),
   `K14 成功路径把落盘路径告诉用户（实得 ${JSON.stringify(nodes["status"].textContent)}）`);

/* 键盘可达（spec §6）。判据分两半，缺一不可：
   正的 —— Enter 也能打开详情；负的 —— **不是行上的按键不许吞**（无条件 preventDefault
   会让邮件页没法用空格滚动）。 */
const kKeydowns = (elListeners["mailList"] || {}).keydown || [];
const kKeyReady = kKeydowns.length === 1;
ok(kKeyReady, `K15 捕获到 #mailList 上的 keydown 处理器（实得 ${kKeydowns.length} 个）`);
const kBeforeKey = kLog.length;
let kPrevented = 0;
callCaptured(kKeydowns, { key: "Enter", preventDefault(){ kPrevented++; },
               target: { closest: sel => (sel === "[data-m]" ? { dataset: { m: "1" } } : null) } });
ok(kLog.length === kBeforeKey + 1 && kLog[kLog.length - 1].url === "/api/mail/detail"
   && kLog[kLog.length - 1].body.id === "SYNTH-K-ID-1",
   "K16 Enter 也能打开详情（键盘可达）");
ok(kPrevented === 1, "K17 命中行时 preventDefault（Space 不滚页面）");
kPrevented = 0;
callCaptured(kKeydowns, { key: "Enter", preventDefault(){ kPrevented++; }, target: { closest: () => null } });
/* `kKeyReady &&`：处理器没捕获到时 `kPrevented` 恒为 0 ⇒ 这条会**空断言变绿**
   （K16/K17 会红，但那是别的判据；本条自身必须也能失败）。 */
ok(kKeyReady && kPrevented === 0, "K18 不是行上的按键不吞（preventDefault 只在命中行时调用）");

/* 弹层是否开着**当场读**，不用前面那段 `run()` 里存下的快照：快照与这里隔了几十行，
   中途任何一次 `closeMailDetail()` 都会让它变成**过期值** —— 那时 K19 照样绿（快照说「开着」），
   而 K20 会因为「它本来就关着」而变绿 ⇒ Esc 那条断言被**静默架空**（实测：在旧写法下插一发
   `closeMailDetail()`，93/93 照旧全绿）。`nodes[…]` 是沙箱里那个真元素的活引用。 */
ok(nodes["mailDetail"].hidden === false, "K19 前提：详情弹层处于打开态");
/* **按 Esc 之前必须把前面几段留下的弹层状态清干净**（这不是洁癖）：
   app.js 的 Esc 链是**顺序 if**，`#loginPrompt` 那一条排在 `#mailDetail` **前面**
   —— I 段（I8 那发）会把 `#loginPrompt` 打开，`iDismiss()` 走的是
   `$("btnLoginPromptNo").click()` 这条**间接**出口。只要它有哪一次没合上，这一发 Esc
   就会被前面那条分支截胡，K20 以一个与详情毫无关系的形态变红（本仓吃过同类亏：
   `mkNode` 的 `hidden` 默认为 false 时，G 段的 Esc 断言根本走不到 `#confirmPrompt`）。
   故先把 Esc 链上排在 `#mailDetail` 之前的弹层逐个合上，K19b 断言它们确实都合上了。 */
run(`for(const id of ["loginPrompt", "confirmPrompt", "detailModal", "modulePop"])
       document.getElementById(id).hidden = true;`);
ok(nodes["loginPrompt"].hidden === true && nodes["detailModal"].hidden === true
   && nodes["modulePop"].hidden === true,
   "K19b 前提：Esc 链上排在 #mailDetail 之前的弹层都已合上（否则 K20 是替别人红）");
pressEscape();
ok(nodes["mailDetail"].hidden === true, "K20 Esc 关掉详情弹层");
sandbox.fetch = kBaseFetch;

/* 空态：转发件为空时说的是「最近 N 天没有转发过来的邮件」，不是「0 条」了事（spec §6）。
   判据正负成对：正半证明新文案在场，负半证明**旧文案不在场**（只查在场排除不了
   两处文案拼进同一格、或旧值被加回字典再用上）。旧文案只写**字面量**。 */
const K_EMPTY = "最近 7 天没有转发过来的邮件";
run(`globalThis.__kEmptyText = t("mail.empty", { n: MAIL_DAYS });`);
ok(sandbox.__kEmptyText === K_EMPTY,
   `K21 前提：t("mail.empty", {n:7}) == ${JSON.stringify(K_EMPTY)}（实得 ${JSON.stringify(sandbox.__kEmptyText)}）`);
run(`mailState.loading = false; mailState.error = ""; mailState.mails = [];
     mailState.groups = null; renderMail();
     globalThis.__kEmpty = document.getElementById("mailList").innerHTML;`);
ok(sandbox.__kEmpty.includes(K_EMPTY) && !sandbox.__kEmpty.includes("没有需要处理的邮件"),
   `K22 空态文案逐字带天数，且不含旧文案（实得 ${JSON.stringify(sandbox.__kEmpty)}）`);

/* L：Task 9 新增的两条**交互入口**（行可点、行可键盘打开）与 Task 10 详情渲染的边界。
   为什么单开这一段（Task 9 复审的头号输入）：Task 9 复审实测「行可点」「行可键盘打开」
   **在自动化里覆盖为零**，而缺陷 1（键盘激活行内写入按钮被劫持）**正是从这个洞漏出去的，
   而且它当时是全绿的**。分组行那半条路径由 K0（渲染侧锚 = `mail_index`）+ K2（点它取的是
   哪一封）覆盖；本段补的是**磁贴行**（`groups` 为空时的 `.mail-row`）那条路径、
   「行内交互元素不被劫持」那道门禁、以及外链/附件区/`fmtBytes` 三处边界。
   判据一律**正负成对**（在场 + 缺席），只查在场排除不了「拿错了但碰巧也在」。
   载荷证明见文件头：`--mutate-kread` → L1 红、`--mutate-kkey` → L7 红、
   `--mutate-kguard` → L8 红。仍零出网：`sandbox.fetch` 此刻已还原成基础桩
   （`/api/mail/detail` 在放行名单里、响应由 `detailStub` 控制），其余 URL 照旧 throw。 */
detailStub.reset();
detailStub.payload = { ok: true, detail: { id: "SYNTH-L-ID-1",
  body: "L-BODY 见 https://example.invalid/l. 尾巴",
  attachments: [{ index: 0, name: "SYNTH-L-ATT-0.pdf", size: 0 },
                { index: 1, name: "SYNTH-L-ATT-1.bin", size: 3145728 }] } };
run(`
  mailState.loading = false; mailState.error = "";
  mailState.groups = [];                       // 空 ⇒ 平铺（磁贴）列表那条路径
  mailState.mails = [
    { index: 0, id: "SYNTH-L-ID-0", subject: "SYNTH-L-SUBJ-0", sender: "s0@example.invalid", received: "2026-09-01T00:00:00Z" },
    { index: 1, id: "SYNTH-L-ID-1", subject: "SYNTH-L-SUBJ-1", sender: "s1@example.invalid", received: "2026-09-02T00:00:00Z" },
  ];
  renderMail();
  globalThis.__lTile = document.getElementById("mailList").innerHTML;
`);
/* 磁贴行的取行锚必须**是它自己那一格**：两行各自的 `data-m` 逐个点名（不是「至少有一个」），
   且带 `tabindex="0"`（没有锚的行点不开详情、也就不该是键盘停靠点，二者同生共死）。 */
const lTileRows = [...sandbox.__lTile.matchAll(/<div class="card mail-row"([^>]*)>/g)]
  .map(m2 => ({ attrs: m2[1], m: (m2[1].match(/data-m="([^"]*)"/) || [])[1] }));
ok(lTileRows.length === 2 && lTileRows[0].m === "0" && lTileRows[1].m === "1"
   && lTileRows.every(r => r.attrs.includes('tabindex="0"')),
   `L0 磁贴行（.mail-row）各自带自己的 data-m 锚与 tabindex（实得 ${JSON.stringify(lTileRows.map(r => r.m))}）`);

/* 点**非第一行**（`data-m="1"`）：恒取第 0 封 / 锚写错位 都会在这里露出来。 */
const lBeforeOpen = fetchLog.length;
await settle(clickers[0]({ target: {
  closest: sel => (sel === "[data-m]" ? { dataset: { m: "1" } } : null) } }));
const lOpenLog = fetchLog.slice(lBeforeOpen).filter(f => f.url === "/api/mail/detail");
ok(lOpenLog.length === 1 && lOpenLog[0].body.id === "SYNTH-L-ID-1",
   `L1 点磁贴行（data-m="1"）取的是那一封自己（实得 ${JSON.stringify(lOpenLog.map(f => f.body.id))}）`);

/* 外链必须走 app.js **既有**的那条 document 级 `a[href]` 捕获委托（`:26`），不是被某个
   新处理器接走。驱动方式是把 document 上的 click 监听器**真的跑一遍**，并临时装一个
   pywebview 桥 —— 只看「没抛错」证明不了任何东西（`location` 缺失时那条委托会抛
   ReferenceError 再被自己的 catch 吞掉、静默 return，正是「假绿」的形态）。
   判据：恰好**一个**监听器接住它，且递出去的 URL 逐字就是正文里那一个。 */
const lAnchorStub = { getAttribute: () => "https://example.invalid/l", target: "" };
let lDelegateHits = 0;
run(`globalThis.pywebview = { api: { openExternal(u){ globalThis.__lOpened = String(u); } } };`);
for (const fn of (docListeners.click || [])) {
  let prev = 0;
  try { fn({ button: 0, defaultPrevented: false,
    target: { closest: sel => (sel === "a[href]" ? lAnchorStub : null) },
    preventDefault(){ prev++; } }); } catch (e) {}
  if (prev) lDelegateHits++;
}
run(`delete globalThis.pywebview;`);
const lDelivered = sandbox.__lOpened || "";
ok(lDelegateHits === 1 && lDelivered === "https://example.invalid/l",
   `L2 详情正文的外链被**既有**的 document 级 a[href] 捕获委托接住并交给系统浏览器（实得 ${lDelegateHits} 个监听器接住 / ${JSON.stringify(lDelivered)}）`);
ok(!(((elListeners["mailDetailBody"] || {}).click || []).length),
   "L3 #mailDetailBody 上没有任何 click 处理器（外链交给 document 级委托，没另起新处理器）");

/* 附件区按**既有类名**渲染（K8/K9 只断言了 `data-att` 与文案，没钉类名）。 */
const lAttsHtml = nodes["mailDetailAtts"].innerHTML;
ok(lAttsHtml.includes('class="mail-att"') && lAttsHtml.includes("mail-att-name")
   && lAttsHtml.includes("mail-att-size") && lAttsHtml.includes('data-att="0"')
   && lAttsHtml.includes('data-att="1"'),
   `L4 附件区按既有类名渲染（mail-att / mail-att-name / mail-att-size + data-att；实得 ${JSON.stringify(lAttsHtml)}）`);

/* `fmtBytes` 的边界（Task 11 附录二·4）：`null`/非数**留白**、真实 0 是 `0 B`、
   极大值进 MB、绝不抛错。K10 只看渲染串里「没有 0 B」；这里逐值点名，把两件事分开钉。
   整张表进一个断言：任一格错了都该红，而「抛错」由 try/catch 转成一条可读的红，
   不让整个 `runInContext` 把自检炸在半路。 */
run(`
  try { globalThis.__lBytes = [fmtBytes(null), fmtBytes(0), fmtBytes(1023), fmtBytes(1024),
        fmtBytes(1073741824), fmtBytes(NaN), fmtBytes(-1), fmtBytes("2048")]; }
  catch(e){ globalThis.__lBytes = "SYNTH-THREW:" + e.message; }
`);
const L_BYTES = ["", "0 B", "1023 B", "1.0 KB", "1024.0 MB", "", "", ""];
ok(JSON.stringify(sandbox.__lBytes) === JSON.stringify(L_BYTES),
   `L5 fmtBytes 边界逐值正确且不抛错（null/0/1023/1024/1GiB/NaN/负/数字串；实得 ${JSON.stringify(sandbox.__lBytes)}）`);

/* 键盘正例：**Space 与 Enter 两个键都要**（K16/K17 钉的是 Enter，这里补 Space）。
   条件与 K16 同形：`e.target` 就是行自己、`closest("button, a, …")` 为 null。 */
const lKeydowns = (elListeners["mailList"] || {}).keydown || [];
/* K 段那两个处理器各有「捕获到…」的计数断言（K11/K15）兜着，**这一段一个都没有** ——
   所以把「抓没抓到」并进 L6/L7 的判据里。不并的话，拆掉注册之后 L6 会红（它断言日志变了），
   而 L7 会**空断言变绿**：没有处理器 ⇒ 既不吞键也不发请求，`prev`/`added` 两个分量都平凡为 0，
   而 L7 恰是本段最重要的那道门禁。不新增断言条数，只让它不再空转。 */
const lKeyReady = lKeydowns.length === 1;
let lSpacePrev = 0;
const lBeforeSpace = fetchLog.length;
callCaptured(lKeydowns, { key: " ", preventDefault(){ lSpacePrev++; },
  target: { closest: sel => (sel === "[data-m]" ? { dataset: { m: "1" } } : null) } });
const lSpaceLog = fetchLog.slice(lBeforeSpace).filter(f => f.url === "/api/mail/detail");
ok(lKeyReady && lSpacePrev === 1 && lSpaceLog.length === 1 && lSpaceLog[0].body.id === "SYNTH-L-ID-1",
   `L6 行上按 Space 也能打开详情且 preventDefault（与 K16/K17 的 Enter 配对；实得 preventDefault ${lSpacePrev} 次 / ${JSON.stringify(lSpaceLog.map(f => f.body.id))}）`);

/* **缺陷 1 的门禁（本段最重要的一条）**：`e.target` 是行内**后代**、`closest("button, a,
   input, select, textarea")` 命中、`closest("[data-m]")` **也命中祖先** —— 这正是那一
   缺陷的形态：少了那行守卫，`preventDefault` 会吞掉按钮的合成 click，同时弹出邮件详情。
   K18 用的 `closest: () => null` 是**另一个**形态（取不到行 ⇒ 本来就会早退），
   它在这个缺陷活着的时候照旧绿 —— 故本条的判别对象必须两个 closest 都命中。 */
const lHijack = ["Enter", " "].map(key => {
  let prev = 0;
  const before = fetchLog.length;
  callCaptured(lKeydowns, { key, preventDefault(){ prev++; },
    target: { closest: sel => (sel === "button, a, input, select, textarea"
      ? { dataset: {} } : sel === "[data-m]" ? { dataset: { m: "1" } } : null) } });
  return { key, prev, added: fetchLog.slice(before).filter(f => f.url === "/api/mail/detail").length };
});
ok(lKeyReady && lHijack.every(o => o.prev === 0 && o.added === 0),
   `L7 行内交互元素（写入按钮）自己吃 Enter/Space：不吞键、也不开详情（缺陷 1 的门禁；实得 ${JSON.stringify(lHijack)}）`);

/* 空的 `data-m` 不取行：`Number("")` 是 0，少一道守卫就会把「没有锚」静默说成「第 0 封」。
   这是 app.js 自己在 `mailRowTarget` 上写明的防御层（`mailRowAttr` 不写非整数索引，
   故只有在 DOM 被别的东西动过时才触发）—— 变异 `--mutate-kguard` 就是拆掉它。 */
const lTitleBefore = nodes["mailDetailTitle"].textContent;
const lBeforeEmpty = fetchLog.length;
await settle(clickers[0]({ target: {
  closest: sel => (sel === "[data-m]" ? { dataset: { m: "" } } : null) } }));
const lEmptyLog = fetchLog.slice(lBeforeEmpty).filter(f => f.url === "/api/mail/detail");
/* 标签里那个「标题怎么样」**必须插值**：`--mutate-kguard` 下标题**真的会变**
   （`Number("")` 是 0 ⇒ 打开了第 0 封，`SYNTH-L-SUBJ-1` → `…-0`），写死一句「标题未动」
   会在红的那一行上主动误导下一个跑变异的人 —— 探针的标签也是断言。 */
const lTitleAfter = nodes["mailDetailTitle"].textContent;
ok(lEmptyLog.length === 0 && lTitleAfter === lTitleBefore,
   `L8 空的 data-m 不取行（守卫挡住 Number("") 是 0；实得 ${lEmptyLog.length} 个详情请求 / 标题 ${
     lTitleAfter === lTitleBefore ? "未动"
       : `动了：${JSON.stringify(lTitleBefore)} → ${JSON.stringify(lTitleAfter)}`}）`);

/* M：**详情缓存必须属于当前打开的那一封**（Task 10 复审缺陷 1 的门禁）。
   要构造的是「缓存非空、且属于**另一封**、弹层还**开着**」这一态 —— 只写「加载期无参调用
   是 no-op」是不够的（那时缓存是 `null`，走的是 `!d` 那条早退，与缺陷完全无关，缺陷活着
   时照样绿；Task 10 复审点名过这一点）。失败与在途是**两条不同的进入方式**，都要走一遍：
   失败分支从不写缓存、在途期间也还没写，两种情况下缓存里留的都是**上一封 A**。
   最后一条是**对照组**（不得过度拦截）：A 成功时无参调必须照旧重渲 A —— 那是 Task 10
   那个钩子存在的全部理由。变异 `--mutate-kdid` 下 M3/M4 必须红、M1 必须仍绿。 */
const M_DETAIL_A = { ok: true, detail: { id: "SYNTH-M-ID-A",
  body: "A-BODY https://example.invalid/a",
  attachments: [{ index: 0, name: "SYNTH-M-ATT-A.pdf", size: 1536 }] } };
detailStub.reset();
run(`closeMailDetail();`);                       // 起点干净：`id` 归零
detailStub.payload = M_DETAIL_A;
await settle(run(`openMailDetail({ id: "SYNTH-M-ID-A" });`));
ok(nodes["mailDetailBody"].innerHTML.includes("A-BODY")
   && nodes["mailDetailAtts"].innerHTML.includes("SYNTH-M-ATT-A.pdf"),
   `M0 前提：A 打开成功 ⇒ 它的正文与附件清单都在场（缓存 = A；实得 ${JSON.stringify(nodes["mailDetailBody"].innerHTML)}）`);

/* 对照组先把 DOM 抹成哨兵再调无参入口：否则「A 还在屏幕上」是**平凡成立**的
   （上一次渲染留下的），这条断言就成了不承重的摆设 —— 本仓吃过「探针自己说谎」的亏。 */
run(`document.getElementById("mailDetailBody").innerHTML = "SYNTH-M-WIPED";
     document.getElementById("mailDetailAtts").innerHTML = "SYNTH-M-WIPED";`);
run(`renderMailDetail();`);
ok(nodes["mailDetailBody"].innerHTML.includes("A-BODY")
   && !nodes["mailDetailBody"].innerHTML.includes("SYNTH-M-WIPED")
   && nodes["mailDetailAtts"].innerHTML.includes("SYNTH-M-ATT-A.pdf"),
   `M1 对照组（不得过度拦截）：无参调把 A 的正文与附件**照旧重渲**（实得 ${JSON.stringify(nodes["mailDetailBody"].innerHTML)}）`);

/* 进入方式①：B 打不开（`{ok:false}`）。裁定 A 要的那条失败路径 —— brief 的 K 段桩
   只有「成功」与「挂起」两种，没有它。 */
detailStub.mode = "fail";
run(`closeMailDetail();`);
await settle(run(`openMailDetail({ id: "SYNTH-M-ID-B" });`));
const mErrBody = nodes["mailDetailBody"].innerHTML;
ok(mErrBody.includes("SYNTH-DETAIL-ERR") && nodes["mailDetail"].hidden === false,
   `M2 前提：B 打不开时正文区是错误卡、弹层仍**开着**（实得 ${JSON.stringify(mErrBody)}）`);
run(`renderMailDetail();`);
ok(nodes["mailDetailBody"].innerHTML === mErrBody
   && !nodes["mailDetailBody"].innerHTML.includes("A-BODY")
   && !nodes["mailDetailAtts"].innerHTML.includes("SYNTH-M-ATT-A.pdf"),
   `M3 失败路径：无参调**不许**把上一封（A）的内容画上来，错误卡逐字还在（实得 ${JSON.stringify(nodes["mailDetailBody"].innerHTML)}）`);

/* 进入方式②：C 的响应**挂起不落地**（deferred，不 resolve）—— 弹层开着、缓存仍是 A。 */
detailStub.mode = "pending";
run(`closeMailDetail();`);
run(`openMailDetail({ id: "SYNTH-M-ID-C" });`);   // 刻意不 await：这一态就是「在途」
const mFlightBody = nodes["mailDetailBody"].innerHTML;
const mFlightAtts = nodes["mailDetailAtts"].innerHTML;
run(`renderMailDetail();`);
ok(nodes["mailDetailBody"].innerHTML === mFlightBody
   && !nodes["mailDetailBody"].innerHTML.includes("A-BODY")
   && nodes["mailDetailAtts"].innerHTML === mFlightAtts
   && !nodes["mailDetailAtts"].innerHTML.includes("SYNTH-M-ATT-A.pdf"),
   `M4 在途路径：无参调**不许**把上一封（A）的内容画上来（弹层开着、响应未落地；实得 ${JSON.stringify(nodes["mailDetailBody"].innerHTML)}）`);

console.log(fails ? `\n✗ ${fails} 条未通过（共 ${total} 条断言）` : `\n✓ 全部通过（共 ${total} 条断言）`);
process.exit(fails ? 1 : 0);
