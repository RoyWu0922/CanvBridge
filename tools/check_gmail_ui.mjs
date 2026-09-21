#!/usr/bin/env node
/**
 * 设置页「学校邮箱（Gmail）」凭据 UI 自检：守住 refreshGmailUi() 的**三态**
 * 与保存/清除两条写入路径的契约。
 *
 * 存在的意义：本仓没有前端测试框架，`node --check` 又只查语法；而既有的三个自检里
 * **没有任何一条**会执行 refreshGmailUi / btnSaveGmail / btnClearGmail ——
 * check_mail_frontend 只驱动 renderMail 与 #mailList 的点击、check_dom_ids 与
 * check_i18n_keys 只做**字面**比对（id 在不在、键在不在）。于是这段 UI 逻辑曾在
 * 整整一轮里**没有任何门禁**：它当时带的一个缺陷（读状态失败被渲染成「没保存过」）
 * 是全绿的三个自检放行的，只被人肉探针发现过。这里用最小 DOM 替身把它真跑起来。
 *
 * 它守的三条契约（失败模式都是**静默**的）：
 *   1) **读状态失败必须是一个可见的错误**，绝不能退化成「没保存过」的界面 ——
 *      地址框空着 + 清除按钮藏着 + 提示空着，与全新安装**逐字相同**：用户分不清
 *      「凭据没了」与「没问出来」，还会把已经存好的应用专用密码再填一遍。
 *   2) **密码框的寿命**：保存成功即清空、刷新即清空、**保存失败必须保留**（供重试）；
 *      服务器响应永不回填密码框。
 *   3) **写入路径**：地址 trim 后才送、清除走 DELETE、成功之后必须重读状态
 *      （`api()` 在传输层失败时返回 `{ok:false}` 而不抛错，所以「重读了没有」
 *      只能靠记账看，不能靠 try/catch 看）。
 *
 * 什么时候该跑：任何改动 frontend/app.js 里的 refreshGmailUi / btnSaveGmail /
 * btnClearGmail / openSettings 之后。
 *
 * 零出网：`fetch` 是桩，只放行下面三条端点并记账，**其余 URL 一律 throw**。
 *   GET          /api/mail/gmail/credentials/status
 *   POST|DELETE  /api/mail/gmail/credentials
 *   GET          /api/banweb/credentials/status   ← 仅因 openSettings() 会连带刷新 AIMS
 *                                                   那一组；AIMS 本身不在本自检观察范围内
 * 全部夹具都是合成的：地址用 RFC 2606 保留域（`synth-user@example.invalid`，永不解析），
 * 密码是 `SYNTH-` 前缀的占位串，**绝不含任何真实凭据**，也不会被写进任何输出文件。
 *
 * 用法：node tools/check_gmail_ui.mjs [变异开关…]
 *
 *   变异开关（用来证明上面的断言不是空转；不属于应用本身，只在改这份自检时手工跑）：
 *     --mutate-status-fail  读状态失败改回「按没有凭据渲染」（**缺陷 1 修复前**的写法）
 *     --mutate-notrim       地址不 trim 就送
 *     --mutate-passclear    保存成功后不清空密码框
 *     --mutate-norefresh    保存/清除成功后都不再重读状态
 *     --mutate-post-clear   清除改回 POST（不再是 DELETE）
 *     --mutate              = 上面**五个一起上**（各自锚点在互不重叠的位置，可以叠加）
 *     --mutate-keepass      刷新不再清空密码框
 *     --mutate-prefill      把服务器响应里的地址回填进密码框
 *   `--mutate-keepass` 与 `--mutate-prefill` **不在** `--mutate` 里，且两者互斥
 *   （打在**同一行**上：都改「刷新时密码框写什么」这一处），必须各自单独跑。
 *   每个变异锚点必须**恰好命中一次**；没命中、或替换后与原文一字不差（空转探针），
 *   都会直接抛错退出。替换成功时会在 stdout 打一行 `[mutate] …`，自证改到了哪一行。
 *
 * 退出码：0 = 全部断言通过；1 = 有断言未通过 / 变异开关用错（锚点没命中、命中不止一次、
 *   替换后与原文一字不差）/ 变异后的源码加载不了 / 源码读不到。
 */
