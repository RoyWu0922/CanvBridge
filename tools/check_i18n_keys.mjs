#!/usr/bin/env node
/**
 * i18n 键交叉校验：比对 frontend/i18n.js 里 I18N.zh 与 I18N.en 的键集合，
 * 并扫描前端文件里对这些键的引用。
 *
 * 存在的意义：i18n.js 是一份双语字典，zh 与 en 各写一遍。下面三类错误
 * 都不会让页面报错，只会静默劣化，肉眼极难发现：
 *   - 只在一侧加键   → 另一侧 t() 查不到 → 回退到中文（英文界面冒中文）
 *   - 引用了不存在的键 → t() 原样返回键名 → 用户看到 "hub.no_canvas" 这种裸键
 *   - 加了键从未引用   → 死键堆积（本轮开工时已有 25 个）
 * 和 tools/check_dom_ids.mjs 是同一类防线：改完立刻能发现，而不是等跑起来看。
 *
 * 用法：node tools/check_i18n_keys.mjs
 * 退出码：0 = 通过，1 = 双语键集不一致 / 存在悬空引用 / 死键数超出基线
 */
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");

/* 死键基线：只允许持平或下降。新增死键（加完忘了用）会让计数超过它 → 失败。
   数字来自实测，不是估计值；某轮把死键用起来之后，应把它下调到新的实测值。

   2026-09-15 Task 6（图书馆前端骨架）21 → 31：本任务只建静态结构，按简报逐字加入的
   17 个 `library.*`/`nav.library` 键里有 10 个是**给 Task 7 预留**的动态文案
   （`area_any` `count` `empty` `loading` `need_input` `no_link` `page` `parsed`
   `warn.llm_degraded` `warn.domains_dropped`），Task 6 没有消费者。这 10 个键
   由 Task 7 的渲染代码消费后，死键应回落 21 —— Task 7 已把本行降回 21。

   2026-09-19 Task 7（学校邮箱前端骨架）21 → 31：同一形态，同一个理由。按简报逐字加入的
   17 个 `mail.*`/`confirm.*`/`nav.mail` 键里只有 7 个有消费者（`nav.mail` `mail.refresh`
   `mail.summarize` 挂在 index.html 的 data-i18n 上；`mail.count` `mail.empty`
   `mail.unread` `mail.no_body` 由 app.js 的 renderMail / mailRowHtml 调 t() 取用），
   余下 10 个是**给 Task 8 预留**的：`mail.group_other` `mail.uncategorized` `mail.ddl`
   `mail.write_event` `mail.write_reminder` `mail.ai_failed` `mail.need_refresh`
   `mail.wrote` `confirm.cancel` `confirm.ok` —— Task 7 只做「能打开、能列邮件」，
   AI 分组、写入日历/提醒按钮、确认弹层都在 Task 8，本任务没有地方引用它们。
   这 10 个键由 Task 8 的渲染/写入代码消费后，死键应回落 21 —— Task 8 已把本行降回 21。

   2026-09-19 Task 8（课程×类型两级分组 + 逐条确认写入）31 → 21：上一行预留的 10 个键
   本任务全部消费，一个不剩 —— `mail.group_other` `mail.uncategorized` `mail.ddl`
   `mail.wrote` `mail.ai_failed` `mail.need_refresh` 经 app.js 的 mailRenderGroups /
   mailItemHtml / mailSummarize 内联 `t()` 取用，`confirm.cancel` `confirm.ok` 挂在
   index.html 那个新弹层 `#confirmPrompt` 的 `data-i18n` 上。实测死键 21，与预留数
   逐字吻合，故把本行下调到 21 收紧防线（本任务另新增 `mail.ddl_incomplete` 并有消费者，
   不影响死键数）。

   2026-09-21 学校邮箱取信层改走 Gmail IMAP（前端那半）：去掉 `is_read` 的未读标记渲染
   （转发件的「已读」只反映转发账号自己的收件状态，与用户是否处理过无关 —— 恒显示未读
   是假信息），`mail.unread` 因此失去唯一消费者。**键本身一并删掉**，而不是留着让它变成
   死键把计数顶到 22 超出基线 —— 这个键没有任何「给下一个任务预留」的理由。实测死键仍为
   21，与基线持平，故 DEAD_BASELINE 不动。上面 Task 7 那行把 `mail.unread` 列为消费者是
   **当时**的事实，保留原样不改写。

   2026-09-22 终审后修复轮 2（终审 M-4 的收尾）：主题槽位的空值回退原先误用
   `mail.no_body`（「（无正文）」被贴在**主题**格上 —— 标签与它标注的值不符），补出
   `mail.no_subject`（「（无主题）」），并把 `mailRowHtml` / `mailItemHtml` **两处**
   都改用新键（同一处错有两份，改一处不等于改一类）。`mail.no_body` 于是失去**全部**
   消费者，而它在本特性 UI 里也**没有**自己的槽位 —— 它此前那两处引用全是主题格，属
   张冠李戴，没有「本该由它渲染的正文格」等着它回去。**键本身一并删掉**，而不是留着
   让它变成死键把计数顶到 22 超出基线 —— 与上面 `mail.unread` 同一形态，这个键没有
   任何「给下一个任务预留」的理由。实测死键仍为 21，与基线持平，故 DEAD_BASELINE
   不动。上面 Task 7 那行把 `mail.no_body` 与 `mail.unread` 并列为消费者，同样是
   **当时**的事实，保留原样不改写。

   2026-09-25 Task 6（邮件待办前端骨架）21 → 41：同一形态，同一个理由。按简报逐字加入的
   34 个 `mailtodo.*`/`nav.mailtodo` 键里只有 14 个有消费者（`nav.mailtodo` `refresh`
   `extract` `rules` `days` `todo_title` `sched_title` `rule_add` `rule_export` `rule_import`
   `rule_save` 挂在 index.html 的 data-i18n 上；`empty` `count` `need_classify` 由 app.js 的
   renderMailTodo() 调 t() 取用），余下 20 个是**给后面几个任务预留**的：Task 7（规则编辑器）
   8 个 —— `rule_name` `rule_senders` `rule_keywords` `rule_body` `rule_del` `rule_bad`
   `rule_saved` `rule_import_failed`；Task 8（分类请求与待办清单）3 个 —— `body_reading`
   `load_failed` `unmatched`；Task 9（时间表）3 个 —— `extract_failed` `extract_none`
   `extracting`；Task 10（自检收口）6 个 —— `warn.bad_rule` `warn.empty_rule`
   `warn.blank_item` `warn.ai_unusable` `warn.ai_dropped` `warn.ids_missing`（Task 10 把这
   6 个加进 `DYNAMIC_PREFIXES`）。8 + 3 + 3 + 6 = 20，与实测增量 41 − 21 = 20 逐字吻合。
   这 20 个键被各自任务的渲染代码消费后，死键应逐轮回落到**当时的实测值**：
   Task 7 → 33、Task 8 → 30、Task 9 → 27、Task 10 → 21。

   2026-09-25 Task 7（规则编辑器）41 → 33：上一段给本任务预留的那 8 个键**全部消费**，
   一个不剩 —— `rule_name` `rule_senders` `rule_keywords` `rule_body` `rule_del` 由 app.js
   新增的 mtRenderRules() 内联 `t()` 取用；`rule_bad` 由 mtRenderRules()、mtSyncRuleGate() 与
   mtRuleSave 的点击处理器**三处**取用；`rule_saved` 由 mtRuleSave 的点击处理器取用；`rule_import_failed`
   由 mtRuleImport 的导入回调取用。实测死键 33，与预留数 41 − 8 逐字吻合，故把本行下调到
   33 收紧防线（本任务不动上一段末尾那条路标：Task 8 → 30、Task 9 → 27、Task 10 → 21）。 */
