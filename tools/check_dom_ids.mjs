#!/usr/bin/env node
/**
 * DOM id 交叉校验：扫 frontend/*.js 里的 $("id") 调用，
 * 检查该 id 是否在 frontend/index.html 中存在。
 *
 * 存在的意义：app.js 有大量解析时立即执行的 $("x").onclick = … 语句。
 * 删掉某个 DOM 元素却留下引用，会在浏览器里 null 抛错并静默中断该文件
 * 后续全部代码——症状是「好几个板块一起失灵」，很难定位。这个脚本让
 * 这类错误在改完就能被发现，而不是等到跑起来肉眼观察。
 *
 * 用法：node tools/check_dom_ids.mjs
 * 退出码：0 = 通过，1 = 有缺失
 */
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = join(ROOT, "frontend");

const jsFiles = readdirSync(FRONTEND).filter(f => f.endsWith(".js"));

const html = readFileSync(join(FRONTEND, "index.html"), "utf8");
// 收集 HTML 里的所有 id（负向后顾排除 data-id= / data-target= 这类同后缀属性）
const htmlIds = new Set([...html.matchAll(/(?<![\w-])id\s*=\s*"([^"$]+)"/g)].map(m => m[1]));

// 有些元素不是写在 index.html 里，而是 JS 用模板字符串动态建的
// （例：app.js 里的 <button id="btnSummarize">）。它们运行时才进 DOM，
// 只扫 HTML 会把对它们的 $("id") 误报成「缺失」，所以一并收进来。
// 同样用负向后顾排除 data-id= / data-cid= 这类属性；值里带 $ 的
// （如 data-id="${c.id}"）是模板表达式而非字面 id，也被 [^"$] 挡掉。
const jsIds = new Set();
for (const file of jsFiles) {
  const src = readFileSync(join(FRONTEND, file), "utf8");
  for (const m of src.matchAll(/(?<![\w-])id\s*=\s*"([^"$]+)"/g)) jsIds.add(m[1]);
}

const allIds = new Set([...htmlIds, ...jsIds]);

const missing = [];
for (const file of jsFiles) {
  const src = readFileSync(join(FRONTEND, file), "utf8");
  src.split("\n").forEach((line, i) => {
    // 只匹配 $("id") —— $$ 是选择器，不走 id 查找
    for (const m of line.matchAll(/(?<!\$)\$\("([A-Za-z_][\w-]*)"\)/g)) {
      const id = m[1];
      if (!allIds.has(id)) missing.push(`${file}:${i + 1}  缺失 id: ${id}`);
    }
  });
}

if (missing.length) {
  console.error("✗ 以下 $(\"id\") 引用在 frontend/index.html 里找不到对应元素：\n");
  missing.forEach(l => console.error("  " + l));
  console.error(`\n共 ${missing.length} 处。删元素时必须同步改掉引用它的语句，`);
  console.error("否则浏览器里会 null 抛错并中断该文件后续全部代码。");
  process.exit(1);
}
console.log(`✓ 所有 $("id") 引用都能在 index.html 中找到（HTML 共 ${htmlIds.size} 个 id）`);