import vm from "node:vm";
import fs from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/* 源码相对**本脚本**解析（与 tools/check_dom_ids.mjs、check_mail_frontend.mjs 同款）：
   这样从任何 cwd 跑都读得到 frontend/，而不是依赖「恰好站在仓库根」。 */
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

const GMAIL_STATUS = "/api/mail/gmail/credentials/status";
const GMAIL_CRED   = "/api/mail/gmail/credentials";
const AIMS_STATUS  = "/api/banweb/credentials/status";

/* ---- 合成夹具（绝不含真实凭据） ---- */
const ADDR    = "synth-user@example.invalid";   // RFC 2606 保留域，永不解析
const APPPASS = "SYNTH-APPPASS-WXYZ";           // 占位串，不是任何真实密码

/* ---- 最小 DOM 替身（与 check_mail_frontend.mjs 同款） ---- */
const nodes = {};
/* 元素级监听器：`$("btnSaveGmail").onclick = …` 这类不走 addEventListener，
   但留着这张表与兄弟自检同形，将来若有 addEventListener 也不会静默丢掉。 */
const elListeners = {};
/* `hidden` 默认 **true**（index.html 里 btnClearGmail 带 hidden）；`value` 是真字段，
   密码框的寿命断言全靠它。 */
const mkNode = id => (nodes[id] ||= {
  id, innerHTML: "", textContent: "", hidden: true, value: "",
  classList: { add(){}, remove(){}, toggle(){} }, style: {}, dataset: {},
  addEventListener(type, fn){ ((elListeners[id] ||= {})[type] ||= []).push(fn); },
  focus(){}, remove(){}, removeAttribute(){}, click(){},
  querySelector(){ return mkNode("q"); },
  appendChild(){}, setAttribute(){}, getAttribute(){ return null; },
});

/* 本自检要直接读写的那些元素**先建出来**：mkNode 是按访问惰性建的，
   裸写 `nodes["gmailAddress"].value = …`（不经 document.getElementById）会在第一次
   访问时拿到 undefined。 */
["gmailAddress", "gmailAppPassword", "gmailSavedHint", "btnSaveGmail", "btnClearGmail", "status"].forEach(mkNode);

/* localStorage 是**真 map**：`LANG()` 读 `sc_lang`，I 段要切到 en 真跑一遍。 */
const ls = {};
/* 账本：本自检碰过的**每一次** fetch。写入路径的断言（trim / DELETE / 重读状态 /
   「字段为空时一个请求都不发」）全部以它为唯一判据。 */
const ledger = [];
/* 全局账本：`ledger` 每个场景开头会被清空（断言要看「这一段新增了几发」），
   而 J 段要的是**通篇**的见证，故另记一份、永不清空。 */
const allLedger = [];
const ALLOWED = new Set([GMAIL_STATUS, GMAIL_CRED, AIMS_STATUS]);
/* 当前场景安排的响应工厂。没安排就抛错 —— 免得某个场景「意外地没发请求」时
   断言因为记账为空而**看起来**通过（空转探针的经典形态）。 */
let route = null;
const resp = payload => ({ ok: true, status: 200, json: async () => payload });
const hasCred = address => resp({ ok: true, has_credentials: true, address });
const noCred  = () => resp({ ok: true, has_credentials: false });
const apiFail = error => resp({ ok: false, error });

const realSetTimeout = setTimeout;
const sandbox = {
  console, clearTimeout,
  /* **setStatus 会 setTimeout(…, 4500)**：不 unref 的话 node 要等每个横幅的定时器
     都走完才肯退出（自检凭空慢好几秒）。unref 后行为不变、进程不被它拖住。 */
  setTimeout: (fn, ms) => { const h = realSetTimeout(fn, ms); if (h && h.unref) h.unref(); return h; },
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
    documentElement: mkNode("html"), title: "",
    addEventListener(){},
  },
  localStorage: { getItem: k => (k in ls ? ls[k] : null), setItem(k, v){ ls[k] = String(v); }, removeItem(k){ delete ls[k]; } },
  matchMedia: () => ({ matches: false, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){} }),
  /* **这不是出网**，是桩：只记账 + 回一个最小响应。允许清单之外的一切 URL 直接 throw
     —— 「零出网」的口径与兄弟自检逐字相同：不是「一律不许 fetch」，而是「只许碰这几条」。 */
  fetch: (url, opts) => {
    const u = String(url);
    if (!ALLOWED.has(u)) throw new Error("自检不该发起 fetch：" + u);
    const method = (opts && opts.method) || "POST";
    const body = opts && opts.body !== undefined ? JSON.parse(opts.body) : undefined;
    ledger.push({ url: u, method, body });
    allLedger.push({ url: u, method, body });
    if (!route) throw new Error(`场景没有安排响应：${method} ${u}`);
    return route({ url: u, method, body });
  },
  open: () => null,
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
vm.createContext(sandbox);