const DEAD_BASELINE = 33;

/* 动态家族前缀：源码里存在 "wd." 这样的引号字面量，键由它拼出来
   （app.js 的 t("wd."+d)）。这类键无法被字面量扫描判活，只能按前缀整体放行。
   `library.warn.` 是同一形态：app.js 的 t("library.warn."+w) 把后端回的**警告码**
   翻成句子（警告必须是码而不是句子，否则英文界面会冒中文），所以整族按前缀放行。 */
const DYNAMIC_PREFIXES = ["wd.", "library.warn."];

/* 只取到 const LANG 为止：后半段是 t()/applyLang() 等运行时代码，
   在 Node 里跑会碰到 document / localStorage。前半段是一次纯对象字面量赋值。 */
const i18nSrc = readFileSync(join(FRONTEND, "i18n.js"), "utf8");
const cut = i18nSrc.indexOf("const LANG");
if (cut < 0) {
  console.error("✗ 在 frontend/i18n.js 里找不到 `const LANG`，无法切出字典片段。");
  console.error("  （若该文件被重构过，请同步更新本脚本的切片锚点。）");
  process.exit(1);
}
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(i18nSrc.slice(0, cut) + "\n;globalThis.__I18N = I18N;", sandbox);
const I18N = sandbox.__I18N;

