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
   数字来自实测，不是估计值；某轮把死键用起来之后，应把它下调到新的实测值。 */
const DEAD_BASELINE = 25;

/* 动态家族前缀：源码里存在 "wd." 这样的引号字面量，键由它拼出来
   （app.js 的 t("wd."+d)）。这类键无法被字面量扫描判活，只能按前缀整体放行。 */
const DYNAMIC_PREFIXES = ["wd."];

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
  console.error(`\n新增了键却没有引用它。要么用起来，要么删掉；`);
  console.error(`确实要保留（例如给下一个任务预留）就下调 DEAD_BASELINE 并说明原因。`);
  process.exit(1);
}
console.log(`✓ 死键 ${dead.length} 个（基线 ${DEAD_BASELINE}，允许持平或下降）`);
if (dead.length) dead.forEach(k => console.log(`    · ${k}`));