/* 变异开关：把每条断言各自钉住的写法改回**修复前/错误**的写法，验证判据不是空转。
   本仓吃过「空转探针」的亏：一个没真的改到东西的变异，什么都不能证明 —— 故替换走
   `mutate()`，锚点必须恰好命中一次、且替换后必须与原文不同，否则直接抛错。 */
const MUTATE          = process.argv.includes("--mutate");
const MUTATE_STATFAIL = process.argv.includes("--mutate-status-fail") || MUTATE;
const MUTATE_NOTRIM   = process.argv.includes("--mutate-notrim")       || MUTATE;
const MUTATE_PASSCLR  = process.argv.includes("--mutate-passclear")    || MUTATE;
const MUTATE_NOREFRESH= process.argv.includes("--mutate-norefresh")    || MUTATE;
const MUTATE_POSTCLR  = process.argv.includes("--mutate-post-clear")   || MUTATE;
/* 这两个打在**同一行**上（刷新时密码框写什么），互斥，且都不在 `--mutate` 里。 */
const MUTATE_KEEPASS  = process.argv.includes("--mutate-keepass");
const MUTATE_PREFILL  = process.argv.includes("--mutate-prefill");
if (MUTATE_KEEPASS && MUTATE_PREFILL)
  throw new Error("--mutate-keepass 与 --mutate-prefill 打在同一个锚点上，互斥，请分开跑");

const mutate = (src, anchor, repl, label) => {
  const hits = src.split(anchor).length - 1;
  if (hits !== 1) throw new Error(`[${label}] 变异锚点命中 ${hits} 次（应为 1）：${JSON.stringify(anchor)}`);
  /* **函数式替换**：`String.replace(anchor, repl)` 会把 repl 里的 `$&`/`$1` 当**替换模式**
     展开，于是真正写进去的东西与下一行打出的 witness 可以**不是一回事** —— 而这一行
     witness 正是「变异非空转」的证明本身。本文件的 repl 里满是 `$("gmailAddress")`，
     字符串式替换下 `$(` 虽不构成替换模式，但照样不该赌。传函数一律按**字面**替换。 */
  const out = src.replace(anchor, () => repl);
  if (out === src) throw new Error(`[${label}] 变异未生效（替换后与原文一字不差）：${JSON.stringify(repl)}`);
  /* witness 必须**如实**表示真正落下去的东西：单行且无控制字符时原样打，否则 JSON 引号化
     （多行替换打 JSON 转义版，绝不截断成一行配多行插入）。
     判别用 charCodeAt 而不是正则字符类：字符类里要写控制字符的**字面量**或转义序列，
     前者会让本文件真的含 NUL 等裸字节（grep 当场把它当二进制文件，半个字都不输出），
     `[ -…]` 这种转义在生成/复制这段源码时又极易被解成控制字符本身 —— 两者都踩过。 */
  const hasCtrl = s => { for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); if (c < 32 || c === 127) return true; } return false; };
  const witness = hasCtrl(repl) ? JSON.stringify(repl) : repl;
  console.log(`[mutate] ${label} → ${witness}`);
  return out;
};

/* 变异锚点**只钉在实现的那几行字面上**，不跨注释 —— 锚点一旦把注释也圈进去，
   往实现里补一句说明就会让变异以「锚点命中 0 次」炸掉（那是自检在说谎：它说变了、其实没变）。
   故 refreshGmailUi 的「修复前」写法拆成三段，各自落在代码行上，中间那段（密码框）留空。 */
const ANCHOR_STATFAIL_A = `  const fail = r.ok !== true;
  const has  = !fail && r.has_credentials === true;
  $("gmailAddress").value = has ? (r.address || "") : "";
`;
const ANCHOR_STATFAIL_B = `  el.textContent = has  ? t("status.gmail_saved_hint")
                 : fail ? t("status.gmail_status_fail") + (r.error||"")
                 : "";
`;
const ANCHOR_STATFAIL_C = `  if(fail) setStatus(t("status.gmail_status_fail") + (r.error||""), "err");\n`;
const ANCHOR_PASSBOX = `  $("gmailAppPassword").value = "";   // 与 AIMS 同款：刷新即清空，不给密码留在 DOM 里的机会`;