const zhKeys = Object.keys(I18N.zh);
const enKeys = Object.keys(I18N.en);
const zhSet = new Set(zhKeys);
const enSet = new Set(enKeys);

const REF_FILES = ["util.js", "app.js", "shell.js", "index.html"];
const blob = REF_FILES.map(f => readFileSync(join(FRONTEND, f), "utf8")).join("\n");

const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/* 规则 A：键作为带引号的完整字面量出现（覆盖 t("k")、["k"]、cond ? "k" : "j"、X || "k"） */
const isLive = k => new RegExp("[\"'`]" + escapeRe(k) + "[\"'`]").test(blob);
/* 规则 B：动态家族 */
const isDynamic = k => DYNAMIC_PREFIXES.some(p => k.startsWith(p));

/* 悬空引用：t("…") 与 data-i18n*="…" 里出现的、字典中没有的键。
   以 `.` 结尾的是动态拼接（t("wd."+d)），按前缀跳过。 */
const referenced = new Set();
for (const m of blob.matchAll(/\bt\(\s*["'`]([^"'`]+)["'`]/g)) if (!m[1].endsWith(".")) referenced.add(m[1]);
for (const m of blob.matchAll(/data-i18n(?:-ph|-aria)?="([^"]+)"/g)) referenced.add(m[1]);

const fail = [];

const onlyZh = zhKeys.filter(k => !enSet.has(k)).sort();
const onlyEn = enKeys.filter(k => !zhSet.has(k)).sort();
if (onlyZh.length || onlyEn.length) {
  fail.push("I18N.zh 与 I18N.en 的键集不一致：");
  onlyZh.forEach(k => fail.push(`  zh 有、en 缺: ${k}`));
  onlyEn.forEach(k => fail.push(`  en 有、zh 缺: ${k}`));
}

const missing = [...referenced].filter(k => !zhSet.has(k)).sort();
if (missing.length) {
  fail.push("以下键被引用但字典里没有定义（页面上会显示成裸键名）：");
  missing.forEach(k => fail.push(`  ${k}`));
}

const dead = zhKeys.filter(k => !isLive(k) && !isDynamic(k)).sort();

if (fail.length) {
  console.error("✗ i18n 键检查未通过：\n");
  fail.forEach(l => console.error("  " + l));
  console.error("");
  process.exit(1);
}

console.log(`✓ I18N.zh / I18N.en 键集一致（各 ${zhKeys.length} 键）`);
console.log(`✓ 被引用的键全部有定义（共 ${referenced.size} 个不同键）`);
if (dead.length > DEAD_BASELINE) {
  console.error(`\n✗ 死键 ${dead.length} 个，超出基线 ${DEAD_BASELINE}：\n`);
  dead.forEach(k => console.error("  " + k));
  console.error(`\n新增了键却没有引用它 —— 死键不允许上升（见文件头注释与裁定 R6）。`);
  console.error(`要么把新键用起来，要么删掉；只有确属有意保留（例如给下一个任务预留）`);
  console.error(`才上调 DEAD_BASELINE，并在提交信息里说明原因。`);
  process.exit(1);
}
console.log(`✓ 死键 ${dead.length} 个（基线 ${DEAD_BASELINE}，允许持平或下降）`);
if (dead.length < DEAD_BASELINE) {
  console.log(`  ↑ 已低于基线：可把 DEAD_BASELINE 下调到 ${dead.length} 以收紧这道防线。`);
}
if (dead.length) dead.forEach(k => console.log(`    · ${k}`));