for (const f of ["i18n.js", "util.js", "app.js", "shell.js"]) {
  let src = fs.readFileSync(join(ROOT, "frontend", f), "utf8");
  if (f === "app.js") {
    if (MUTATE_STATFAIL) {
      /* **缺陷 1 修复前**：读失败没有分支，`has` 直接取 `r.ok === true && …`，
         于是失败被渲染成「没保存过」（地址空、清除按钮藏、提示空），一声不吭。
         三段合起来正好还原修复前的函数体（中间那行密码框保持原样）。 */
      src = mutate(src, ANCHOR_STATFAIL_A,
        `  const has = r.ok === true && r.has_credentials === true;
  $("gmailAddress").value = has ? (r.address || "") : "";
`, "status-fail-a");
      src = mutate(src, ANCHOR_STATFAIL_B, `  el.textContent = has ? t("status.gmail_saved_hint") : "";\n`, "status-fail-b");
      src = mutate(src, ANCHOR_STATFAIL_C, "", "status-fail-c");
    }
    if (MUTATE_KEEPASS) {
      src = mutate(src, ANCHOR_PASSBOX,
        `  $("gmailAppPassword").value = $("gmailAppPassword").value;   // 变异：刷新不再清空密码框`,
        "keepass");
    }
    if (MUTATE_PREFILL) {
      /* 服务器**永不**回传密码：把响应里的地址回填进密码框是最容易犯的那种「顺手回填」，
         且它与「刷新即清空」在同一条断言上，正好证明那条断言不是空转。 */
      src = mutate(src, ANCHOR_PASSBOX,
        `  $("gmailAppPassword").value = r.address || "";   // 变异：拿服务器响应回填密码框`,
        "prefill");
    }
    if (MUTATE_NOTRIM) {
      src = mutate(src, "  const address = $(\"gmailAddress\").value.trim();",
        `  const address = $("gmailAddress").value;   // 变异：不 trim 就送`, "notrim");
    }
    if (MUTATE_PASSCLR) {
      src = mutate(src, `    $("gmailAppPassword").value = "";   // 存成功即清空：密码不留在 DOM 里`,
        `    // 变异：保存成功后不清空密码框`, "passclear");
    }
    if (MUTATE_NOREFRESH) {
      src = mutate(src, `    setStatus(t("status.gmail_saved_ok"), "ok", 4000);\n    await refreshGmailUi();`,
        `    setStatus(t("status.gmail_saved_ok"), "ok", 4000);\n    // 变异：保存成功后不重读状态`, "norefresh-save");
      src = mutate(src, `    setStatus(t("status.gmail_cleared"), "ok");\n    await refreshGmailUi();`,
        `    setStatus(t("status.gmail_cleared"), "ok");\n    // 变异：清除后不重读状态`, "norefresh-clear");
    }
    if (MUTATE_POSTCLR) {
      src = mutate(src, `    const r = await api("mail/gmail/credentials", undefined, "DELETE");`,
        `    const r = await api("mail/gmail/credentials");   // 变异：清除改回 POST`, "post-clear");
    }
  }
  try {
    vm.runInContext(src, sandbox, { filename: f });
  } catch (err) {
    /* 变异把代码改成了语法错误时，要让失败说清是谁干的，而不是抛一个看不出与变异有关的裸错。 */
    throw new Error(`加载 frontend/${f} 失败（跑过变异开关的话，多半是它把代码改坏了）：${err.message}`);
  }
}

const run = code => vm.runInContext(code, sandbox);

/* 变异的实现可能真的挂住。挂住也是「红」，不该让整轮自检吊死，故给 await 套一个限时。 */
const settle = async p => {
  let t;
  const guard = new Promise(r => { t = realSetTimeout(() => r("SYNTH-TIMEOUT"), 500); });
  const v = await Promise.race([p, guard]);
  clearTimeout(t);
  return v;
};
/* 把挂起的微任务/宏任务推平：`api()` 是 `await fetch` + `await r.json()` 两跳，
   withBusy 外面还有一层 await，故留足 6 拍。**不靠猜时序**：每拍一个宏任务，
   比 `setTimeout(…, N)` 那种「睡够就好」的写法可靠。 */
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(r => realSetTimeout(r, 0)); };

/* 字典直接取（与 check_i18n_keys.mjs 同款切片）：**不能**拿沙箱里的 t() 当期望值 ——
   键缺失时 t() 原样返回键名，而断言两边会一起变成那个裸键名，**恒真**（别名盲区）。 */
const i18nSrc = fs.readFileSync(join(ROOT, "frontend", "i18n.js"), "utf8");
const cut = i18nSrc.indexOf("const LANG");
if (cut < 0) throw new Error("在 frontend/i18n.js 里找不到 `const LANG`，无法切出字典片段");
const dictCtx = {}; vm.createContext(dictCtx);
vm.runInContext(i18nSrc.slice(0, cut) + "\n;globalThis.__I18N = I18N;", dictCtx);
const ZH = dictCtx.__I18N.zh, EN = dictCtx.__I18N.en;
/* 取值缺失时回一个**一眼假**的哨兵而不是抛错：断言会红，witness 直接指出是哪个键没了。 */
const zh = k => (typeof ZH[k] === "string" && ZH[k]) || `<<zh 缺键:${k}>>`;

let fails = 0, total = 0;
const ok = (cond, label) => { total++; console.log((cond ? "✓ " : "✗ ") + label); if (!cond) fails++; };
const J = v => JSON.stringify(v);

const hint  = () => nodes["gmailSavedHint"].textContent;
/* `nodes` 里的条目是**按访问惰性建**的（mkNode），所以这里必须走 mkNode 而不是裸下标 ——
   直接写 nodes["status"] 会在第一次调用时拿到 undefined。 */
const banner = () => ({ text: mkNode("status").textContent, cls: mkNode("status").className });
const resetBanner = () => { mkNode("status").textContent = ""; mkNode("status").className = ""; };
const calls = (url, method) => ledger.filter(e => e.url === url && (!method || e.method === method));

/* ================= A：无凭据 —— 这就是「未配置」界面 ================= */
/* 它的三件套（地址空 / 提示空 / 清除藏）是后文「读失败不算未配置」的**对照物**，
   故把提示原文记下来，C 段直接拿两者比对。 */
ledger.length = 0; resetBanner();
route = () => noCred();
nodes["gmailAppPassword"].value = "SYNTH-PRE-SEED";   // 先塞一个值：刷新必须把它清掉
await settle(run("refreshGmailUi()"));
const A_status = calls(GMAIL_STATUS);
const hintNoCred = hint();
ok(A_status.length === 1 && A_status[0].method === "GET",
   `A1 读状态走 GET（该端点是 @app.get，发成 POST 就是 405；实得 ${J(A_status.map(e => e.method + " " + e.url))}）`);
ok(nodes["gmailAddress"].value === "" && hintNoCred === "" && nodes["btnClearGmail"].hidden === true,
   `A2 无凭据 = 未配置界面：地址空、提示空、清除按钮藏（实得 ${J([nodes["gmailAddress"].value, hintNoCred, nodes["btnClearGmail"].hidden])}）`);
ok(nodes["gmailAppPassword"].value === "",
   `A3 刷新即清空密码框（服务器永不回传密码，「清空」是唯一正确语义；实得 ${J(nodes["gmailAppPassword"].value)}）`);

/* ================= B：有凭据 ================= */
ledger.length = 0; resetBanner();
route = () => hasCred(ADDR);
nodes["gmailAppPassword"].value = "SYNTH-PRE-SEED";
await settle(run("refreshGmailUi()"));
ok(nodes["gmailAddress"].value === ADDR,
   `B1 有凭据：地址回填（实得 ${J(nodes["gmailAddress"].value)}）`);
ok(nodes["btnClearGmail"].hidden === false, "B2 有凭据：清除按钮现身");
ok(hint() === zh("status.gmail_saved_hint"),
   `B3 有凭据：提示是「已保存」那句（实得 ${J(hint())}）`);
ok(nodes["gmailAppPassword"].value === "",
   `B4 刷新只清空、绝不回填：密码框原先的值与响应里的地址都没进来（实得 ${J(nodes["gmailAppPassword"].value)}）`);

/* ================= C：读状态失败 —— **缺陷 1 的回归绑定** ================= */
/* 三态里的第三态。**它必须是可见的错误，且不能与 A（未配置）同一种渲染** ——
   否则用户分不清「凭据没了」与「没问出来」。
   先把界面摆成「有凭据」的样子：证明失败会把它清掉，而不是留下一屏过期信息。 */
ledger.length = 0; resetBanner();
nodes["gmailAddress"].value = ADDR;
nodes["btnClearGmail"].hidden = false;
nodes["gmailSavedHint"].textContent = "SYNTH-STALE-HINT";
nodes["gmailAppPassword"].value = "SYNTH-PRE-SEED-2";
route = () => apiFail("SYNTH-KEYCHAIN-DOWN");
await settle(run("refreshGmailUi()"));
const failPrefix = zh("status.gmail_status_fail");
const expectFail = failPrefix + "SYNTH-KEYCHAIN-DOWN";
ok(hint() !== "" && hint().includes(failPrefix) && hint().includes("SYNTH-KEYCHAIN-DOWN"),
   `C1 读失败必须留下**非空**且带出原因的提示（提示空着就是「没保存过」；实得 ${J(hint())}）`);
ok(hint() !== hintNoCred,
   `C2 读失败的渲染与「未配置」**不是**同一种（未配置 ${J(hintNoCred)} ≠ 读失败 ${J(hint())}）`);
ok(banner().text === expectFail && /banner-err/.test(banner().cls),
   `C3 读失败要亮 err 横幅、并带出原因（实得 ${J(banner())}）`);
ok(nodes["gmailAppPassword"].value === "",
   `C4 读失败绝不让密码框留着值（实得 ${J(nodes["gmailAppPassword"].value)}）`);
ok(nodes["gmailAddress"].value === "" && nodes["btnClearGmail"].hidden === true,
   `C5 读失败时不留「有凭据」的样子：地址清空、清除入口藏起（实得 ${J([nodes["gmailAddress"].value, nodes["btnClearGmail"].hidden])}）`);

/* ================= D：保存成功 ================= */
/* 刷新那一发**扣住不落地**：这样才能证明「密码框是**保存处理器**清掉的」，
   而不是靠紧随其后的刷新兜底（探针自己会撒谎的那种：删掉保存处的清空，终态照样是空）。 */
ledger.length = 0; resetBanner();
let held = null;
route = ({ url }) => {
  if (url === GMAIL_CRED)   return resp({ ok: true });
  if (url === GMAIL_STATUS) return new Promise(res => { held = () => res(hasCred(ADDR)); });
  throw new Error("意外请求 " + url);
};
nodes["gmailAddress"].value = "  " + ADDR + "  ";     // 前后带空白 → 必须 trim 后才送
nodes["gmailAppPassword"].value = APPPASS;
nodes["gmailSavedHint"].textContent = "";             // 保存前：未保存态
const pSave = nodes["btnSaveGmail"].onclick();
await tick(6);
const dPost = ledger.filter(e => e.url === GMAIL_CRED);
ok(dPost.length === 1 && dPost[0].method === "POST"
   && dPost[0].body.address === ADDR && dPost[0].body.app_password === APPPASS,
   `D1 保存 POST 到 ${GMAIL_CRED}，正文 {address(已 trim), app_password(原样)}（实得 ${J(dPost.map(e => e.method + " " + e.url + " " + J(e.body)))}）`);
ok(nodes["gmailAppPassword"].value === "",
   `D2 保存成功那一刻密码就已出 DOM，不靠随后的刷新兜底（扣住刷新时实得 ${J(nodes["gmailAppPassword"].value)}）`);
ok(banner().text === zh("status.gmail_saved_ok") && /banner-ok/.test(banner().cls),
   `D3 保存成功提示正确（实得 ${J(banner())}）`);
if (held) held();                                     // 放行被扣住的那次重读
await settle(pSave);
ok(calls(GMAIL_STATUS).length === 1 && calls(GMAIL_CRED).length === 1
   && ledger.indexOf(calls(GMAIL_STATUS)[0]) > ledger.indexOf(calls(GMAIL_CRED)[0]),
   `D4 保存成功后**重读**了状态（实得 ${J(ledger.map(e => e.method + " " + e.url))}）`);
ok(nodes["gmailAddress"].value === ADDR && hint() === zh("status.gmail_saved_hint")
   && nodes["btnClearGmail"].hidden === false,
   `D5 重读把界面刷新成「已保存」（保存前提示是空的，它变出来只能靠这一次重读；实得 ${J([nodes["gmailAddress"].value, hint(), nodes["btnClearGmail"].hidden])}）`);
ok(nodes["gmailAppPassword"].value === "",
   `D6 走完全程后密码框仍为空（实得 ${J(nodes["gmailAppPassword"].value)}）`);

/* ================= E：保存失败 → 保留密码供重试 ================= */
ledger.length = 0; resetBanner();
route = () => apiFail("SYNTH-KEYCHAIN-DENIED");
nodes["gmailAddress"].value = ADDR;
nodes["gmailAppPassword"].value = "SYNTH-RETRY-ME";
await settle(nodes["btnSaveGmail"].onclick());
ok(calls(GMAIL_CRED).length === 1 && calls(GMAIL_CRED)[0].method === "POST",
   `E1 保存失败前确实发过一次 POST（实得 ${J(ledger.map(e => e.method + " " + e.url))}）`);
ok(nodes["gmailAppPassword"].value === "SYNTH-RETRY-ME",
   `E2 保存**失败**必须保留用户已输入的密码供重试（实得 ${J(nodes["gmailAppPassword"].value)}）`);
ok(banner().text === zh("status.gmail_save_fail") + "SYNTH-KEYCHAIN-DENIED" && /banner-err/.test(banner().cls),
   `E3 保存失败提示带出原因（实得 ${J(banner())}）`);

/* ================= F：任一字段为空 → 本地拦下，一个请求都不发 ================= */
/* 三个子例各自跑一遍：两个真空格、以及**只有空白**的地址（守卫取的是 trim 之后的值）。 */
const fCases = [
  { label: "地址为空",   addr: "",      pass: APPPASS },
  { label: "密码为空",   addr: ADDR,    pass: "" },
  { label: "地址只有空白", addr: "   ",  pass: APPPASS },
];
let fi = 0;
for (const c of fCases) {
  fi++;
  ledger.length = 0; resetBanner();
  route = () => { throw new Error("字段没填齐时不该发请求"); };   // 万一发了，记账先记上
  nodes["gmailAddress"].value = c.addr;
  nodes["gmailAppPassword"].value = c.pass;
  await settle(nodes["btnSaveGmail"].onclick());
  ok(ledger.length === 0 && banner().text === zh("status.gmail_need_both") && /banner-err/.test(banner().cls),
     `F${fi} ${c.label} → 本地拦下、不发任何请求，并报「两项都要填」（实测发出 ${ledger.length} 次，提示 ${J(banner())}）`);
}

/* ================= G：清除 ================= */
/* 先摆成**有凭据**的状态：清除后提示会变回空，而「变回空」只能由重读造成 ——
   把重读删掉（变异 norefresh-clear）时，提示会**留在**「已保存」上，G4/G5 立刻红。 */
ledger.length = 0; resetBanner();
route = () => hasCred(ADDR);
await settle(run("refreshGmailUi()"));
ok(hint() === zh("status.gmail_saved_hint"), `G1 前提：清除前界面处于「已保存」态（实得 ${J(hint())}）`);

ledger.length = 0; resetBanner();
let held2 = null;
route = ({ url }) => {
  if (url === GMAIL_CRED)   return resp({ ok: true });
  if (url === GMAIL_STATUS) return new Promise(res => { held2 = () => res(noCred()); });
  throw new Error("意外请求 " + url);
};
nodes["gmailAppPassword"].value = "SYNTH-SHOULD-BE-CLEARED";
const pClear = nodes["btnClearGmail"].onclick();
await tick(6);
const gDel = ledger.filter(e => e.url === GMAIL_CRED);
ok(gDel.length === 1 && gDel[0].method === "DELETE",
   `G2 清除走 DELETE（实得 ${J(gDel.map(e => e.method + " " + e.url))}）`);
ok(gDel.length === 1 && gDel[0].body === undefined,
   `G3 清除不带正文（实得 ${J(gDel.map(e => e.body))}）`);
if (held2) held2();
await settle(pClear);
ok(nodes["gmailAddress"].value === "" && nodes["gmailAppPassword"].value === "",
   `G4 清除后地址与密码都空（实得 ${J([nodes["gmailAddress"].value, nodes["gmailAppPassword"].value])}）`);
ok(calls(GMAIL_STATUS).length === 1 && ledger.indexOf(calls(GMAIL_STATUS)[0]) > ledger.indexOf(gDel[0]),
   `G5 清除成功后**重读**了状态（实得 ${J(ledger.map(e => e.method + " " + e.url))}）`);
ok(hint() === "" && nodes["btnClearGmail"].hidden === true,
   `G6 重读把界面刷回「未配置」（提示从「已保存」变回空只能由这次重读造成；实得 ${J([hint(), nodes["btnClearGmail"].hidden])}）`);

/* ================= H：打开设置页会顺手刷新 Gmail 那组 ================= */
/* app.js 的 openSettings() 里 refreshGmailUi() 与 refreshAimsUi() 并排两行 ——
   删掉前者不会有任何报错，只是这一组永远停在页面加载时的初始样子。 */
ledger.length = 0; resetBanner();
route = () => noCred();
nodes["gmailAppPassword"].value = "SYNTH-PRE-SEED-3";
run("openSettings()");
await tick(6);
ok(calls(GMAIL_STATUS).length === 1,
   `H1 打开设置页会刷新 Gmail 凭据状态（实得 ${J(ledger.map(e => e.method + " " + e.url))}）`);
ok(nodes["gmailAppPassword"].value === "",
   `H2 进入设置页也要清空密码框（实得 ${J(nodes["gmailAppPassword"].value)}）`);

/* ================= I：i18n ================= */
/* 这个键的**用途**是直接接 `r.error`，故两语言的值都必须以「接得上」的分隔符收尾
   （与 status.gmail_save_fail / status.connect_fail 那批兄弟同款：zh 全角冒号、en 冒号+空格）。 */
const zhFail = ZH["status.gmail_status_fail"], enFail = EN["status.gmail_status_fail"];
ok(typeof zhFail === "string" && zhFail !== "" && typeof enFail === "string" && enFail !== "",
   `I1 zh/en 两侧都有非空的 status.gmail_status_fail（实得 ${J([zhFail, enFail])}）`);
ok(/：$/.test(zhFail || "") && /: $/.test(enFail || ""),
   `I2 两语言的值都以可直接拼接错误串的分隔符收尾（zh「：」/ en「: 」；实得 ${J([zhFail, enFail])}）`);
/* 端到端：切到 en 真跑一次读失败，横幅必须是 en 那句 —— 光查字典证明不了它被接上了。 */
ls["sc_lang"] = "en";
ledger.length = 0; resetBanner();
route = () => apiFail("SYNTH-EN-ERR");
await settle(run("refreshGmailUi()"));
ok(banner().text === enFail + "SYNTH-EN-ERR",
   `I3 en 环境下读失败用的是 en 文案（实得 ${J(banner())}）`);
delete ls["sc_lang"];

/* ================= J：账本 ================= */
const tally = new Map();
for (const e of allLedger) { const k = `${e.method} ${e.url}`; tally.set(k, (tally.get(k) || 0) + 1); }
console.log("✓ 本自检只碰过这些端点（零出网：fetch 是桩，允许清单之外的 URL 一律 throw）：");
for (const [k, v] of tally) console.log(`    ${k}   ${v} 次`);
ok(allLedger.filter(e => e.url === GMAIL_STATUS).every(e => e.method === "GET"),
   `J1 通篇对凭据状态端点的每一次调用都是 GET（实得 ${J([...new Set(allLedger.filter(e => e.url === GMAIL_STATUS).map(e => e.method))])}）`);
/* 这条在当前结构下**不会触发**（allowlist 之外在 fetch 里就 throw 了）—— 留着它钉住
   「零出网」的口径本身：一旦有人把 allowlist 放宽，这里会立刻说出放宽到了哪条 URL。 */
ok([...tally.keys()].every(k => ALLOWED.has(k.slice(k.indexOf(" ") + 1))),
   `J2 账本里没有允许清单之外的 URL（实得 ${J([...tally.keys()])}）`);

console.log(fails ? `\n✗ ${fails} 条未通过（共 ${total} 条断言）` : `\n✓ 全部通过（共 ${total} 条断言）`);
process.exit(fails ? 1 : 0);
