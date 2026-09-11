
/* ===== 桌面窗口壳桥（pywebview 内嵌窗口模式）=====
   窗口里没有“新标签页”：外部 http(s) 链接 / target=_blank / window.open
   交给系统默认浏览器（pywebview 注入的 window.pywebview.api.openExternal）。
   普通浏览器访问 localhost 时无桥 → 本块完全不起作用，保持原浏览器行为。 */
(function(){
  function shellApi(){
    try {
      const a = window.pywebview && window.pywebview.api;
      return (a && typeof a.openExternal === "function") ? a : null;
    } catch (e) { return null; }
  }
  window.CanvBridgeShell = {
    isWebview(){ return !!shellApi(); },
    openExternal(url){
      const a = shellApi();
      if (a && url && /^https?:/i.test(url)){ try { a.openExternal(url); return true; } catch (e) {} }
      return false;
    }
  };
  const nativeOpen = window.open.bind(window);
  window.open = function(url){
    if (url && window.CanvBridgeShell.openExternal(url)) return null;   // 壳接管 → 系统浏览器
    return nativeOpen.apply(window, arguments);                          // 无桥 → 原样
  };
  document.addEventListener("click", (e)=>{                              // 捕获阶段，先于业务 handler
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const el = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!el) return;
    const href = el.getAttribute("href") || "";
    if (href.charAt(0) === "#") return;                                  // 页内锚点交回原逻辑
    let abs = "";
    try { abs = new URL(href, location.href).href; } catch (err) { return; }
    const external = new URL(abs).origin !== location.origin || el.target === "_blank";
    if (!external) return;
    if (window.CanvBridgeShell.openExternal(abs)) e.preventDefault();    // 有桥并接管→阻止默认；无桥→放行
  }, true);
})();

/* 外观主题：light / dark / system（跟随系统），写 localStorage 的 sc_theme。
   首绘前的解析由 index.html 头部内联脚本负责，这里负责持久化 + 实时跟随系统 + 下拉同步。 */
const THEME_KEY = "sc_theme";
const _themeMQ = window.matchMedia("(prefers-color-scheme: dark)");
let themeMode = "dark";
try { const _v = localStorage.getItem(THEME_KEY); if (_v === "light" || _v === "dark" || _v === "system") themeMode = _v; } catch (e) {}
function applyThemeAttr(){
  const dark = themeMode === "dark" || (themeMode === "system" && _themeMQ.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  // 沙箱 iframe 是独立文档，拿不到父页面的 :root 变量，配色要单独同步一次
  // （函数声明提升，所以即便本文件靠后定义，这里也调得到）
  refreshRichFrames();
}
function _themeMQChanged(){ if (themeMode === "system") applyThemeAttr(); }
function applyTheme(mode){
  if (mode !== "light" && mode !== "dark" && mode !== "system") mode = "system";
  themeMode = mode;
  try { localStorage.setItem(THEME_KEY, mode); } catch (e) {}
  applyThemeAttr();
  if (mode === "system") _themeMQ.addEventListener("change", _themeMQChanged);
  else _themeMQ.removeEventListener("change", _themeMQChanged);
}
applyTheme(themeMode);

function defaultRange(){
  const now = new Date();
  const start = new Date(now); start.setDate(now.getDate()-7);
  $("inpStart").value = fmt(start);
  $("inpEnd").value = fmt(now);
}
function range(){
  const st=$("inpStart").value, en=$("inpEnd").value;
  if(!st || !en){ setStatus(t("status.need_date"),"err"); return null; }
  if(st>en){ setStatus(t("status.date_invalid"),"err"); return null; }
  return { start_date:st, end_date:en };
}

function fillProfessorFilter(){
  const sel = $("selProfessor");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = t("schedule.prof_all"); sel.appendChild(all);
  // 每个「课程代码 + 教授」一个选项（同教授教多门课则各一项），value 编码两者
  const seen = new Set();
  (banwebSchedule.courses || []).forEach(c => {
    const p = (c.primary_instructor || "").trim();
    if (!p) return;
    const key = c.code + "" + p;
    if (seen.has(key)) return;
    seen.add(key);
    const o = document.createElement("option");
    o.value = key; o.textContent = `${c.code} · ${p}`;
    sel.appendChild(o);
  });
  const un = document.createElement("option");
  un.value = "__none__"; un.textContent = t("schedule.prof_unspecified"); sel.appendChild(un);
  sel.value = prev && [...sel.options].some(o => o.value === prev) ? prev : "";
}

/* 设置页：进入时同步一次控件状态（页面切换由 shell.js 的 switchPage 负责，这里不再切） */
function openSettings(){
  $("selTheme").value = themeMode;
  refreshAimsUi();
  fillIgnoreCourses();
}
$("selTheme").addEventListener("change", e => applyTheme(e.target.value));

/* 下载目录：点「浏览」弹系统文件夹选择框，选中后直接填入（取消则无操作） */
$("btnBrowseDir").onclick = async () => {
  const r = await api("pick_dir");
  if(r.ok && r.path){ $("downloadDir").value = r.path; saveSettings(); }
  else if(!r.cancelled) setStatus(t("status.pick_dir_fail") + (r.error||""), "err");
};

/* AIMS 自动登录（账号密码存本机钥匙串，由后端代为登录） */
let aimsAutoTried = false;   // 本次会话是否已尝试过自动登录（防 3s 轮询重复触发）
async function refreshAimsUi(){
  const el = $("aimsSavedHint"); if(!el) return;
  const r = await api("banweb/credentials/status", undefined, "GET");
  if(r.ok!==true){ el.textContent = t("status.aims_status_fail"); return; }
  if(r.has_credentials){
    $("aimsUsername").value = r.username;
    $("aimsPassword").value = "";
    $("btnClearAims").hidden = false;
    el.textContent = t("status.aims_saved", {u: r.username});
  } else {
    $("btnClearAims").hidden = true;
    el.textContent = t("status.aims_not_saved");
  }
}
async function runAutoLogin(showBusy){
  aimsAutoTried = true;
  const attempt = async () => {
    const r = await api("banweb/auto_login");
    const loginBtn=$("btnBanwebLogin");
    if(r.ok !== true){
      setStatus(t("status.aims_login_fail") + (r.error||""), "err", 8000);
      setBanwebStatusText(t("status.aims_need_login_after_fail"), "err");
      if(loginBtn) loginBtn.hidden=false;   // 立即给出手动登录入口
      startBanwebPoll();
      return false;
    }
    // 自动登录成功 → 直接加载学期，不再慢速重查 status（省一次抓取）
    aimsAutoTried = false;
    if(loginBtn) loginBtn.hidden=true;
    setBanwebStatusText(t("status.banweb_ok"),"ok");
    stopBanwebPoll();
    loadTerms();   // 已登录 → 刷新学期（有缓存也刷新一次，保证最新）
    return true;
  };
  return showBusy ? withBusy(t("status.aims_logging_in"), $("btnSaveAims"), attempt) : attempt();
}
$("btnSaveAims").onclick = async () => {
  const username = $("aimsUsername").value.trim();
  const password = $("aimsPassword").value;
  if(!username || !password){ setStatus(t("status.aims_need_both"), "err"); return; }
  await withBusy(t("status.aims_saving"), $("btnSaveAims"), async ()=>{
    const r = await api("banweb/credentials", { username, password });
    if(r.ok !== true){ setStatus(t("status.aims_save_fail") + (r.error||""), "err"); return; }
    setStatus(t("status.aims_saved_ok"), "ok", 4000);
    await refreshAimsUi();
    await runAutoLogin(true);   // 保存后立即自动登录
  });
};
$("btnClearAims").onclick = async () => {
  await withBusy(t("status.aims_clearing"), $("btnClearAims"), async ()=>{
    const r = await api("banweb/credentials", undefined, "DELETE");
    if(r.ok !== true){ setStatus(t("status.aims_clear_fail") + (r.error||""), "err"); return; }
    $("aimsUsername").value=""; $("aimsPassword").value="";
    setStatus(t("status.aims_cleared"), "ok");
    await refreshAimsUi();
  });
};

/* 课程详情弹层 */
let assignmentMarks = {};        // {course_id: [未截止作业]}，周视图标注与详情共用
let showAssignments = true;      // 周视图作业标注显隐开关（隐藏时数据保留）
let detailCourse = null;         // {id, name, syllabus_text, teachers}
let detailAssignments = [];      // 当前打开课程的作业列表
let detailMeetings = [];         // 从课表打开的详情：该课程的 Banweb meetings（含完整地点）
let detailSummary = "";          // 已生成的 AI 总结（切语言后仍显示）
let detailModules = null;        // 课程 Modules（null=未取到/不适用，[]=确实没有）
let detailModulesError = "";     // Modules 拉取失败信息（有值时展示弱提示）
let detailSylEvents = [];        // 总结提取的日历事件（勾选 → 写入日历）
let detailSylReminders = [];     // 总结提取的提醒（勾选 → 写入提醒列表）
let detailBanweb = null;         // 从课表打开的详情：Banweb 课程块（code/section/crn/credits/course）
let detailPages = null;        // 该课程的 Pages 列表 | null = 尚未取
let detailPagesError = "";
/* Page 正文本地缓存：键 `${course_id}:${page_url}`，会话内不失效。
   内存态，不落 localStorage —— 刷新页面即清空。 */
const pageBodyCache = {};

function fmtDue(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${String(d.getMonth()+1).padStart(2,"0")}/${String(d.getDate()).padStart(2,"0")} ` +
         `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
}
async function ensureAssignments(courseIds){
  const missing = courseIds.filter(id => !(id in assignmentMarks));
  if (!missing.length) return null;                       // 已加载 → 不动
  const s = settings();
  const r = await api("assignments", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                       course_ids:missing });
  if (r.ok !== true) throw new Error(r.error || t("status.assignments_fail"));
  Object.keys(r.by_course || {}).forEach(k => { assignmentMarks[Number(k)] = r.by_course[k] || []; });
  return r;                                               // 含 errors，供调用方提示
}
function matchCourseByCode(code){
  // 只看「字母简称 + 4 位数字」，忽略 a/c 等后缀：CS1315A 与 CS1315 视为同一课程。
  // 两侧都抽取该模式再等值比较，避免 includes 被后缀字母卡死。
  const m = String(code||"").toUpperCase().match(/([A-Z]+\d{4})/);
  const target = m ? m[1] : "";
  if (target){
    return courseList.find(c => {
      const srcs = [c.course_code, c.name].filter(Boolean).join(" ");
      return (String(srcs).toUpperCase().match(/[A-Z]+\d{4}/g) || []).includes(target);
    }) || null;
  }
  // 课程号不含「字母+4位数字」模式时退回旧的子串匹配，行为不退化
  const norm = s => String(s).toUpperCase().replace(/\s+/g, "");
  const t2 = norm(code);
  if (!t2) return null;
  return courseList.find(c => norm(c.name).includes(t2)) || null;
}
function closeDetail(){ closeModulePop(); $("detailModal").hidden = true; }
$("btnCloseDetail").onclick = closeDetail;
$("detailModal").querySelector(".modal-backdrop").addEventListener("click", closeDetail);
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (modulePop && !modulePop.hidden){ closeModulePop(); return; }
  if (!$("detailModal").hidden) closeDetail();
});

/* 拉课程 Pages 列表（不含正文）。失败只影响该板块，不拖垮 Modules / 文件。 */
async function fetchDetailPages(canvasId){
  try {
    const s = settings();
    const r = await api("pages", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                   course_id: canvasId });
    detailPages = Array.isArray(r.pages) ? r.pages : [];
    detailPagesError = r.ok === true ? "" : (r.error || "");
  } catch (e) {
    detailPages = [];
    detailPagesError = String((e && e.message) || e);
  }
}

/* ===== Canvas 富内容（syllabus / Page 正文）的沙箱渲染 =====
   Canvas 给的是原始 HTML（表格、列表、图片、站内链接），纯文本渲染会把它们全拍平 ——
   课程安排表、评分标准表这类内容只在 HTML 里。这里用 sandbox 的 iframe 还原。

   安全约束（改动前务必读完）：sandbox 只给 allow-same-origin，**永远不要加 allow-scripts**。
   「脚本不执行」是这套方案成立的前提；allow-same-origin 只是为了让父页面能读
   contentDocument 去挂链接拦截和换主题样式 —— 一旦同时给了 allow-scripts，
   iframe 就拿到了与本应用同源的脚本执行权，等于把 XSS 面开给 Canvas 侧任意内容。
   同理：原始 HTML 只许进 srcdoc，**不许拼进主文档的 innerHTML**。 */
function richThemeCss(){
  const cs = getComputedStyle(document.documentElement);
  const v = (n, fb) => (cs.getPropertyValue(n).trim() || fb);
  const dark = document.documentElement.dataset.theme === "dark";
  const border = v("--border", "#e5e6ea"), s2 = v("--surface-2", "#f1f2f4");
  return `
    html { color-scheme:${dark ? "dark" : "light"}; }
    body { margin:0; padding:10px 12px; background:transparent; overflow-wrap:anywhere;
      font:13px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif;
      color:${v("--ink", "#1a1d23")}; }
    a { color:${v("--link", "#2563eb")}; }
    img { max-width:100%; height:auto; }
    h1,h2,h3,h4,h5,h6 { font-size:14px; margin:12px 0 6px; }
    p { margin:8px 0; }
    ul,ol { margin:8px 0; padding-left:22px; }
    table { border-collapse:collapse; width:100%; margin:8px 0; font-size:12.5px; }
    th,td { border:1px solid ${border}; padding:6px 8px; text-align:left; vertical-align:top; }
    th { background:${s2}; font-weight:600; }
    code,pre { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
    pre { background:${s2}; padding:8px 10px; border-radius:6px; overflow:auto; }
    blockquote { margin:8px 0; padding:4px 12px; border-left:3px solid ${border};
      color:${v("--muted", "#6b7280")}; }
    hr { border:none; border-top:1px solid ${border}; margin:12px 0; }`;
}

/* 相对链接与图片（Canvas 大纲里很常见）要按 Canvas 站点解析，而不是本应用地址 */
function richFrameDoc(rawHtml){
  let base = "";
  try {
    const u = settings().canvas_url;
    if (/^https?:\/\//i.test(u)) base = `<base href="${escAttr(u.replace(/\/+$/, "") + "/")}">`;
  } catch (e) {}
  return `<!doctype html><html><head><meta charset="utf-8">${base}` +
         `<style id="cb-theme">${richThemeCss()}</style></head><body>${rawHtml}</body></html>`;
}

/* 用 srcdoc 而不是拼进主文档 —— 属性转义一份长 HTML 文档极易出错 */
function setRichFrame(frame, rawHtml){
  if(!frame || !rawHtml){ return; }
  frame.setAttribute("sandbox", "allow-same-origin");   // 绝不加 allow-scripts，见上方注
  frame.srcdoc = richFrameDoc(rawHtml);
  const onload = () => {
    // srcdoc 之前 iframe 会先加载一次 about:blank —— 用 cb-theme 认出真正那一次
    let d = null;
    try { d = frame.contentDocument; } catch (e) { return; }
    if(!d || !d.body || !d.getElementById("cb-theme")){ return; }
    frame.removeEventListener("load", onload);
    wireRichFrameLinks(frame);
  };
  frame.addEventListener("load", onload);
}

/* iframe 内的点击不会冒泡到主文档，app.js 顶部那个外链桥（挂父文档捕获阶段）够不着它，
   必须伸进 contentDocument 自己挂一个 —— 否则点链接会把 Canvas 页面加载进这个小框。 */
function wireRichFrameLinks(frame){
  let d = null;
  try { d = frame.contentDocument; } catch (e) { return; }   // 跨源或不允许 → 放弃拦截
  if(!d){ return; }
  d.addEventListener("click", (e) => {
    const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if(!a){ return; }
    const href = a.getAttribute("href") || "";
    if(href.charAt(0) === "#"){ return; }                    // 页内锚点留给框内自己滚
    let abs = "";
    try { abs = new URL(href, d.baseURI).href; } catch (err) { return; }   // baseURI 认 <base>
    if(!/^https?:/i.test(abs)){ return; }
    e.preventDefault();                                      // 这个框只用来展示，不导航
    window.open(abs, "_blank", "noopener");                  // 有壳桥时被接管 → 系统浏览器
  }, true);
}

/* 渲染后调用：把容器里那个占位 iframe 装上内容。rawHtml 为空则不接管，保留纯文本兜底。 */
function mountRichFrame(container, rawHtml){
  if(!container || !rawHtml){ return; }
  setRichFrame(container.querySelector("iframe.rich-frame"), rawHtml);
}

/* 主题切换时只替换 iframe 里那段 <style>，不重设 srcdoc —— 重设会重新加载并丢掉滚动位置 */
function refreshRichFrames(){
  $$("iframe.rich-frame").forEach(f => {
    try {
      const st = f.contentDocument && f.contentDocument.getElementById("cb-theme");
      if(st){ st.textContent = richThemeCss(); }
    } catch (e) {}
  });
}

/* 展开单条 Page 时才拉正文；pageBodyCache 命中则直接渲染，不发请求。 */
async function loadPageBody(courseId, pageUrl, hostEl){
  const key = `${courseId}:${pageUrl}`;
  if(!(key in pageBodyCache)){
    hostEl.innerHTML = `<div class="muted">${t("courses.page_loading")}</div>`;
    const s = settings();
    try {
      const r = await api("page_body", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                         course_id:courseId, page_url:pageUrl });
      pageBodyCache[key] = r.ok === true ? { page: r.page } : { error: r.error || "" };
    } catch (e) {
      pageBodyCache[key] = { error: String((e && e.message) || e) };
    }
  }
  const hit = pageBodyCache[key];
  const pg = hit.error ? {} : (hit.page || {});
  hostEl.innerHTML = hit.error
    ? `<div class="muted">${t("courses.page_fail")}${esc(hit.error)}</div>`
    // 有原始 HTML 就交给沙箱 iframe（表格/图片/站内链接靠它还原），否则退回纯文本
    : pg.body_html
        // sandbox 写在标记里而不是只靠 setRichFrame：这样它从创建的第一刻就是沙箱，
        // 不存在「先无沙箱加载 about:blank、再补属性」的窗口
        ? `<iframe class="rich-frame" sandbox="allow-same-origin" title="${escAttr(pg.title || "")}"></iframe>`
        : `<div class="detail-syllabus">${esc(pg.body_text || "")}</div>`;
  mountRichFrame(hostEl, pg.body_html);
}

async function openCourseDetail(canvasId, banwebCourse){
  detailBanweb = banwebCourse || null;
  detailMeetings = detailBanweb ? (detailBanweb.meetings || []) : [];
  detailSummary = "";
  detailModules = null;
  detailModulesError = "";
  detailPages = null;
  detailPagesError = "";
  detailSylEvents = [];
  detailSylReminders = [];
  detailCourse = null;
  detailAssignments = [];
  const s = settings();
  if (canvasId && s.canvas_url && s.canvas_token) {
    const r = await api("course_detail", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                           course_id:canvasId });
    if (r.ok === true){
      detailCourse = r.course;                     // 失败则退化为仅 Banweb 数据
      detailModules = Array.isArray(r.modules) ? r.modules : null;
      detailModulesError = r.modules_error || "";
    }
    // 两个请求并发；allSettled 保证任一失败也等另一个落地，页面区不会整块消失
    await Promise.allSettled([ensureAssignments([canvasId]), fetchDetailPages(canvasId)]);
    detailAssignments = Array.isArray(assignmentMarks[canvasId]) ? assignmentMarks[canvasId] : [];
  }
  renderDetail();
  $("detailModal").hidden = false;
}
function parseDateLine(s){
  const m = String(s).match(/([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})/);
  if (!m) return null;
  const MON = {Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11};
  if (MON[m[1]] == null) return null;
  return new Date(+m[3], MON[m[1]], +m[2]);
}
function coursePace(meetings){
  /* 由各 meeting 的 range（如 "Sep 1, 2026 - Nov 28, 2026"）取最小起 / 最大止，估算持续周数与剩余周数。 */
  let min = null, max = null;
  for (const m of meetings) {
    const parts = String(m.range || "").split(" - ");
    if (parts.length !== 2) continue;
    const a = parseDateLine(parts[0]), b = parseDateLine(parts[1]);
    if (!a || !b) continue;
    if (!min || a < min) min = a;
    if (!max || b > max) max = b;
  }
  if (!min || !max) return null;
  const WEEK = 7 * 86400000;
  const total = Math.max(1, Math.round((max - min) / WEEK));
  const today = new Date(); today.setHours(0,0,0,0);
  const left = Math.max(0, Math.ceil((max - today) / WEEK));
  return { start: min, end: max, total, left };
}
function renderDetail(){
  if (!detailCourse && !detailBanweb) return;
  const c = detailCourse, bw = detailBanweb;
  const head = esc((c && c.name) || (bw && (bw.course || (bw.code + " " + bw.section))) || "");
  // ① 课程信息补全：code/section · CRN · 学分
  const bwMeta = bw
    ? [esc(bw.code + " " + bw.section),
       bw.crn ? "CRN " + esc(bw.crn) : "",
       bw.credits ? esc(bw.credits) + " " + t("detail.credits") : ""].filter(Boolean).join(" · ")
    : "";
  // ② 学期节奏：起止日期 · 共 N 周 · 还剩 N 周
  const pace = detailMeetings.length ? coursePace(detailMeetings) : null;
  const paceHtml = pace
    ? `<div class="detail-pace">${fmtMD(pace.start)} → ${fmtMD(pace.end)} · ${esc(t("detail.pace_weeks", {total: pace.total, left: pace.left}))}</div>`
    : "";
  // ④ 课程快捷入口：Canvas 首页 / 文件 / 作业
  const s = settings();
  const links = c && s.canvas_url
    ? (() => {
        const base = s.canvas_url.replace(/\/+$/, "");
        const list = [
          {label: t("detail.link_home"), href: `${base}/courses/${c.id}`},
          {label: t("detail.link_files"), href: `${base}/courses/${c.id}/files`},
          {label: t("detail.link_assignments"), href: `${base}/courses/${c.id}/assignments`},
        ];
        return `<div class="detail-links">` +
          list.map(x => `<a href="${escAttr(x.href)}" target="_blank" rel="noopener">${esc(x.label)}</a>`).join("") +
          `</div>`;
      })()
    : "";
  // ⑤ Canvas Modules：模块名 + 可点 items（每项超链接到 Canvas）
  const modulesHtml = (c && (detailModules || detailModulesError))
    ? `<div class="detail-section"><div class="sub-label">${t("detail.modules")}</div>` +
      (detailModulesError
        ? `<div class="muted">${t("detail.modules_fail")}${esc(detailModulesError)}</div>`
        : !detailModules.length
          ? `<div class="muted">${t("detail.no_modules")}</div>`
          : detailModules.map(m =>
              `<div class="detail-module" data-module="${escAttr(m.name)}"><div class="detail-module-title">${esc(m.name)}</div>` +
              m.items.map(moduleItemHtml).join("") +
              `</div>`).join("")) +
      `</div>`
    : "";
  // ⑦ Canvas Pages：列表来自 /api/pages；正文展开时才拉（懒加载 + 内存缓存）
  const pagesHtml = (c && (detailPages || detailPagesError))
    ? `<div class="detail-section"><div class="sub-label">${t("courses.pages")}</div>` +
      (detailPagesError
        ? `<div class="muted">${t("courses.pages_fail")}${esc(detailPagesError)}</div>`
        : !detailPages.length
          ? `<div class="muted">${t("courses.pages_empty")}</div>`
          : detailPages.map(p =>
              `<div class="detail-page-row" data-cid="${c.id}" data-purl="${escAttr(p.url)}">
                 <div class="detail-page-head"><span class="detail-page-caret">▸</span>${esc(p.title || p.url)}${p.front_page ? ` <span class="chip">${t("courses.page_front")}</span>` : ""}</div>
                 <div class="detail-page-body" hidden></div>
               </div>`).join("")) +
      `</div>`
    : "";
  const profs = (c && c.teachers ? c.teachers : []).map(x => `<span class="chip">${esc(x)}</span>`).join("");
  const profLine = profs ? `<div class="detail-prof">${t("detail.teachers")}: ${profs}</div>` : "";
  // ③ 每节谁上课：instr 带 (P) 标记为「主讲」
  const loc = detailMeetings.length
    ? `<div class="detail-section"><div class="sub-label">${t("detail.location")}</div>` +
      detailMeetings.map(m => {
        const isP = /\(P\)/.test(m.instr || "");
        const instr = (m.instr || "").replace(/\s*\(P\)\s*$/, "").trim();
        const instrHtml = instr
          ? `<span class="detail-instr">${isP ? ` · ${esc(t("detail.primary"))} ` : " · "}${esc(instr)}</span>`
          : "";
        return `<div class="detail-loc">
          <div class="item-title">${esc(m.room || "")}</div>
          <div class="file-path">${esc([m.type, m.days, m.time, m.range].filter(Boolean).join(" · "))}${instrHtml}</div>
        </div>`;
      }).join("") + `</div>`
    : "";
  const summaryHtml = detailSummary
    ? `<div class="detail-summary"><div class="sub-label">${t("detail.summary_label")}</div>
         ${esc(detailSummary)}</div>`
    : "";
  // ⑥ syllabus 提取出的可写事项：日历事件 + 提醒，勾选后经底部 write-bar 写入
  const sylExtractHtml = (detailSylEvents.length || detailSylReminders.length)
    ? `<div class="detail-extract">` +
      (detailSylEvents.length
        ? `<div class="sub-label">${t("announce.calendar_events")}（${detailSylEvents.length}）</div>` +
          detailSylEvents.map((e, ei) =>
            `<div class="item"><input type="checkbox" class="syl-ev" data-si="${ei}">
              <div><div class="item-title">${esc(e.title)}</div>
              <div class="file-path">${esc(e.start)} → ${esc(e.end)}${e.location ? ` · ${esc(e.location)}` : ""}</div></div></div>`).join("")
        : "") +
      (detailSylReminders.length
        ? `<div class="sub-label">${t("announce.reminders")}（${detailSylReminders.length}）</div>` +
          detailSylReminders.map((e, ei) =>
            `<div class="item"><input type="checkbox" class="syl-rm" data-si="${ei}">
              <div><div class="item-title">${esc(e.title)}</div>
              <div class="file-path">${t("announce.due")} ${esc(e.due_date)}</div></div></div>`).join("")
        : "") +
      `</div>`
    : "";
  let syl = "";
  if (c && (c.syllabus_html || c.syllabus_text)) {
    // 有原始 HTML 就交给沙箱 iframe（表格/图片/站内链接靠它还原），否则退回纯文本
    const sylBody = c.syllabus_html
      // sandbox 写在标记里而不是只靠 setRichFrame：这样它从创建的第一刻就是沙箱
      ? `<iframe class="rich-frame" sandbox="allow-same-origin" title="${escAttr(t("detail.syllabus"))}"></iframe>`
      : `<div class="detail-syllabus">${esc(c.syllabus_text)}</div>`;
    syl = `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         ${sylBody}
         <button id="btnSummarize" class="btn btn-ghost">${t("detail.summarize")}</button>
         ${summaryHtml}${sylExtractHtml}</div>`;
  } else if (c) {
    syl = `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="muted">${t("detail.no_syllabus")}</div></div>`;
  }
  const asg = c && detailAssignments.length
    ? detailAssignments.map(a => `
        <a class="assignment-row" href="${escAttr(a.html_url || "")}" target="_blank" rel="noopener">
          <div class="item-title">${esc(a.name)}</div>
          <div class="file-path">${a.due_at
              ? t("announce.due") + " " + esc(fmtDue(a.due_at))
              : t("detail.no_due")}${a.points_possible != null ? ` · ${esc(t("common.points", { n: a.points_possible }))}` : ""}</div>
        </a>`).join("")
    : c ? `<div class="muted">${t("detail.no_assignments")}</div>`
        : "";
  const banwebOnly = c ? "" : `<div class="detail-banweb-only">${t("detail.banweb_only")}</div>`;
  $("detailBody").innerHTML = `
    <div class="detail-head">${head}</div>
    ${bwMeta ? `<div class="detail-meta">${bwMeta}</div>` : ""}
    ${paceHtml}
    ${links}
    ${modulesHtml}
    ${pagesHtml}
    ${profLine}
    ${banwebOnly}
    ${loc}
    ${syl}
    ${c ? `<div class="detail-section"><div class="sub-label">${t("detail.assignments")}</div>${asg}</div>` : ""}`;
  // 上面那串里 syllabus 那格是空 iframe 占位，内容在这里装（原始 HTML 不许拼进 innerHTML）
  mountRichFrame($("detailBody"), c && c.syllabus_html);
  // 有提取项才显示底部写入条（写日历/写提醒下拉与按钮在 index.html 静态区块）
  const wb = $("detailWriteBar");
  if (wb) wb.hidden = !(detailSylEvents.length || detailSylReminders.length);
}

/* ===== 未读徽标：跨会话“已看集合”，判定新公告/新待办（红点显示在页签右上角） ===== */
const SEEN_KEY = "sc_seen_items";
const SEEN_CAP = 2000;                     // 集合上限，超出丢弃最旧的已看记录
let seenSet = { announce: new Set(), todo: new Set() };
function announceKey(c, a){ return a && a.id != null ? `${c.course_id}:${a.id}`
  : `${c.course_id}:${a.posted_at || ""}:${(a.title || "").slice(0, 40)}`; }
function todoKey(it){ return it.id != null ? String(it.id)
  : `${it.type || "todo"}:${it.html_url || it.title || ""}`; }
function loadSeenSet(){
  let raw = null;
  try { raw = JSON.parse(localStorage.getItem(SEEN_KEY) || "null"); } catch (e) { raw = null; }
  seenSet = {
    announce: new Set(raw && Array.isArray(raw.announce) ? raw.announce : []),
    todo: new Set(raw && Array.isArray(raw.todo) ? raw.todo : []),
  };
}
function saveSeenSet(){
  const trim = set => { const a = [...set]; return a.length > SEEN_CAP ? a.slice(-SEEN_CAP) : a; };
  try { localStorage.setItem(SEEN_KEY, JSON.stringify({
    announce: trim(seenSet.announce), todo: trim(seenSet.todo) })); } catch (e) {}
}
loadSeenSet();
/* 当前拉到的公告/待办里还没看过的条数 */
function countNewAnnounce(){
  const s = seenSet.announce;
  return summaryResults.reduce((n, c) =>
    n + (c.announcements || []).filter(a => !s.has(announceKey(c, a))).length, 0);
}
function countNewTodo(){
  const s = seenSet.todo;
  return todoItems.reduce((n, it) => n + (s.has(todoKey(it)) ? 0 : 1), 0);
}
/* 把某域当前拉到的条目记入已看（仅在有新增时才写 localStorage） */
function markAnnounceSeen(){
  let ch = false;
  for (const c of summaryResults) for (const a of (c.announcements || [])) {
    const k = announceKey(c, a); if (!seenSet.announce.has(k)) { seenSet.announce.add(k); ch = true; } }
  if (ch) saveSeenSet();
}
function markTodoSeen(){
  let ch = false;
  for (const it of todoItems) { const k = todoKey(it); if (!seenSet.todo.has(k)) { seenSet.todo.add(k); ch = true; } }
  if (ch) saveSeenSet();
}

$("btnTest").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus(t("status.need_canvas"),"err"); return; }
  await withBusy(t("status.connecting"), $("btnTest"), async ()=>{
    const r=await api("test_connection", s);
    setStatus(r.ok ? t("status.connected", {n: r.courses.length}) : t("status.connect_fail")+(r.error||""), r.ok?"ok":"err");
  });
};
$("btnLoadCalendars").onclick = async () => {
  await withBusy(t("status.reading_cal"), $("btnLoadCalendars"), async ()=>{
    const [cal, list] = await Promise.all([api("calendars"), api("reminder_lists")]);
    fillSelect("selCalendar", cal.calendars||[]);
    fillSelect("selList", list.lists||[]);
    if(cal.ok===false) setStatus(t("status.cal_fail")+cal.error, "err");
    else if(list.ok===false) setStatus(t("status.list_fail")+list.error, "err");
    else setStatus(t("status.refreshed", {c:(cal.calendars||[]).length, l:(list.lists||[]).length}), "ok");
  });
};

let courseList=[], summaryResults=[], displayResults=[], fileCourses=[];
$("btnLoadCourses").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus(t("status.need_canvas_config"),"err"); return; }
  await withBusy(t("status.loading_courses"), $("btnLoadCourses"), async ()=>{
    const r=await api("courses", s);
    if(!r.ok){ setStatus(t("status.courses_fail")+r.error,"err"); return; }
    courseList=r.courses;
    renderCourseCheckboxes(courseList);
    if (typeof renderCourseHubList === "function") renderCourseHubList();   // 课程中心列表跟着更新
    setStatus(t("status.courses_loaded", {n: courseList.length}),"ok");
  });
  // 课程配置已搬进设置页：这里不再 switchPage("announce")。
  // 旧行为会让用户在设置页点一下加载就被弹去公告页，莫名其妙。
  // 公告同步改由这条自动路径补一次，且没有可选课程时不发请求、不弹错。
  if (selectedCourses().length) syncAnnouncements();
};
function selectedCourses(){ return [...document.querySelectorAll("#courseCheckboxes input:checked")].map(i=>Number(i.dataset.id)); }

/* 记住上次课程勾选（含取消勾选的），下次「加载课程」自动还原 */
const COURSE_SEL_KEY = "sc_courseChecked";
function savedCourseSel(){
  try {
    const v = JSON.parse(localStorage.getItem(COURSE_SEL_KEY) || "null");
    return Array.isArray(v) ? new Set(v) : null;   // null = 还没有记忆
  } catch (e) { return null; }
}
function saveCourseSel(){ localStorage.setItem(COURSE_SEL_KEY, JSON.stringify(selectedCourses())); }
/* 行尾操作按钮：详情（复用 openCourseDetail）/ 忽略（复用忽略机制）。
   用内联 t() 而非 data-i18n —— 动态插入的节点不会被 applyLang() 处理。 */
function courseRowActionsHtml(c){
  return `<span class="cr-actions">
    <button type="button" class="btn btn-ghost btn-xs cr-detail" data-id="${c.id}">${t("courses.detail")}</button>
    <button type="button" class="btn btn-ghost btn-xs cr-ignore" data-id="${c.id}">${t("courses.ignore")}</button>
  </span>`;
}
function renderCourseCheckboxes(courses){
  const saved = savedCourseSel();
  const ignored = savedCourseIgnored();
  const shown = ignored && ignored.size ? courses.filter(c => !ignored.has(c.id)) : courses;
  if(!shown.length){
    $("courseCheckboxes").innerHTML = `<span class="muted">${t("settings.ignore_all")}</span>`;
    return;
  }
  $("courseCheckboxes").innerHTML = shown.map(c => {
    const on = !saved || saved.has(c.id);           // 无记忆 / 新课程 → 默认勾选
    return `<label class="chip"><input type="checkbox"${on ? " checked" : ""} data-id="${c.id}"> ${esc(c.name)}${courseRowActionsHtml(c)}</label>`;
  }).join("");
}
/* 课程行「详情 / 忽略」委托：只在顶层绑定一次（放进 renderCourseCheckboxes 会随每次渲染叠加监听）。
   按钮落在勾选框 <label> 内，preventDefault + stopPropagation 防止连带切换勾选状态。 */
$("courseCheckboxes").addEventListener("click", async (e) => {
  const d = e.target.closest(".cr-detail");
  if (d){ e.preventDefault(); e.stopPropagation();
    openCourseDetail(Number(d.dataset.id)); return; }
  const ig = e.target.closest(".cr-ignore");
  if (ig){ e.preventDefault(); e.stopPropagation();
    const id = Number(ig.dataset.id);
    const cur = savedCourseIgnored() || new Set();
    if (!cur.has(id)) saveCourseIgnored([...cur, id]);
    fillIgnoreCourses();
    renderCourseCheckboxes(courseList);
    return; }
});
$("courseCheckboxes").addEventListener("change", e => {
  if (e.target && e.target.matches("input[data-id]")) { saveCourseSel(); scheduleAutoSync(); }
});

/* 忽略课程：在设置里勾选即忽略 —— 从顶部课程区隐藏、永不自动勾选 */
const COURSE_IGNORE_KEY = "sc_courseIgnored";
function savedCourseIgnored(){
  try {
    const v = JSON.parse(localStorage.getItem(COURSE_IGNORE_KEY) || "null");
    return Array.isArray(v) ? new Set(v) : null;   // null = 没有忽略
  } catch (e) { return null; }
}
function saveCourseIgnored(ids){
  localStorage.setItem(COURSE_IGNORE_KEY, JSON.stringify(ids));
  // 刚被忽略的课同步从「勾选记忆」里移除：以后恢复时默认不勾
  const checked = savedCourseSel();
  if (checked){
    const drop = new Set(ids);
    const next = [...checked].filter(id => !drop.has(id));
    localStorage.setItem(COURSE_SEL_KEY, JSON.stringify(next));
  }
}
function fillIgnoreCourses(){
  const box = $("ignoreCourses");
  if (!box) return;
  if (!courseList.length){
    box.innerHTML = `<span class="muted">${t("settings.ignore_empty")}</span>`;
    return;
  }
  const ignored = savedCourseIgnored() || new Set();
  box.innerHTML = courseList.map(c => {
    const on = ignored.has(c.id);
    return `<label class="chip"><input type="checkbox"${on ? " checked" : ""} data-id="${c.id}"> ${esc(c.name)}</label>`;
  }).join("");
}
$("ignoreCourses").addEventListener("change", e => {
  if (!e.target || !e.target.matches("input[data-id]")) return;
  const ids = [...document.querySelectorAll("#ignoreCourses input:checked")].map(i => Number(i.dataset.id));
  saveCourseIgnored(ids);
  if (courseList.length) renderCourseCheckboxes(courseList);  // 顶部课程区立即增删
  if (typeof renderCourseHubList === "function") renderCourseHubList();
});
$("btnClearIgnore").onclick = () => {
  localStorage.removeItem(COURSE_IGNORE_KEY);
  if (courseList.length) renderCourseCheckboxes(courseList);
  if (typeof renderCourseHubList === "function") renderCourseHubList();
  fillIgnoreCourses();
};

/* 同步公告：只拉原始公告，不做 AI 总结 */
async function syncAnnouncements(){
  const s=settings(), ids=selectedCourses();
  const rng=range(); if(!rng) return false;
  if(!ids.length){ setStatus(t("status.need_select_course"),"err"); return false; }
  const r=await api("sync_announcements", {
    canvas_url:s.canvas_url, canvas_token:s.canvas_token, course_ids:ids,
    start_date:rng.start_date, end_date:rng.end_date });
  if(!r.ok){ setStatus(t("status.sync_fail")+r.error,"err"); return false; }
  summaryResults=(r.courses||[]).map(c=>({
    course_id:c.course_id, course_name:c.course_name, announcements:c.announcements||[],
    _summarized:false, _summarizing:false,
    summaries:[], calendar_events:[], reminders:[], warning:"", error:"" }));
  renderSummaries();
  setStatus(t("status.sync_done", {n: summaryResults.length}),"ok");
  refreshBadges();          // 纯展示红点；公告“已读”等切走页签时记
  bgFetchTodoBadge();       // 启动首次同步后，后台顺带查一次待办（有守卫，只跑一次）
  return true;
}
/* 改日期范围 / 课程勾选 → 自动重同步公告（去抖 600ms，不遮罩、不切页签） */
let _autoSyncT = null;
function scheduleAutoSync(){
  clearTimeout(_autoSyncT);                                  // 作废先前排队；guard 不通过也清掉，避免悬空触发
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;               // 还没配 Canvas
  if(!courseList.length) return;                             // 还没加载过课程
  if(!selectedCourses().length) return;                      // 全不选时不打扰
  if($("btnLoadCourses").disabled) return;                   // 手动「加载课程+同步」进行中
  _autoSyncT = setTimeout(async ()=>{
    _autoSyncT = null;
    const st=$("inpStart").value, en=$("inpEnd").value;
    if(!st || !en || st>en) return;                          // 范围还没填好，等下一次
    await syncAnnouncements();
  }, 600);
}

/* 单门课程 AI 总结（手点） */
async function summarizeAnnouncement(orig){
  const c=summaryResults[orig];
  if(!c || c._summarizing) return;
  c._summarizing=true; c.error=""; renderSummaries();
  const s=settings();
  const r=await api("summarize_course", {
    canvas_url:s.canvas_url, canvas_token:s.canvas_token,
    llm_base_url:s.llm_base_url, llm_api_key:s.llm_api_key, llm_model:s.llm_model,
    course_id:c.course_id, course_name:c.course_name,
    announcements:c.announcements||[], language:LANG() });
  if(summaryResults[orig]!==c) return;            // 已重新同步 → 丢弃陈旧响应
  c._summarizing=false;
  if(r.ok!==true){ c.error=r.error||""; renderSummaries(); setStatus(t("status.summarize_fail")+(r.error||""),"err"); return; }
  c._summarized=true;
  c.summaries=Array.isArray(r.summaries)?r.summaries:[];
  c.calendar_events=r.calendar_events||[];
  c.reminders=r.reminders||[]; c.warning=r.warning||"";
  renderSummaries();
  setStatus(t("status.summarized", {n:c.course_name}),"ok");
}

function renderSummaries(){
  fillCourseFilter("selAnnounceCourse", summaryResults.map(c => c.course_name));
  const courseSel = $("selAnnounceCourse").value;
  let src = summaryResults;
  if (courseSel) src = summaryResults.filter(c => c.course_name === courseSel);
  displayResults = src.map((c) => ({ ...c, _orig: summaryResults.indexOf(c) }));
  if(!displayResults.length){
    $("summaries").innerHTML = `<div class="empty">${t("announce.empty")}</div>`;
    return;
  }
  $("summaries").innerHTML = displayResults.map((c,ci)=>{
    const orig=c._orig, st=summaryResults[orig];
    const evs=c.calendar_events||[], rms=c.reminders||[];
    const cvUrl=(settings().canvas_url||"").replace(/\/+$/,"");
    // 逐条公告：原文在下、该条 AI 总结紧随其后（.ai-summary 与原文样式区分）；
    // 未总结时只显示原文
    const anns=st.announcements||[];
    const sums=st._summarized ? (Array.isArray(st.summaries)?st.summaries:[]) : [];
    const itemsHtml = anns.length ? anns.map((a,i)=>{
        const ai = sums[i] && sums[i].trim()
          ? `<div class="ai-summary"><span class="ai-summary-tag">${esc(t("announce.ai_summary"))}</span>
              <div class="ai-summary-body">${esc(sums[i])}</div></div>`
          : "";
        const titleHtml = (cvUrl && a.id)
          ? `<a class="announce-link" href="${escAttr(cvUrl)}/courses/${escAttr(c.course_id)}/announcements/${escAttr(a.id)}" target="_blank" rel="noopener">${esc(a.title)}</a>`
          : esc(a.title);
        const isNew = !seenSet.announce.has(announceKey(c, a));    // 与页签红点同口径：跨会话没读过
        const newTag = isNew ? `<span class="badge-new">${esc(t("announce.new_tag"))}</span>` : "";
        return `<div class="item${isNew ? " is-new" : ""}"><div><div class="item-title">${newTag}${titleHtml} <span class="muted">${esc((a.posted_at||"").slice(0,10))}</span></div>
          <div class="announce-msg-wrap"><div class="announce-msg"><div class="announce-msg-inner">${esc(a.message)}</div></div>
            <button class="btn-announce-expand" hidden>${t("announce.expand")}</button></div>
          ${ai}</div></div>`;
      }).join("")
      : `<div class="muted" style="padding:4px 0 8px">${t("announce.no_announce")}</div>`;
    // AI 提取出的可写事项：事件 / 提醒。整门课聚合一份，有结果即以折叠块显示
    // （默认收起），展开勾选可写回；下标与 c.calendar_events / c.reminders 完整数组对齐
    const extractBlock = (label, list, mk) => list.length
      ? `<details class="extract-block"><summary><span class="sub-label">${label}</span></summary>
          <div class="extract-body">${list.map(mk).join("")}</div></details>`
      : "";
    const eventsBlock = extractBlock(
      `${t("announce.calendar_events")}（${evs.length}）`, evs,
      (e, ei) => `<div class="item"><input type="checkbox" class="ev" data-ci="${ci}" data-ei="${ei}">
        <div><div class="item-title">${esc(e.title)}</div>
        <div class="file-path">${esc(e.start)} → ${esc(e.end)}${e.location ? ` · ${esc(e.location)}` : ""}</div></div></div>`);
    const remindersBlock = extractBlock(
      `${t("announce.reminders")}（${rms.length}）`, rms,
      (e, ei) => `<div class="item"><input type="checkbox" class="rm" data-ci="${ci}" data-ei="${ei}">
        <div><div class="item-title">${esc(e.title)}</div><div class="file-path">${t("announce.due")} ${esc(e.due_date)}</div></div></div>`);
    const body = `
      ${st.warning ? `<div style="color:var(--err);font-size:12.5px;margin-bottom:6px">${esc(st.warning)}</div>` : ""}
      ${itemsHtml}
      ${st._summarized ? `${eventsBlock}${remindersBlock}` : ""}`;
    // 每课操作按钮
    const actions = st._summarizing
      ? `<span class="btn btn-ghost" disabled>${t("announce.summarizing")}</span>`
      : st._summarized
        ? `<button class="btn btn-ghost btn-summarize" data-orig="${orig}">${t("announce.resummarize")}</button>`
        : `<button class="btn btn-primary btn-summarize" data-orig="${orig}">${t("announce.summarize")}</button>`;
    return `
    <div class="course-card">
      <div class="course-name">${c.course_id
          ? `<a href="#" class="course-detail-link" data-cid="${c.course_id}">${esc(c.course_name)}</a>`
          : esc(c.course_name)}
        <span class="course-actions">${actions}</span></div>
      ${body}
    </div>`;
  }).join("");
  wireAnnounceExpands();
}
/* 公告消息超出折叠高度才显示「展开」按钮（line-clamp 会钳住内部高度，用脱离文档的探针量真实高度） */
function wireAnnounceExpands(){
  document.querySelectorAll("#summaries .announce-msg").forEach(msg=>{
    const btn=msg.parentElement.querySelector(".btn-announce-expand");
    if(!btn) return;
    const cs=getComputedStyle(msg);
    const probe=document.createElement("div");
    probe.style.cssText="position:absolute;visibility:hidden;pointer-events:none;left:-9999px;top:0;"
      +`white-space:pre-wrap;width:${msg.clientWidth}px;font:${cs.font};line-height:${cs.lineHeight};`
      +`word-break:${cs.wordBreak};letter-spacing:${cs.letterSpacing};`;
    probe.textContent=msg.textContent;
    document.body.appendChild(probe);
    const full=probe.offsetHeight;
    probe.remove();
    if(full > msg.clientHeight + 2) btn.hidden=false;
  });
}
$("selAnnounceCourse").addEventListener("change", renderSummaries);

$("summaries").addEventListener("click", (e) => {
  const sum = e.target.closest(".btn-summarize");
  if (sum){ summarizeAnnouncement(Number(sum.dataset.orig)); return; }
  const exp = e.target.closest(".btn-announce-expand");
  if (exp){
    const msg = exp.closest(".announce-msg-wrap").querySelector(".announce-msg");
    const expanded = msg.classList.toggle("expanded");
    exp.textContent = expanded ? t("announce.collapse") : t("announce.expand");
    return;
  }
  const link = e.target.closest(".course-detail-link");
  if (!link) return;
  e.preventDefault();
  openCourseDetail(Number(link.dataset.cid));
});
$("detailBody").addEventListener("click", async (e) => {
  const btn = e.target.closest("#btnSummarize");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = t("detail.summarizing");
  const s = settings();
  const cid = detailCourse.id;                     // 快照发起请求时的课程
  try {
    const r = await api("summarize_syllabus", {
      canvas_url:s.canvas_url, canvas_token:s.canvas_token,
      llm_base_url:s.llm_base_url, llm_api_key:s.llm_api_key, llm_model:s.llm_model,
      course_id:cid, language:LANG() });
    if (!detailCourse || detailCourse.id !== cid) return;   // 已切课/关弹层 → 丢弃陈旧响应
    if (r.ok !== true) setStatus(t("detail.summarize_fail") + (r.error || ""), "err");
    else {
      detailSummary = r.summary || "";
      detailSylEvents = Array.isArray(r.calendar_events) ? r.calendar_events : [];
      detailSylReminders = Array.isArray(r.reminders) ? r.reminders : [];
      renderDetail();
      if (detailSylEvents.length || detailSylReminders.length) ensureDetailWriteOpts();
    }
  } catch (err) {
    if (!detailCourse || detailCourse.id !== cid) return;
    setStatus(t("detail.summarize_fail") + (err.message || ""), "err");
  } finally {
    const b2 = $("btnSummarize");                       // 成功后已重渲，按钮是新元素
    if (b2 && detailCourse && detailCourse.id === cid) { b2.disabled = false; b2.textContent = t("detail.summarize"); }
  }
});
$("btnWriteCalendar").onclick = async () => {
  const cal=$("selCalendar").value;
  const amVal = $("selAlert").value ? Number($("selAlert").value) : null;
  if(!cal){ setStatus(t("status.need_calendar"),"err"); return; }
  const evs=[...document.querySelectorAll(".ev:checked")].map(i=>{
    const c=displayResults[Number(i.dataset.ci)]; return c.calendar_events[Number(i.dataset.ei)]; });
  if(!evs.length){ setStatus(t("status.no_event"),"err"); return; }
  await withBusy(t("status.writing_events", {n: evs.length}), $("btnWriteCalendar"), async ()=>{
    let n=0;
    for(const e of evs){
      const r=await api("add_calendar_event",{ calendar_name:cal, title:e.title, start:e.start,
        end:e.end, location:e.location||"", notes:e.notes||"", alert_minutes:amVal });
      if(r.ok) n++; else setStatus(t("status.write_fail")+r.error,"err");
    }
    const alertLabel = amVal ? t("status.alert_set") : "";
    setStatus(t("status.events_done", {a:n, b:evs.length})+alertLabel, n===evs.length?"ok":"err");
  });
};
$("btnWriteReminders").onclick = async () => {
  const list=$("selList").value;
  if(!list){ setStatus(t("status.need_list"),"err"); return; }
  const rms=[...document.querySelectorAll(".rm:checked")].map(i=>{
    const c=displayResults[Number(i.dataset.ci)]; return c.reminders[Number(i.dataset.ei)]; });
  if(!rms.length){ setStatus(t("status.no_reminder"),"err"); return; }
  await withBusy(t("status.writing_reminders", {n: rms.length}), $("btnWriteReminders"), async ()=>{
    let n=0;
    for(const e of rms){
      const r=await api("add_reminder",{ list_name:list, title:e.title, due_date:e.due_date, notes:e.notes||"" });
      if(r.ok) n++; else setStatus(t("status.write_fail")+r.error,"err");
    }
    setStatus(t("status.reminders_done", {a:n, b:rms.length}), n===rms.length?"ok":"err");
  });
};

/* 详情弹层 syllabus 提取结果写入条（日历 / 提醒列表），首次出现提取项时懒加载可选项 */
async function ensureDetailWriteOpts(){
  const cal = $("selDetailCal"), list = $("selDetailList");
  if (!cal || !list) return;
  if (cal.options.length > 0 && list.options.length > 0) return;   // 已加载过，保留当前选择
  const [cres, lres] = await Promise.all([api("calendars"), api("reminder_lists")]);
  if (cal.options.length === 0)
    fillSelect("selDetailCal", cres.ok === true ? (cres.calendars || []) : []);
  if (list.options.length === 0)
    fillSelect("selDetailList", lres.ok === true ? (lres.lists || []) : []);
}
$("btnDetailWriteCalendar").onclick = async () => {
  const cal = $("selDetailCal").value;
  const amVal = $("selDetailAlert").value ? Number($("selDetailAlert").value) : null;
  if(!cal){ setStatus(t("status.need_calendar"),"err"); return; }
  const evs=[...document.querySelectorAll("#detailModal .syl-ev:checked")].map(i =>
    detailSylEvents[Number(i.dataset.si)]);
  if(!evs.length){ setStatus(t("status.no_event"),"err"); return; }
  await withBusy(t("status.writing_events", {n: evs.length}), $("btnDetailWriteCalendar"), async ()=>{
    let n=0;
    for(const e of evs){
      const r=await api("add_calendar_event",{ calendar_name:cal, title:e.title, start:e.start,
        end:e.end, location:e.location||"", notes:e.notes||"", alert_minutes:amVal });
      if(r.ok) n++; else setStatus(t("status.write_fail")+r.error,"err");
    }
    const alertLabel = amVal ? t("status.alert_set") : "";
    setStatus(t("status.events_done", {a:n, b:evs.length})+alertLabel, n===evs.length?"ok":"err");
  });
};
$("btnDetailWriteReminders").onclick = async () => {
  const list = $("selDetailList").value;
  if(!list){ setStatus(t("status.need_list"),"err"); return; }
  const rms=[...document.querySelectorAll("#detailModal .syl-rm:checked")].map(i =>
    detailSylReminders[Number(i.dataset.si)]);
  if(!rms.length){ setStatus(t("status.no_reminder"),"err"); return; }
  await withBusy(t("status.writing_reminders", {n: rms.length}), $("btnDetailWriteReminders"), async ()=>{
    let n=0;
    for(const e of rms){
      const r=await api("add_reminder",{ list_name:list, title:e.title, due_date:e.due_date, notes:e.notes||"" });
      if(r.ok) n++; else setStatus(t("status.write_fail")+r.error,"err");
    }
    setStatus(t("status.reminders_done", {a:n, b:rms.length}), n===rms.length?"ok":"err");
  });
};

/* 模块 File 行：点名称弹「打开页面 / 打开文件」，点右侧 ⤓ 直接下载；非文件条目照旧直接打开 */
function moduleItemHtml(it){
  const href = it.url ? ` href="${escAttr(it.url)}" target="_blank" rel="noopener"` : "";
  if (!it.file_id) return `<a class="module-item"${href}>${esc(it.title)}</a>`;
  const name = it.url
    ? `<a class="module-file-link"${href} title="${escAttr(t("module.hint"))}">${esc(it.title)}</a>`
    : `<span class="module-file-link" title="${escAttr(t("module.hint"))}">${esc(it.title)}</span>`;
  return `<div class="module-item module-file" data-fid="${escAttr(it.file_id)}"
    data-title="${escAttr(it.title)}">
    ${name}
    <button type="button" class="module-dl-btn" title="${escAttr(t("module.download"))}"
      aria-label="${escAttr(t("module.download"))}">⤓</button>
  </div>`;
}
const modulePop = $("modulePop");
let modulePopCtx = null;               // {fid, pageUrl, title, moduleName}
function openModuleFilePop(itemEl){
  const fid = Number(itemEl.dataset.fid);
  if (!fid || !modulePop) return;
  const modEl = itemEl.closest(".detail-module");
  const link = itemEl.querySelector(".module-file-link");
  const pageUrl = link ? (link.getAttribute("href") || "") : "";
  modulePopCtx = {
    fid, pageUrl,
    title: itemEl.dataset.title || "",
    moduleName: modEl ? modEl.dataset.module : "",
  };
  $("modulePopTitle").textContent = modulePopCtx.title;
  $("modulePopTitle").title = t("file.open");
  const pageBtn = $("btnModuleOpenPage");
  pageBtn.hidden = !pageUrl;           // 无 Canvas 页面链接 → 只给「打开文件」
  if (pageUrl) pageBtn.textContent = t("module.open_page");
  if (window.CanvBridgeShell && window.CanvBridgeShell.isWebview()){
    // 内嵌窗口没有“新标签页内联 PDF”，所以「打开文件」在这里不做内联预览。
    // 但它不再是死按钮：经 openFileSmart 分叉后它落盘下载（见 Step 1），所以保持可见 ——
    // 隐藏它会让弹层里唯一带标签的文件入口消失，只剩标题上那个没有任何提示的点击。
    // 纯文件项（无 Canvas 链接）仍然直接下载并关掉弹窗，省一次点击。
    if (!pageUrl){ closeModulePop(); downloadModuleFile(itemEl); return; }
  }
  modulePop.hidden = false;            // 先显示再量尺寸，无闪动
  const r = itemEl.getBoundingClientRect();
  const pw = modulePop.offsetWidth || 220, ph = modulePop.offsetHeight || 120;
  const left = Math.max(8, Math.min(r.left, window.innerWidth - pw - 8));
  let top = r.bottom + 6;
  if (top + ph > window.innerHeight - 8) top = r.top - ph - 6;
  modulePop.style.left = left + "px";
  modulePop.style.top = Math.max(8, top) + "px";
}
function closeModulePop(){
  if (modulePop) modulePop.hidden = true;
  modulePopCtx = null;
}
/* ⤓ 下载（右侧键）：下载到 app 下载目录/课程/模块/文件。rowEl 为所在的 .module-file 行 */
async function downloadModuleFile(rowEl){
  if (!rowEl) return;
  const fid = Number(rowEl.dataset.fid), title = rowEl.dataset.title || "";
  if (!fid) return;
  const modEl = rowEl.closest(".detail-module");
  const moduleName = modEl ? modEl.dataset.module : "";
  const s = settings(), c = detailCourse;
  const btn = rowEl.querySelector(".module-dl-btn");
  await withBusy(t("module.downloading", {f: title}), btn, async () => {
    const r = await api("download_module_item", {
      canvas_url:s.canvas_url, canvas_token:s.canvas_token,
      download_dir:downloadDir(), course_id:c ? c.id : 0,
      course_name:(c && c.name) || "", module_name:moduleName, file_id:fid });
    if (modulePop && !modulePop.hidden) closeModulePop();
    if (r.ok !== true){ setStatus(t("module.fail") + (r.error || ""), "err"); return; }
    setStatus(r.saved ? t("module.saved") : t("module.downloaded", {p: r.dest_path}), "ok");
  });
}
/* ===== 文件直链：浏览器形态内联打开，桌面（内嵌窗口）形态退化为下载 =====
   三个调用点共用：侧栏文件页、课程 Modules 弹层、课程中心的文件子标签。
   注意 api()（util.js:26-33）无条件 await r.json()，二进制端点必须走裸 fetch。 */
async function openFileInline(courseId, fileId, name){
  const s = settings();
  let resp;
  try {
    resp = await fetch("/api/module_file_stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canvas_url: s.canvas_url, canvas_token: s.canvas_token,
                             course_id: courseId, file_id: fileId }),
    });
  } catch (e) {
    // 与 api()（util.js:26-28）同一约定：网络失败返回错误对象而不是抛出。
    // 三个调用点都不 await 本函数，抛出会变成未处理的 rejection。
    return { ok: false, error: t("status.backend_fail") };
  }
  const ctype = (resp.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
  // 后端出错时同样是 200 + JSON（main.py 约定），所以 Content-Type 是判据，不能只看 resp.ok
  if (!resp.ok || ctype === "application/json"){
    let msg = "";
    try { const j = await resp.json(); msg = j.error || ""; } catch (e) { /* 忽略解析失败 */ }
    return { ok: false, error: msg };
  }
  let blob;
  try { blob = await resp.blob(); }
  catch (e) { return { ok: false, error: t("status.backend_fail") }; }
  const url = URL.createObjectURL(blob);
  /* 这里**不能**拿 window.open 的返回值判断成败。HTML 规范在 window open 步骤末尾规定：
     "If noopener is true or windowType is 'new with no opener', then return null."
     —— 带 noopener 时返回值**无条件**为 null，与标签页是否真的打开无关。
     若写成 `const win = window.open(...); if (!win) return {ok:false}`，本函数将永远走降级
     分支，「浏览器内联打开」这个功能会整个消失、每次点击都变成下载 —— 而且所有静态门都是绿的。
     noopener 必须保留：Canvas 上的 .html 文件经 blob: 渲染后与本站同源，去掉它便可经
     window.opener 反向操控本页。代价是弹窗被拦时无法探测；这与既有的 btnModuleOpenPage
     （app.js:1173，同样的调用且忽略返回值）取舍一致，用户已接受。 */
  window.open(url, "_blank", "noopener");
  // objectURL 交给新打开的文档用；延迟释放避免新标签还没加载完就被回收
  setTimeout(() => { try { URL.revokeObjectURL(url); } catch (e) {} }, 60000);
  return { ok: true };
}

async function downloadFileTo(courseId, fileId, name, courseName, moduleName){
  const s = settings();
  const r = await api("download_module_item", {
    canvas_url: s.canvas_url, canvas_token: s.canvas_token,
    download_dir: downloadDir(), course_id: courseId,
    course_name: courseName || "", module_name: moduleName || "", file_id: fileId });
  if (r.ok !== true) return { ok: false, error: r.error || "" };
  return { ok: true, dest: r.dest_path || "", saved: !!r.saved };
}

/* 统一入口：按形态分叉 + 失败自动降级。
   用户点文件名要的是"拿到文件"，所以内联失败时报错之余还要替他下载一次，
   而不是只丢一句红字让他自己再找下载按钮。 */
async function openFileSmart(courseId, fileId, name, courseName, moduleName, btnEl){
  if (!fileId){ setStatus(t("file.open_fail"), "err"); return; }
  /* 三个调用点都不 await 本函数（点一下就返回），所以这里必须自己兜住所有异常：
     withBusy 只有 try/finally、没有 catch（util.js:34-40），抛出去就成了未处理的 rejection ——
     遮罩消失、没有提示、降级下载也不会跑，正是"点了没反应"那种失败。 */
  try {
    await withBusy(t("file.opening", {f: name}), btnEl || null, async () => {
      if (window.CanvBridgeShell && window.CanvBridgeShell.isWebview()){
        const r = await downloadFileTo(courseId, fileId, name, courseName, moduleName);
        if (!r.ok){ setStatus(t("file.open_fail") + (r.error || ""), "err"); return; }
        setStatus(r.saved ? t("file.downloaded_saved", {f: name})
                          : t("file.downloaded", {p: r.dest}), "ok");
        return;
      }
      const r = await openFileInline(courseId, fileId, name);
      if (r.ok) return;
      setStatus(t("file.open_fail") + (r.error || ""), "err");
      const d = await downloadFileTo(courseId, fileId, name, courseName, moduleName);
      if (d.ok) setStatus(t("file.downloaded", {p: d.dest}), "ok");
    });
  } catch (e) {
    setStatus(t("file.open_fail") + (e && e.message ? e.message : ""), "err");
  }
}
$("detailBody").addEventListener("click", (e) => {
  const dl = e.target.closest(".module-dl-btn");
  if (dl){
    e.preventDefault(); e.stopPropagation();
    downloadModuleFile(dl.closest(".module-item.module-file"));
    return;
  }
  const mf = e.target.closest(".module-item.module-file");
  if (!mf){
    if (modulePop && !modulePop.hidden && !e.target.closest("#modulePop")) closeModulePop();
    return;
  }
  e.preventDefault(); e.stopPropagation();
  openModuleFilePop(mf);
});
/* Pages 条目展开：展开时才拉正文；缓存命中则直接渲染（二次展开不重复请求） */
$("detailBody").addEventListener("click", (e) => {
  const head = e.target.closest(".detail-page-head");
  if(!head) return;
  const row = head.parentElement;
  const body = row && row.querySelector(".detail-page-body");
  if(!body) return;
  const opening = body.hidden;
  body.hidden = !opening;
  const caret = head.querySelector(".detail-page-caret");
  if(caret) caret.textContent = opening ? "▾" : "▸";
  if(opening) loadPageBody(Number(row.dataset.cid), row.dataset.purl, body);
});
document.addEventListener("click", (e) => {
  if (modulePop && !modulePop.hidden && !e.target.closest("#modulePop")) closeModulePop();
});
$("btnModuleOpenPage").onclick = () => {
  if (modulePopCtx && modulePopCtx.pageUrl) window.open(modulePopCtx.pageUrl, "_blank", "noopener");
  closeModulePop();
};
/* 标题可点 = 等同于点「打开文件」（spec §5.2(b)）：让"点名字就能打开"在模块列表里也成立 */
$("modulePopTitle").onclick = () => {
  const ctx = modulePopCtx;
  if (!ctx) return;
  const c = detailCourse;
  closeModulePop();
  openFileSmart(c ? c.id : 0, ctx.fid, ctx.title, (c && c.name) || "", ctx.moduleName, null);
};
$("btnModuleOpenFile").onclick = () => {
  const ctx = modulePopCtx;
  if (!ctx) return;
  const c = detailCourse;
  const btn = $("btnModuleOpenFile");
  closeModulePop();
  openFileSmart(c ? c.id : 0, ctx.fid, ctx.title, (c && c.name) || "", ctx.moduleName, btn);
};

$("btnListFiles").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  if(!ids.length){ setStatus(t("status.need_course"),"err"); return; }
  switchPage("files");
  await withBusy(t("status.loading_files"), $("btnListFiles"), async ()=>{
    const r=await api("list_files",{ ...s, course_ids:ids, download_dir:downloadDir() });
    if(!r.ok){ setStatus(t("status.files_fail")+r.error,"err"); return; }
    fileCourses=r.courses; expandedFiles.clear(); renderFiles();   // 新一批课程默认全部收起
    setStatus(t("status.files_loaded", {n: fileCourses.length}),"ok");
  });
};
/* 文件页展开的课程（按 course_id）。新列出文件时清空 → 默认全收起；筛选重渲时保留，不打断查看 */
const expandedFiles = new Set();
function renderFiles(){
  const filter=$("inpTypeFilter").value.toLowerCase().trim().replace(/^\./,"");
  fillCourseFilter("selFileCourse", fileCourses.map(c => c.name));
  const courseSel = $("selFileCourse").value;
  const shown = fileCourses
    .filter(c => !courseSel || c.name === courseSel)
    .map(c => ({ ...c, _orig: fileCourses.indexOf(c), _empty:(c.files||[]).length===0, files:(c.files||[]).filter(f=>{
      if(!filter) return true;
      return (f.content_type||"").toLowerCase().includes(filter)
          || (f.display_name||"").toLowerCase().endsWith("."+filter);
    })}));
  if(!shown.length){
    $("filesArea").innerHTML = `<div class='muted' style='padding:12px 0'>${t("files.empty")}</div>`;
    updateSelectAllBtn();
    return;
  }
  $("filesArea").innerHTML = shown.map((c)=>{
    const cfs=c.files||[];
    const rows = cfs.map(f=>`
        <div class="item"><input type="checkbox" class="fl" data-ci="${c._orig}" data-fi="${f.file_id}" ${f.saved?"":"checked"}>
          <div><div class="item-title"><a href="#" class="file-open" data-ci="${c._orig}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type)}）</span>${f.saved?` <span class="file-saved">${esc(t("files.saved"))}</span>`:""}</div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`).join("");
    const has = cfs.length>0;
    const open = has && expandedFiles.has(c.course_id);
    return `
    <div class="course-card${open?" open":""}">
      <div class="course-name"><label class="course-sel" title="${escAttr(t("files.course_sel"))}"><input type="checkbox" class="cs" data-orig="${c._orig}"></label>${esc(c.name)} ${c.error?`<span style="color:var(--err);font-size:12px">（${esc(c.error)}）</span>`:c._empty?`<span class="muted" style="font-size:12px"> ${esc(t("files.no_files"))}</span>`:""}${has?`<button type="button" class="caret" data-cid="${c.course_id}" aria-expanded="${open}" title="${open?escAttr(t("files.collapse")):escAttr(t("files.expand"))}">▸</button>`:""}</div>
      ${has?`<div class="items"${open?"":" hidden"}>${rows}</div>`:""}
    </div>`;
  }).join("");
  updateSelectAllBtn();
  refreshCourseChecks();
}
function updateSelectAllBtn(){
  const boxes=[...document.querySelectorAll(".fl")];
  const allOn = boxes.length>0 && boxes.every(b=>b.checked);
  $("btnSelectAllFiles").textContent = allOn ? t("btn.unselect_all") : t("btn.select_all");
}
/* 每课左侧勾选框：全选→checked，部分→indeterminate，无→unchecked */
function refreshCourseChecks(){
  document.querySelectorAll(".course-card").forEach(card=>{
    const cb=card.querySelector(".cs"); if(!cb) return;
    const boxes=[...card.querySelectorAll(".fl")];
    const on=boxes.filter(b=>b.checked).length;
    cb.checked = boxes.length>0 && on===boxes.length;
    cb.indeterminate = on>0 && on<boxes.length;
  });
}
$("inpTypeFilter").oninput = renderFiles;
$("selFileCourse").addEventListener("change", renderFiles);
$("btnSelectAllFiles").onclick = () => {
  const boxes=[...document.querySelectorAll(".fl")];
  const allOn = boxes.length>0 && boxes.every(b=>b.checked);
  boxes.forEach(b=>b.checked=!allOn);
  refreshCourseChecks();
  updateSelectAllBtn();
};
$("filesArea").addEventListener("click", (e)=>{
  const fo = e.target.closest(".file-open");
  if (fo){
    e.preventDefault();                       // href="#" 只是占位，别让页面跳到顶部
    const c = fileCourses[Number(fo.dataset.ci)];
    if (!c) return;
    openFileSmart(c.course_id, Number(fo.dataset.fid), fo.dataset.name, c.name, "", null);
    return;
  }
  const ct=e.target.closest(".caret");
  if(ct){
    const card=ct.closest(".course-card");
    const items=card.querySelector(".items");
    const wasOpen = items && !items.hidden;              // 当前开着 → 本次点它收起
    if(items) items.hidden = wasOpen;
    card.classList.toggle("open", !wasOpen);
    ct.setAttribute("aria-expanded", String(!wasOpen));
    ct.title = wasOpen ? t("files.expand") : t("files.collapse");
    const cid=Number(ct.dataset.cid);
    if(wasOpen) expandedFiles.delete(cid); else expandedFiles.add(cid);
    return;
  }
  const cb=e.target.closest(".cs");
  if(!cb) return;
  // 浏览器已翻转勾选态：点 indeterminate → 全选；点已全选 → 取消全选
  const on=cb.checked;
  [...cb.closest(".course-card").querySelectorAll(".fl")].forEach(b=>b.checked=on);
  refreshCourseChecks();
  updateSelectAllBtn();
});
$("filesArea").addEventListener("change", (e)=>{
  if(!e.target.classList.contains("fl")) return;
  refreshCourseChecks();
  updateSelectAllBtn();
});
$("btnDownloadFiles").onclick = async () => {
  const s=settings();
  const items=[...document.querySelectorAll(".fl:checked")].map(i=>{
    const c=fileCourses[Number(i.dataset.ci)];
    const f=c.files.find(x=>x.file_id===Number(i.dataset.fi));
    return { course_id:c.course_id, file_id:f.file_id, dest_path:f.dest_path }; });
  if(!items.length){ setStatus(t("status.no_file"),"err"); return; }
  const bar=$("downloadProgress"), fill=$("downloadProgressFill"), txt=$("downloadProgressText");
  bar.hidden=false; fill.style.width="0%";
  const downloaded=[], skipped=[], failed=[];
  $("btnDownloadFiles").disabled=true;
  try{
    for(let i=0;i<items.length;i++){
      const fname=items[i].dest_path.split("/").pop();
      fill.style.width=Math.round(i/items.length*100)+"%";
      txt.textContent=t("status.download_progress", {c:i+1, n:items.length, f:fname});
      const r=await api("download_files",{ ...s, download_dir:downloadDir(), items:[items[i]] });
      if(!r.ok){ failed.push({file_id:items[i].file_id, error:r.error||""}); continue; }
      downloaded.push(...(r.downloaded||[]));
      skipped.push(...(r.skipped||[]));
      failed.push(...(r.failed||[]));
    }
    fill.style.width="100%";
    const doneMsg=t("status.download_done", {a:downloaded.length, b:failed.length, s:skipped.length});
    txt.textContent=doneMsg;
    setStatus(doneMsg, failed.length===0?"ok":"err");
    renderFiles();
  } finally {
    $("btnDownloadFiles").disabled=false;
    setTimeout(()=>{ bar.hidden=true; }, 1500);
  }
};

/* ===== 待办 + Canvas 日历事件 ===== */
let todoItems = [];       // 归一化待办（/api/todo）
let todoEvents = [];      // Canvas 一次性事件（/api/calendar_events）
let todoTabInit = false;
/* 启动同步公告后后台顺带查一次待办，让「待办」页签未读红点能显示（不渲染、不遮罩）。
   只跑一次；若期间用户已打开待办页签，则交给 loadTodo，忽略本次后台结果。 */
let bgTodoDone = false;
async function bgFetchTodoBadge(){
  if(bgTodoDone || todoTabInit) return;
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;
  try {
    const r = await api("todo", { canvas_url: s.canvas_url, canvas_token: s.canvas_token });
    if(r.ok === true && Array.isArray(r.items)){
      if(todoTabInit) return;
      bgTodoDone = true;
      todoItems = r.items;                 // 占位即可：待办页签打开时会再 loadTodo 刷新
      refreshBadges();
    }
  } catch (e) {}
}
/* ===== 讨论区（只读：点条目交系统浏览器打开）===== */
let discussData = null;        // {by_course:{cid:[topic]}, errors:{cid:msg}} | null = 尚未加载
let discussTabInit = false;

function initDiscussTab(){
  if(discussTabInit) return;
  discussTabInit = true;
  loadDiscussions();
}

async function loadDiscussions(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token){ setStatus(t("discuss.need_canvas"), "err"); return; }
  const ids = selectedCourses();
  if(!ids.length){ setStatus(t("discuss.need_course"), "err"); return; }
  const el = $("discussStatus");
  el.textContent = t("discuss.loading");
  const r = await api("discussions", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                       course_ids: ids });
  if(r.ok !== true){ el.textContent = t("discuss.fail") + (r.error || ""); return; }
  discussData = { by_course: r.by_course || {}, errors: r.errors || {} };
  renderDiscussions();
  refreshBadges();
}

/* 未读口径：根帖未读 **或** 有未读回复。两个维度独立（实测 read_state="read" 而
   unread_count=8 确实存在），只数 read_state 会漏掉最该看的那一类。 */
function topicUnread(tp){ return tp.read_state === "unread" || Number(tp.unread_count) > 0; }

function topicRowHtml(tp){
  const unread = topicUnread(tp);
  const when = String(tp.last_reply_at || tp.posted_at || "");
  const bits = [tp.author || "", t("discuss.replies", { n: tp.replies_count })];
  if(tp.unread_count) bits.push(t("discuss.unread", { n: tp.unread_count }));
  if(when) bits.push(short(when.slice(0, 10)));
  return `<a class="discuss-row${unread ? " is-unread" : ""}" href="${escAttr(tp.html_url || "")}" target="_blank" rel="noopener">
    <div class="item-title">${unread ? `<span class="badge-new">${t("discuss.new")}</span>` : ""}${esc(tp.title || "")}</div>
    <div class="file-path">${esc(bits.filter(Boolean).join(" · "))}</div>
  </a>`;
}

function renderDiscussions(){
  const box = $("discussGroups");
  if(!discussData){ box.innerHTML = `<div class="muted">${t("discuss.not_loaded")}</div>`; return; }
  const by = discussData.by_course, errs = discussData.errors;
  const names = {};
  (courseList || []).forEach(c => { names[c.id] = c.name; });
  const keys = Object.keys(by).concat(Object.keys(errs).filter(k => !(k in by)));
  if(!keys.length){ box.innerHTML = `<div class="muted">${t("discuss.empty")}</div>`; return; }
  box.innerHTML = keys.map(k => {
    const list = by[k] || [], err = errs[k] || "";
    const body = err
      ? `<div class="muted">${t("discuss.course_fail")}${esc(err)}</div>`
      : !list.length
        ? `<div class="muted">${t("discuss.course_empty")}</div>`
        : list.map(topicRowHtml).join("");
    return `<div class="glass-card ann-grp"><div class="sub-label">${esc(names[k] || ("#" + k))}</div>${body}</div>`;
  }).join("");
  $("discussStatus").textContent = t("discuss.loaded", { n: keys.length });
}

/* 侧栏徽章用。首次加载讨论之前恒为 0 —— 本轮不为讨论加启动期后台预取。 */
function countUnreadDiscussions(){
  if(!discussData) return 0;
  return Object.keys(discussData.by_course || {}).reduce((n, k) =>
    n + (discussData.by_course[k] || []).filter(topicUnread).length, 0);
}

$("btnReloadDiscuss").onclick = () => loadDiscussions();

async function initTodoTab(){
  if(todoTabInit) return;
  todoTabInit = true;
  if(!$("selTodoCalendar").options.length){
    const r = await api("calendars");
    fillSelect("selTodoCalendar", r.calendars || []);
  }
  if(!$("selTodoAlert").options.length) fillAlert("selTodoAlert");
  await loadTodo();
}
async function loadTodo(){
  await withBusy(t("todo.loading"), $("btnLoadTodo"), async ()=>{
    const s = settings();
    const r = await api("todo", { canvas_url:s.canvas_url, canvas_token:s.canvas_token });
    if(r.ok !== true){ setStatus(t("todo.fail") + (r.error || ""), "err"); return; }
    todoItems = r.items || [];
    const ids = selectedCourses();
    if(!ids.length){
      todoEvents = [];
      renderTodo();
      $("todoEvents").innerHTML = `<div class="muted">${t("todo.need_course")}</div>`;
      setStatus(t("todo.loaded", {a: todoItems.length, b: 0}), "ok");
      markTodoSeen();   // 待办内容此刻已显示 → 记已读
      refreshBadges();
      return;
    }
    const now = new Date();
    const end = new Date(now); end.setDate(end.getDate() + 30);
    const er = await api("calendar_events", {
      canvas_url:s.canvas_url, canvas_token:s.canvas_token,
      course_ids:ids, start_date:fmt(now), end_date:fmt(end) });
    if(er.ok !== true){ setStatus(t("todo.fail") + (er.error || ""), "err"); return; }
    todoEvents = er.events || [];
    renderTodo();
    setStatus(t("todo.loaded", {a: todoItems.length, b: todoEvents.length}), "ok");
    markTodoSeen();   // 待办内容此刻已显示 → 记已读
    refreshBadges();
  });
}
$("btnLoadTodo").onclick = () => { todoTabInit = false; initTodoTab(); };
/* 分组键：已过期 / 今天 / 本周（7 天内）/ 以后 */
function todoGroupKey(item){
  if(item.overdue) return "overdue";
  if(!item.due_at) return "later";
  const due = new Date(item.due_at);
  if(isNaN(due)) return "later";
  const now = new Date(); now.setHours(0,0,0,0);
  const dueDay = new Date(due); dueDay.setHours(0,0,0,0);
  const diff = Math.round((dueDay - now) / 86400000);
  if(diff < 0) return "overdue";
  if(diff === 0) return "today";
  if(diff < 7) return "week";
  return "later";
}
function renderTodo(){
  const groups = { overdue: [], today: [], week: [], later: [] };
  todoItems.forEach(it => { (groups[todoGroupKey(it)] || groups.later).push(it); });
  const order = ["overdue", "today", "week", "later"];
  const keys = { overdue: t("todo.group_overdue"), today: t("todo.group_today"),
                 week: t("todo.group_week"), later: t("todo.group_later") };
  const has = order.some(k => groups[k].length);
  if(!has){
    $("todoGroups").innerHTML = `<div class="empty">${t("todo.no_todo")}</div>`;
  } else {
    $("todoGroups").innerHTML = order.map(k => {
      if(!groups[k].length) return "";
      return `<div class="sub-label">${esc(keys[k])}（${groups[k].length}）</div>` +
        groups[k].map(it => `
        <div class="item">
          <div>
            <div class="item-title">${it.html_url
              ? `<a href="${escAttr(it.html_url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
              : esc(it.title)}
              ${it.overdue ? `<span class="sched-badge err">${esc(t("todo.overdue_badge"))}</span>` : ""}</div>
            <div class="file-path">${esc(it.course_name || "")}${it.due_at ? " · " + esc(t("announce.due")) + " " + esc(fmtDue(it.due_at)) : ""}${it.points_possible != null ? " · " + esc(t("common.points", { n: it.points_possible })) : ""}</div>
          </div>
        </div>`).join("");
    }).join("");
  }
  const evs = todoEvents;
  if(!evs.length){
    $("todoEvents").innerHTML = `<div class="muted">${t("todo.no_events")}</div>`;
    return;
  }
  $("todoEvents").innerHTML = evs.map((e, i) => `
    <div class="item"><input type="checkbox" class="cev" data-i="${i}">
      <div><div class="item-title">${e.html_url
        ? `<a href="${escAttr(e.html_url)}" target="_blank" rel="noopener">${esc(e.title)}</a>`
        : esc(e.title)}</div>
      <div class="file-path">${esc(fmtDue(e.start_at))} → ${e.end_at ? esc(fmtDue(e.end_at)) : ""}${e.location_name ? " · " + esc(e.location_name) : ""}</div></div></div>`).join("");
}
$("btnWriteTodoEvents").onclick = async () => {
  const cal = $("selTodoCalendar").value;
  if(!cal){ setStatus(t("status.need_calendar"), "err"); return; }
  const sel = [...document.querySelectorAll(".cev:checked")].map(i => todoEvents[Number(i.dataset.i)]);
  if(!sel.length){ setStatus(t("status.no_event"), "err"); return; }
  const amVal = $("selTodoAlert").value ? Number($("selTodoAlert").value) : null;
  await withBusy(t("status.writing_events", {n: sel.length}), $("btnWriteTodoEvents"), async ()=>{
    const r = await api("write_canvas_events", {
      calendar_name: cal,
      items: sel.map(e => ({ title:e.title, start:e.start_at, end:e.end_at,
                            location:e.location_name || "", notes:"" })),
      alert_minutes: amVal });
    if(r.ok !== true){ setStatus(t("status.write_fail") + (r.error || ""), "err"); return; }
    setStatus(t("todo.events_done", {a:r.created, b:r.created + r.exists}),
      r.errors === 0 ? "ok" : "err");
  });
};

/* ===== 成绩 ===== */
let gradesData = [];
let gradesTabInit = false;
async function initGradesTab(){
  if(gradesTabInit) return;
  gradesTabInit = true;
  await loadGrades();
}
async function loadGrades(){
  const ids = selectedCourses();
  if(!ids.length){ setStatus(t("todo.need_course"), "err"); return; }
  await withBusy(t("grades.loading"), $("btnLoadGrades"), async ()=>{
    const s = settings();
    const r = await api("grades", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                    course_ids: ids });
    if(r.ok !== true){ setStatus(t("grades.fail") + (r.error || ""), "err"); return; }
    gradesData = r.courses || [];
    renderGrades();
    setStatus(t("grades.loaded", {n: gradesData.length}), "ok");
  });
}
$("btnLoadGrades").onclick = () => { gradesTabInit = false; initGradesTab(); };
function renderGrades(){
  if(!gradesData.length){
    $("gradesArea").innerHTML = `<div class="empty">${t("grades.no_data")}</div>`;
    return;
  }
  $("gradesArea").innerHTML = gradesData.map(g => {
    const scoreLine = (g.current_score != null || g.final_score != null)
      ? `<div class="file-path">${esc(t("grades.current"))}: <b>${g.current_score != null ? esc(String(g.current_score)) : "—"}</b> · ${esc(t("grades.final"))}: <b>${g.final_score != null ? esc(String(g.final_score)) : "—"}</b></div>`
      : `<div class="file-path">${esc(t("grades.no_grade"))}</div>`;
    const rows = (g.assignments || []).map(a => `
      <div class="item">
        <div>
          <div class="item-title">${a.html_url
            ? `<a href="${escAttr(a.html_url)}" target="_blank" rel="noopener">${esc(a.name)}</a>`
            : esc(a.name)}
            ${a.submitted ? `<span class="file-saved">${esc(t("grades.submitted"))}</span>` : `<span class="muted">${esc(t("grades.unsubmitted"))}</span>`}</div>
          <div class="file-path">${a.due_at ? esc(t("announce.due")) + " " + esc(fmtDue(a.due_at)) : ""}
            ${a.points_possible != null ? " · " + esc(t("common.points", { n: a.points_possible })) : ""}
            ${a.score != null ? " · <b>" + esc(String(a.score)) + "</b>" : ""}</div>
        </div>
      </div>`).join("");
    return `
    <div class="course-card">
      <div class="course-name">${esc(g.course_name)}</div>
      ${scoreLine}
      <div class="sub-label">${esc(t("grades.assignments"))}（${(g.assignments || []).length}）</div>
      ${rows || `<div class="muted" style="padding:4px 0">${esc(t("detail.no_assignments"))}</div>`}
    </div>`;
  }).join("");
}

/* ===== 课表（AIMS / Banweb）===== */
const BANWEB_KEY="sc_banweb_preview";
let banwebSchedule = loadBanweb();        // {term,fetchedAt,courses,selected,results}
let banwebPollTimer=null;
function loadBanweb(){
  try { const v=JSON.parse(localStorage.getItem(BANWEB_KEY)||"null");
        return v && typeof v==="object" ? v : {term:"",fetchedAt:null,courses:[],selected:[],results:{}}; }
  catch(e){ return {term:"",fetchedAt:null,courses:[],selected:[],results:{}}; }
}
function saveBanweb(){ localStorage.setItem(BANWEB_KEY, JSON.stringify(banwebSchedule)); }
function setBanwebStatusText(msg, kind){
  const el=$("banwebStatusText"); if(!el) return;
  el.textContent=msg;
  el.className = kind ? `muted sched-badge ${kind}` : "muted";
}
async function checkBanwebStatus(){
  const r=await api("banweb/status");
  if(r.ok!==true){ setBanwebStatusText(t("status.banweb_gw"),"err"); return; }
  const loginBtn=$("btnBanwebLogin");
  if(r.status==="logged_in"){
    aimsAutoTried = false;   // 登录成功后重置，下次退登还能自动登录
    setBanwebStatusText(t("status.banweb_ok"),"ok");
    loginBtn.hidden=true;
    stopBanwebPoll();
    loadTerms();   // 已登录 → 刷新学期（有缓存也刷新一次，保证最新）
  } else if(r.status==="needs_login"){
    loginBtn.hidden=false;
    if(!aimsAutoTried){
      // 已存凭据 → 静默自动登录一次（不弹窗）；未存 → 提示手动
      const cr = await api("banweb/credentials/status", undefined, "GET");
      if(cr.ok===true && cr.has_credentials){
        aimsAutoTried = true;
        setBanwebStatusText(t("status.aims_logging_in"), "muted");
        await runAutoLogin(false);
        return;   // runAutoLogin 成功时会直接加载学期
      }
      aimsAutoTried = true;
    }
    setBanwebStatusText(t("status.banweb_need_login"),"err");
    startBanwebPoll();
  } else if(r.status==="opening"){
    setBanwebStatusText(t("status.banweb_opening"),"muted");
    loginBtn.hidden=true;
    startBanwebPoll();
    // 自愈：页面停在空白/中间态时不再死等，直接试抓学期
    // （list_terms 内部自己会导航到学期页；失败落回 needs_login → 自动登录）
    if($("selTerm").options.length <= 1) loadTerms();
  } else {
    setBanwebStatusText(t("status.banweb_unknown"),"err");
    loginBtn.hidden=true;
    startBanwebPoll();
  }
}
$("btnBanwebLogin").onclick = async () => {
  // 「重新登录」一律先无头自动登录：已存凭据即静默完成、不弹窗；
  // 失败（未存凭据/密码错误/后端忙）才弹手动窗口兜底，而不是一上来就开窗。
  aimsAutoTried = true;   // 抑制 3s 轮询重复触发，避免并发双登录
  const loginBtn = $("btnBanwebLogin");
  await withBusy(t("status.aims_logging_in"), loginBtn, async ()=>{
    const auto = await api("banweb/auto_login");
    if(auto.ok === true){
      aimsAutoTried = false;               // 成功 → 直接进入已登录态
      if(loginBtn) loginBtn.hidden = true;
      setBanwebStatusText(t("status.banweb_ok"), "ok");
      stopBanwebPoll();
      loadTerms();
      setStatus(t("status.banweb_ok"), "ok", 4000);
      return;
    }
    // 无头失败 → 落回弹手动登录窗口，用户可自行输入
    const w = await api("banweb/open_login");
    if(w.ok === true) setStatus(t("status.login_opened"), "ok", 6000);
    else setStatus(t("status.login_fail") + (w.error || auto.error || ""), "err");
    checkBanwebStatus();
  });
};
function startBanwebPoll(){ if(banwebPollTimer) return; banwebPollTimer=setInterval(checkBanwebStatus, 3000); }
function stopBanwebPoll(){ if(banwebPollTimer){ clearInterval(banwebPollTimer); banwebPollTimer=null; } }
/* 学期下拉：始终有占位项（避免空下拉在 macOS 上呈置灰不可选），聚焦空下拉时按需重载 */
const BANWEB_TERMS_KEY="sc_banweb_terms";
function loadCachedTerms(){
  try { const v=JSON.parse(localStorage.getItem(BANWEB_TERMS_KEY)||"null");
        return v && Array.isArray(v.terms) ? v.terms : null; }
  catch(e){ return null; }
}
function saveCachedTerms(terms){
  try { localStorage.setItem(BANWEB_TERMS_KEY, JSON.stringify({terms, fetchedAt:new Date().toISOString()})); }
  catch(e){}
}
/* 用给定学期列表填充下拉（占位 + 学期），保留当前选中项 */
function populateTermOptions(terms){
  const sel=$("selTerm"); if(!sel) return;
  const tlist = terms||[];
  const prev = tlist.some(t=>t.value===banwebSchedule.term) ? banwebSchedule.term : "";
  sel.innerHTML="";
  const ph=document.createElement("option"); ph.value=""; ph.textContent=t("schedule.term_placeholder");
  sel.appendChild(ph);
  tlist.forEach(t=>{ const o=document.createElement("option"); o.value=t.value; o.textContent=t.label; sel.appendChild(o); });
  if(prev) sel.value=prev;
  refreshTermState();
}
function ensureTermPlaceholder(){
  const sel=$("selTerm");
  if(!sel || sel.options.length) return;
  const o=document.createElement("option");
  o.value=""; o.textContent=t("schedule.term_placeholder");
  sel.appendChild(o);
}
function refreshTermState(){
  const sel=$("selTerm");
  if(!$("btnFetchSchedule")) return;
  $("btnFetchSchedule").disabled = !sel.value;
}
$("selTerm").addEventListener("focus", ()=>{ if($("selTerm").options.length <= 1) loadTerms(); });
$("selTerm").addEventListener("change", refreshTermState);
let termsLoading=false;
async function loadTerms(){
  if(termsLoading) return;   // 防止聚焦重载与状态轮询并发发起多次抓取
  termsLoading=true;
  const r=await api("banweb/terms");
  const sel=$("selTerm");
  termsLoading=false;
  if(r.ok!==true){
    ensureTermPlaceholder();   // 失败也保证占位项在，避免空下拉在 macOS 置灰
    if((r.error||"").includes("尚未登录")){
      setBanwebStatusText(t("status.banweb_need_login_manual"),"err");
      startBanwebPoll();
    } else {
      // 瞬时故障（如抓取窗口被关）→ 恢复轮询，浏览器重开/登录恢复后会自动重载学期
      setBanwebStatusText(t("status.banweb_terms_fail", {e: (r.error||"")}),"err");
      startBanwebPoll();
    }
    return;
  }
  const terms = r.terms || [];
  if(!terms.length){
    // 空列表：学期页 select 未渲染出来等瞬时竞态 → 当瞬时故障重试，不清缓存
    setBanwebStatusText(t("status.banweb_terms_empty"),"err");
    startBanwebPoll();
    return;
  }
  saveCachedTerms(terms);   // 缓存成功抓到的学期，会话失效后仍可先选
  populateTermOptions(terms);
}
function schedBadge(res){
  // res 可能是字符串（旧版保存）或 {s:状态, old:旧时间}
  const st=(res&&res.s)?res.s:res;
  const old=(res&&res.old)?res.old:"";
  if(st==="created") return `<span class="sched-badge ok">${t("badge.created")}</span>`;
  if(st==="updated") return `<span class="sched-badge ok"${old?` title="${esc(t("badge.old_title",{s:old}))}"`:""}>${esc(old?t("badge.updated_old",{s:old}):t("badge.updated"))}</span>`;
  if(st==="exists") return `<span class="sched-badge exists">${t("badge.exists")}</span>`;
  if(st==="error") return `<span class="sched-badge err">${t("badge.error")}</span>`;
  return "";
}
/* 低饱和柔和色板：配深色玻璃底不刺眼，相邻课程仍可区分 */
const PALETTE = ["#7aa7d8","#6fb8c4","#9d8fd0","#c98fae","#d9a273",
                 "#8fbf9a","#a8a06f","#7f9fc9"];
function scheduleColor(code){
  let h = 0; for (const ch of String(code)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
function gridMinutes(){
  let lo = 480, hi = 1320;   // 默认 8:00–22:00
  for (const c of banwebSchedule.courses)
    for (const m of (c.meetings || []))
      if (m.start_min != null && m.end_min != null) {
        lo = Math.min(lo, m.start_min); hi = Math.max(hi, m.end_min);
      }
  lo = Math.max(0, Math.floor((lo - 30) / 60) * 60);
  hi = Math.min(1440, Math.ceil((hi + 30) / 60) * 60);
  return { lo, hi };
}
function fmtTime(min){ const h = Math.floor(min/60), m = min%60;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}`; }
const DAY_INDEX = { M:0, T:1, W:2, R:3, F:4, S:5, U:6 };
function startOfWeek(d){
  const x = new Date(d);
  const day = (x.getDay() + 6) % 7;   // 周一=0
  x.setDate(x.getDate() - day);
  x.setHours(0, 0, 0, 0);
  return x;
}
function fmtMD(d){ return `${d.getMonth()+1}/${d.getDate()}`; }
let schedWeekStart = startOfWeek(new Date());   // 当前浏览周的周一（本地时间）
function renderSchedule(){
  const gridEl = $("schedulePreview"), noFixedEl = $("scheduleNoFixed");
  const data = banwebSchedule;
  if (!data.courses.length) {
    gridEl.innerHTML = `<div class="empty">${esc(t("schedule.empty"))}</div>`;
    noFixedEl.innerHTML = "";
    $("btnWriteSchedule").disabled = true;
    fillProfessorFilter();
    return;
  }
  $("btnWriteSchedule").disabled = false;
  const { lo, hi } = gridMinutes();
  // 当前浏览周的周一 0 点 → 下周一 0 点（作业 due 只在所属周显示）
  const wkStart = schedWeekStart.getTime();
  const wkEnd = wkStart + 7 * 86400000;
  if ($("weekRange")) {
    const we = new Date(wkStart); we.setDate(we.getDate() + 6);
    $("weekRange").textContent = `${fmtMD(new Date(wkStart))} – ${fmtMD(we)}`;
  }
  fillProfessorFilter();
  const profFilter = $("selProfessor") ? $("selProfessor").value : "";
  const visible = profFilter
    ? data.courses.filter(c => {
        const p = (c.primary_instructor || "").trim();
        if (profFilter === "__none__") return p === "";
        const sep = profFilter.indexOf("");
        if (sep < 0) return p === profFilter;          // 兼容旧值
        return c.code === profFilter.slice(0, sep) && p === profFilter.slice(sep + 1);
      })
    : data.courses;
  const HOUR_PX = 56, PX_PER_MIN = HOUR_PX / 60;
  const rows = Math.round((hi - lo) / 60);
  // 每列的块 HTML
  const colBlocks = Array.from({length:7}, () => "");
  const assignBlocks = Array.from({length:7}, () => "");
  const markCount = Array.from({length:7}, () => 0);
  const noFixed = [];
  for (const c of visible) {
    const key = c.code + ":" + c.section;
    const color = scheduleColor(c.code);
    const selected = banwebSchedule.selected.includes(key);
    const res = banwebSchedule.results[key];
    const canvas = matchCourseByCode(c.code);
    // ⓘ 详情始终可点：匹配到 Canvas 传其 id（详情带大纲/作业/链接），否则仅 Banweb 数据
    const detailBtn = `<button class="cal-detail" data-cid="${canvas ? canvas.id : ""}" data-code="${escAttr(c.code)}"
         aria-label="${esc(t("detail.open"))}" title="${esc(t("detail.open"))}">ⓘ</button>`;
    let placed = false;
    for (const m of (c.meetings || [])) {
      if (m.start_min == null || m.end_min == null) continue;
      for (const d of (m.days_list || [])) {
        const idx = DAY_INDEX[d];
        if (idx == null) continue;
        placed = true;
        const top = (m.start_min - lo) * PX_PER_MIN;
        const hgt = Math.max(20, (m.end_min - m.start_min) * PX_PER_MIN);
        const badge = res ? schedBadge(res) : "";
        colBlocks[idx] += `<div class="cal-block${selected ? " sel" : ""}" data-key="${esc(key)}"
          style="top:${top}px;height:${hgt}px;background:${color}">
          ${detailBtn}
          <div style="font-weight:600;color:#fff">${esc(c.code)} ${esc(c.section)}</div>
          <div style="color:rgba(255,255,255,.9)">${fmtTime(m.start_min)}–${fmtTime(m.end_min)}</div>
          ${(m.room_short || m.room) ? `<div style="color:rgba(255,255,255,.8)">${esc(m.room_short || m.room)}</div>` : ""}
          ${badge}</div>`;
      }
    }
    if (!placed) noFixed.push(c);
  }
  // 考试块叠加：日期落在浏览周 → 加到对应日列（叠在课程块之上）
  if (banwebExams && banwebExams.exams.length) {
    banwebExams.exams.forEach(ex => {
      if (!ex.date) return;
      const d = new Date(ex.date + "T00:00:00");
      if (isNaN(d)) return;
      const t = d.getTime();
      if (t < wkStart || t >= wkEnd) return;
      const idx = (d.getDay() + 6) % 7;
      const sm = examMinutes(ex.start);
      if (sm == null) return;
      const em = examMinutes(ex.end);
      const top = (sm - lo) * PX_PER_MIN;
      const hgt = em != null ? Math.max(20, (em - sm) * PX_PER_MIN) : 90;
      const title = (ex.code || ex.course || "Exam") + " " + (ex.section || "");
      colBlocks[idx] += `<div class="cal-block exam-block" style="top:${top}px;height:${hgt}px">
        <span class="exam-tag">${esc(t("schedule.exam_badge"))}</span>
        <div style="font-weight:600;color:#fff">${esc(title)}</div>
        <div style="color:rgba(255,255,255,.9)">${fmtTime(sm)}–${em != null ? fmtTime(em) : "?"}</div>
        ${(ex.room || ex.seat) ? `<div style="color:rgba(255,255,255,.8)">${esc([ex.room, ex.seat].filter(Boolean).join(" · "))}</div>` : ""}
      </div>`;
    });
  }
  // 作业 due 标注：只在所属周显示，整块是 <a target="_blank"> 指向 Canvas 提交页
  const courseNameById = {};
  courseList.forEach(c => { courseNameById[c.id] = c.name; });
  if (showAssignments) {
    Object.entries(assignmentMarks).forEach(([cidStr, list]) => {
      const cid = Number(cidStr);
      if (!Array.isArray(list)) return;
      const cname = courseNameById[cid] || `Course ${cid}`;
      list.forEach(a => {
        if (!a.due_at) return;                 // 无截止日期 → 不上日历
        const due = new Date(a.due_at);
        if (isNaN(due)) return;
        const dueT = due.getTime();
        if (dueT < wkStart || dueT >= wkEnd) return;   // 不在当前浏览周 → 不显示
        const idx = (due.getDay() + 6) % 7;    // JS 周日=0 → 转 周一=0
        assignBlocks[idx] += `<a class="assignment-mark" href="${escAttr(a.html_url || "")}"
          target="_blank" rel="noopener"
          title="${escAttr(cname)} — ${escAttr(a.name)}">
          <span class="mark-course">${esc(cname)}</span>
          <span class="mark-name">${esc(a.name)}</span>
          <span class="mark-due">${esc(t("announce.due"))} ${esc(fmtDue(a.due_at))}</span></a>`;
        markCount[idx]++;
      });
    });
  }
  // 统一琥珀条高度：任一列有标注时，7 列 + 时间轴都预留同高（每张作业卡 ~68px），保证时间轴对齐
  const maxMarks = Math.max(...markCount);
  const stripH = maxMarks ? Math.round(6 + 68 * maxMarks) : 0;
  // 时间轴
  let axis = `<div class="time-axis"><div class="corner"></div>`;
  if (stripH > 0) axis += `<div class="axis-strip" style="height:${stripH}px"></div>`;
  for (let r = 0; r < rows; r++) axis += `<div class="time-label">${fmtTime(lo + r * 60)}</div>`;
  axis += `</div>`;
  // 7 个列容器（day-body 相对定位，块绝对定位叠在其上）
  let cols = "";
  for (let d = 0; d < 7; d++) {
    const strip = stripH > 0
      ? `<div class="assign-strip" style="height:${stripH}px">${assignBlocks[d]}</div>`
      : "";
    const dd = new Date(wkStart); dd.setDate(dd.getDate() + d);
    cols += `<div class="day-col"><div class="day-head">${t("wd."+d)} ${fmtMD(dd)}</div>
      ${strip}
      <div class="day-body" style="height:${rows * HOUR_PX}px">${colBlocks[d]}</div></div>`;
  }
  gridEl.innerHTML = `<div class="schedule-grid">${axis}${cols}</div>`;
  updateAssignBtn();
  noFixedEl.innerHTML = noFixed.length
    ? `<div class="sub-label">${t("schedule.no_fixed")}</div>` +
      noFixed.map(c => {
        const key = c.code + ":" + c.section;
        return `<div class="course-card" style="opacity:.6">
          <label class="check" style="border:none;padding:0;background:transparent">
            <input type="checkbox" data-key="${esc(key)}" disabled> ${esc(c.code)} ${esc(c.section)} · ${esc(c.course)}
          </label>
          <span class="sched-badge muted">${t("badge.no_time")}</span></div>`;
      }).join("")
    : "";
}
$("selProfessor").addEventListener("change", renderSchedule);
$("btnFetchSchedule").onclick = async () => {
  const term=$("selTerm").value;
  if(!term){ setStatus(t("status.need_term"),"err"); return; }
  await withBusy(t("status.fetching"), $("btnFetchSchedule"), async ()=>{
    const r=await api("banweb/schedule",{ term });
    if(r.ok!==true){ setStatus(t("status.fetch_fail")+(r.error||""),"err"); return; }
    banwebSchedule={ term, fetchedAt:new Date().toISOString(), courses:r.courses, selected:[], results:{} };
    banwebSchedule.selected = r.courses.filter(c=>(c.meetings||[]).length>0).map(c=>c.code+":"+c.section);
    saveBanweb();
    renderSchedule();
    setStatus(t("status.fetched", {n: r.courses.length}),"ok");
  });
};
function hasAssignData(){
  return Object.keys(assignmentMarks).some(k => (assignmentMarks[k] || []).length);
}
function updateAssignBtn(){
  const b = $("btnLoadAssignments");
  if (!b) return;
  b.textContent = (hasAssignData() && showAssignments) ? t("btn.hide_assignments") : t("btn.load_assignments");
}
$("btnLoadAssignments").onclick = async () => {
  if (hasAssignData()) {              // 已加载 → 只切换显隐，不重新拉取
    showAssignments = !showAssignments;
    renderSchedule();
    return;
  }
  const ids = selectedCourses();
  if (!ids.length){ setStatus(t("status.need_course"), "err"); return; }
  await withBusy(t("status.loading_assignments"), $("btnLoadAssignments"), async ()=>{
    let r;
    try { r = await ensureAssignments(ids); }
    catch (err){ setStatus(t("status.assignments_fail") + (err.message || ""), "err"); return; }
    showAssignments = true;
    renderSchedule();
    const errCount = Object.keys((r && r.errors) || {}).length;
    if (errCount)
      setStatus(t("status.assignments_loaded", {n: ids.length - errCount}) +
                " · " + t("status.assignments_fail") + errCount, "err");
    else
      setStatus(t("status.assignments_loaded", {n: ids.length}), "ok");
  });
};
$("schedulePreview").addEventListener("click", (e) => {
  const detailBtn = e.target.closest(".cal-detail");
  if (detailBtn) {
    e.preventDefault(); e.stopPropagation();
    const code = detailBtn.dataset.code;
    const cid = detailBtn.dataset.cid;
    const bc = code ? banwebSchedule.courses.find(c => c.code === code) : null;
    openCourseDetail(cid ? Number(cid) : null, bc);
    return;
  }
  const blk = e.target.closest(".cal-block");
  if (!blk) return;
  const key = blk.dataset.key;
  const sel = new Set(banwebSchedule.selected);
  if (sel.has(key)) sel.delete(key); else sel.add(key);
  banwebSchedule.selected = [...sel];
  saveBanweb(); renderSchedule();
});
$("btnWeekPrev").addEventListener("click", () => { schedWeekStart.setDate(schedWeekStart.getDate() - 7); renderSchedule(); });
$("btnWeekNext").addEventListener("click", () => { schedWeekStart.setDate(schedWeekStart.getDate() + 7); renderSchedule(); });
$("btnWeekToday").addEventListener("click", () => { schedWeekStart = startOfWeek(new Date()); renderSchedule(); });
$("btnWriteSchedule").onclick = async () => {
  const cal=$("selSchedCalendar").value;
  if(!cal){ setStatus(t("status.need_sched_calendar"),"err"); return; }
  const selected = banwebSchedule.selected;
  if(!selected.length){ setStatus(t("status.no_sched"),"err"); return; }
  const amVal = $("selSchedAlert").value ? Number($("selSchedAlert").value) : null;
  await withBusy(t("status.syncing_sched", {n: selected.length}), $("btnWriteSchedule"), async ()=>{
    const r=await api("banweb/write_calendar",{ calendar_name:cal, courses:banwebSchedule.courses,
      selected, alert_minutes:amVal });
    if(r.ok!==true){ setStatus(t("status.sched_fail")+(r.error||""),"err"); return; }
    const newRes={}; (r.items||[]).forEach(it=>{
      const block=it.key.split(":").slice(0,2).join(":");
      newRes[block]=it.old_time?{s:it.status,old:it.old_time}:it.status; });
    banwebSchedule.results=newRes;
    saveBanweb();
    renderSchedule();
    setStatus(t("status.sched_done", {a:r.created, b:r.exists, c:r.updated, d:r.removed, e:r.errors}),
      r.errors===0?"ok":"err");
  });
};
$("btnClearSchedule").onclick = () => {
  banwebSchedule={ term:"",fetchedAt:null,courses:[],selected:[],results:{} };
  localStorage.removeItem(BANWEB_KEY);
  renderSchedule();
  setStatus(t("status.sched_cleared"),"ok");
};
/* ===== 考试时间表叠加 ===== */
let banwebExams = null;   // {term_label, exams} | null
let canvasQuizzes = null;      // {by_course:{cid:[quiz]}, errors:{cid:msg}} | null = 未加载
function examMinutes(hhmm){
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}
async function loadExams(){
  const el = $("examStatus");
  if(el) el.textContent = t("schedule.exam_loading");
  const r = await api("banweb/exams");
  if(r.ok !== true){
    banwebExams = null;
    if(el) el.textContent = t("schedule.exam_fail") + (r.error || "");
    renderSchedule();
    return;
  }
  banwebExams = { term_label: r.term_label || "", exams: r.exams || [] };
  if(el) el.textContent = banwebExams.exams.length
    ? t("schedule.exam_loaded", {term: banwebExams.term_label})
    : t("schedule.exam_none");
  renderSchedule();
}
/* 测验来自 Canvas，与 AIMS 登录无关 —— 未登录也要能看。整体失败就静默不显示分组。 */
async function loadQuizzes(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;
  const ids = selectedCourses();
  if(!ids.length) return;
  try {
    const r = await api("quizzes", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                     course_ids: ids });
    canvasQuizzes = r.ok === true
      ? { by_course: r.by_course || {}, errors: r.errors || {} }
      : null;
  } catch (e) {
    canvasQuizzes = null;
  }
  renderQuizzes();
}

/* 未启用测验工具的课程在端点层就没进 errors（实测约一半课程如此），
   所以这里没有「404 错误行」要处理；真正失败的那些课程也只是不贡献条目。 */
function renderQuizzes(){
  const box = $("quizGroup");
  if(!box) return;
  if(!canvasQuizzes){ box.hidden = true; box.innerHTML = ""; return; }
  const names = {};
  (courseList || []).forEach(c => { names[c.id] = c.name; });
  const rows = [];
  Object.keys(canvasQuizzes.by_course).forEach(k => {
    (canvasQuizzes.by_course[k] || []).forEach(q =>
      rows.push({ ...q, course: names[k] || ("#" + k) }));
  });
  box.hidden = false;
  if(!rows.length){
    box.innerHTML = `<div class="filter-bar" style="margin-top:14px">
      <span class="filter-label">${t("schedule.quiz_label")}</span>
      <span class="muted">${t("schedule.quiz_none")}</span></div>`;
    return;
  }
  rows.sort((a, b) => String(a.due_at || "").localeCompare(String(b.due_at || "")));
  box.innerHTML =
    `<div class="filter-bar" style="margin-top:14px">
       <span class="filter-label">${t("schedule.quiz_label")}</span>
       <span class="muted">${t("schedule.quiz_loaded", { n: rows.length })}</span>
     </div>
     <div class="quiz-list">` +
    rows.map(q => {
      const bits = [];
      if(q.due_at) bits.push(t("schedule.quiz_due") + " " + fmtDue(q.due_at));
      if(q.question_count != null) bits.push(t("schedule.quiz_questions", { n: q.question_count }));
      if(q.time_limit != null) bits.push(t("schedule.quiz_limit", { n: q.time_limit }));
      if(q.points_possible != null) bits.push(t("common.points", { n: q.points_possible }));
      return `<a class="quiz-row" href="${escAttr(q.html_url || "")}" target="_blank" rel="noopener">
        <div class="item-title">${esc(q.title || "")}</div>
        <div class="file-path">${esc(q.course + (bits.length ? " · " + bits.join(" · ") : ""))}</div>
      </a>`;
    }).join("") + `</div>`;
}
$("btnReloadExams").onclick = loadExams;
$("btnWriteExams").onclick = async () => {
  const cal = $("selExamCalendar").value;
  if(!cal){ setStatus(t("status.need_sched_calendar"), "err"); return; }
  const exams = (banwebExams && banwebExams.exams) || [];
  if(!exams.length){ setStatus(t("schedule.exam_none"), "err"); return; }
  const amVal = $("selSchedAlert").value ? Number($("selSchedAlert").value) : null;
  await withBusy(t("status.writing_events", {n: exams.length}), $("btnWriteExams"), async ()=>{
    const r = await api("banweb/write_exams", { calendar_name: cal, exams, alert_minutes: amVal });
    if(r.ok !== true){ setStatus(t("status.write_fail") + (r.error || ""), "err"); return; }
    setStatus(t("schedule.exam_done", {a:r.created, b:r.exists, e:r.errors}),
      r.errors === 0 ? "ok" : "err");
  });
};
let schedTabInit=false;
async function initScheduleTab(){
  if(schedTabInit) return;
  schedTabInit=true;
  setBanwebStatusText(t("schedule.checking"), "muted");
  if(!$("selSchedCalendar").options.length){
    const r=await api("calendars");
    fillSelect("selSchedCalendar", r.calendars||[]);
  }
  if(!$("selSchedAlert").options.length) fillAlert("selSchedAlert");
  ensureTermPlaceholder();
  refreshTermState();
  renderSchedule();
  if(!$("selExamCalendar").options.length){
    const r = await api("calendars");
    fillSelect("selExamCalendar", r.calendars || []);
  }
  loadExams();     // 静默拉考试（登录态复用课表会话；失败仅提示不阻塞）
  loadQuizzes();   // 静默拉 Canvas 测验（与 AIMS 无关，不登录也要能看）
  // 先填上次抓到的学期（离线 / AIMS 会话失效时也能先选），再走登录流程刷新
  const cachedTerms = loadCachedTerms();
  if(cachedTerms && cachedTerms.length) populateTermOptions(cachedTerms);
  // 已存凭据 → 直接自动登录并加载学期（跳过慢速 status 探测）；未存 → 状态检查提示登录
  const cr = await api("banweb/credentials/status", undefined, "GET");
  if(cr.ok===true && cr.has_credentials){
    await runAutoLogin(false);
  } else {
    await checkBanwebStatus();
  }
}

$("btnLang").onclick = () => {
  localStorage.setItem("sc_lang", LANG() === "zh" ? "en" : "zh");
  applyLang();
  renderSummaries();                     // 重渲动态文案（AI 总结按钮等）
  renderFiles();                         // 重渲文件全选按钮标签
  refreshBadges();                       // applyLang 会清掉页签内子节点，重画红点徽标
  renderCourseHubList();                 // 课程中心列表：内容是动态拼的，data-i18n 刷不到
  renderCourseHub();                     // 详情页标题/标签/面板：同上（无目标课程时它自己早退）
};
applyLang();
loadSettings();
defaultRange();
fillSelect("selCalendar", []); fillSelect("selList", []);
fillAlert();
renderSchedule();
function onTopRangeChange(){
  scheduleAutoSync();
}
$("inpStart").addEventListener("change", onTopRangeChange);
$("inpEnd").addEventListener("change", onTopRangeChange);
renderSummaries();

/* ===== 课程中心：列表 → 单课程四子标签 =====
   与课程详情弹层（#detailModal）并存，两者各自独立可用。
   本页只呈现"这一门课"的内容，不做跨课程聚合 —— 那是侧栏各全局页的职责。 */
let hubCid = null;        // 当前详情页的 Canvas course id（null = 未匹配到 Canvas 课程）
let hubBanweb = null;     // 从首页课表进来时带的 Banweb 课程对象（可为 null）
let hubTab = "files";     // 当前标签
let hubInit = false;      // 首次进入是否已拉过课程列表

/* 同一课程同一标签 60 秒内不重复后台拉取：切标签是高频动作，
   每次都打一次网络既慢又无意义。 */
const HUB_REFRESH_TTL = 60000;
const hubRefreshed = new Map();          // "cid:tab" -> 上次刷新时间戳
function hubShouldRefresh(key){
  const last = hubRefreshed.get(key) || 0;
  if (Date.now() - last < HUB_REFRESH_TTL) return false;
  hubRefreshed.set(key, Date.now());
  return true;
}

/* 详情页标题：优先 Canvas 课程名；未匹配到 Canvas 时退化为 Banweb 的 code+section */
function hubCourseName(){
  if (hubCid != null){
    const c = (courseList || []).find(x => x.id === hubCid);
    if (c) return c.name;
  }
  if (hubBanweb) return `${hubBanweb.code || ""} ${hubBanweb.section || ""}`.trim();
  return hubCid != null ? ("#" + hubCid) : "";
}

/* 首次进入课程中心：课程配置现在在设置页，不能指望用户先去点「加载课程」，
   所以这里自行拉一次（有缓存就直接渲染，不闪空白）。与 discuss/grades/home
   的"首次进入才拉"是同一个 house pattern。 */
function initCourseHubTab(){
  if (hubInit){ renderCourseHubList(); return; }   // 已初始化过：从缓存重绘，不重复打网络
  hubInit = true;
  if ((courseList || []).length){ renderCourseHubList(); return; }
  const s = settings();
  if (!s.canvas_url || !s.canvas_token){ renderCourseHubList(); return; }   // 未配置 → 空态
  loadCourseHubList();
}
async function loadCourseHubList(){
  const r = await api("courses", settings());
  if (r.ok) courseList = r.courses || [];
  else setStatus(t("status.courses_fail") + (r.error || ""), "err");   // 失败必须说失败，不能装成「没有课程」
  renderCourseHubList();
}

function renderCourseHubList(){
  const box = $("hubListArea");
  if (!box) return;
  const s = settings();
  if (!s.canvas_url || !s.canvas_token){
    box.innerHTML = `<div class="empty">${t("hub.need_config")}</div>`;
    return;
  }
  const ignored = savedCourseIgnored() || new Set();
  const list = (courseList || []).filter(c => !ignored.has(c.id));
  if (!list.length){
    box.innerHTML = `<div class="empty">${t("hub.empty")}</div>`;
    return;
  }
  box.innerHTML = list.map(c => `
    <button type="button" class="hub-card" data-id="${c.id}">
      <span class="hub-card-name">${esc(c.name)}</span>
      <span class="hub-card-code">${esc(c.course_code || "")}</span>
    </button>`).join("");
}
$("hubListArea").addEventListener("click", (e) => {
  const b = e.target.closest(".hub-card");
  if (b) openCourseHub(Number(b.dataset.id), null);
});

function backToHubList(){
  hubCid = null; hubBanweb = null;
  $("hubDetail").hidden = true;
  $("hubList").hidden = false;
  renderCourseHubList();
}
$("btnHubBack").onclick = backToHubList;

/* 进入某门课的详情。canvasId 允许为 null（Banweb 有课但没匹配上 Canvas 课程），
   此时退化为只显示课表信息 + 一条明确提示。 */
function openCourseHub(canvasId, banwebCourse){
  hubCid = (canvasId == null ? null : Number(canvasId));
  hubBanweb = banwebCourse || null;
  hubTab = "files";
  $("hubList").hidden = true;
  $("hubDetail").hidden = false;
  renderCourseHub();
  // 详情页住在 #page-courses 里，若当前不在课程页则切过去（侧栏高亮随之同步）。
  // 放最后：switchPage 会触发 PAGE_INIT.courses，而 hubInit 守卫保证它不会重拉数据。
  if (typeof switchPage === "function" && currentPage !== "courses") switchPage("courses");
  hubRefreshCurrent();
}

function renderCourseHub(){
  if (hubCid == null && !hubBanweb) return;     // 没有目标课程：什么都不写，避免误清空
  $("hubTitle").textContent = hubCourseName();
  $$("#hubTabs .tab-item").forEach(b => {
    const on = b.dataset.tab === hubTab;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  renderHubTab();
}
$("hubTabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab-item");
  if (b) switchHubTab(b.dataset.tab);
});
/* 课程中心里的文件名点击：与侧栏文件页共用 openFileSmart，行为完全一致 */
$("hubPanel").addEventListener("click", (e) => {
  const fo = e.target.closest(".file-open");
  if (!fo) return;
  e.preventDefault();
  openFileSmart(Number(fo.dataset.cid), Number(fo.dataset.fid), fo.dataset.name, hubCourseName(), "", null);
});
function switchHubTab(name){
  if (!["files", "announce", "todo", "discuss"].includes(name)) return;
  hubTab = name;
  renderCourseHub();
  hubRefreshCurrent();
}

/* Banweb 课表信息（未匹配到 Canvas 时唯一能显示的内容）。
   字段取自 renderHomeToday 已在用的那批（shell.js:169-171），不用未验证的字段。 */
function hubBanwebInfoHtml(){
  if (!hubBanweb) return "";
  const ms = (hubBanweb.meetings || []).map(m =>
    `<div class="file-path">${esc((m.days_list || []).join(""))} ${esc(fmtTime(m.start_min))}–${esc(fmtTime(m.end_min))}${(m.room_short || m.room) ? " · " + esc(m.room_short || m.room) : ""}</div>`).join("");
  const head = `${hubBanweb.code || ""} ${hubBanweb.section || ""}`.trim();
  return `<div class="glass-card"><div class="sub-label">${esc(head)}</div>${ms}</div>`;
}

/* 标签分发。四个 renderHub* 由后续任务实现（函数声明提升，运行时可见）。 */
function renderHubTab(){
  const box = $("hubPanel");
  if (!box) return;
  if (hubCid == null){
    box.innerHTML = `<div class="muted">${t("hub.no_canvas")}</div>` + hubBanwebInfoHtml();
    return;
  }
  const fn = { files: renderHubFiles, announce: renderHubAnnounce,
               todo: renderHubTodo, discuss: renderHubDiscuss }[hubTab];
  (fn || renderHubFiles)(hubCid);
}

/* 后台刷新：只对能按课程粒度的端点做（spec §4.4）。
   待办不做 —— /api/todo 没有课程参数，只能整批取，本轮只用全局缓存。
   注意各 renderHub* 自身绝不触发刷新，否则会形成 渲染→刷新→渲染 的死循环。 */
function hubRefreshCurrent(){
  if (hubCid == null) return;
  const key = hubCid + ":" + hubTab;
  if (!hubShouldRefresh(key)) return;
  if (hubTab === "files") hubRefreshFiles(hubCid);
  else if (hubTab === "discuss") hubRefreshDiscuss(hubCid);
  else if (hubTab === "announce") hubRefreshAnnounce();
}

/* 文件子标签。只读 fileCourses 缓存，不触发网络（刷新由 hubRefreshCurrent 统一发起）。 */
function renderHubFiles(cid){
  const box = $("hubPanel");
  if (!box) return;
  const c = (fileCourses || []).find(x => x.course_id === cid);
  if (!c){                                   // 全局缓存里根本没有这门课 → 尚未加载
    box.innerHTML = `<div class="muted">${t("hub.tab_empty.files_unloaded")}</div>`;
    return;
  }
  if (c.error){ box.innerHTML = `<div class="muted">${esc(c.error)}</div>`; return; }
  const files = c.files || [];
  if (!files.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.files")}</div>`; return; }
  box.innerHTML = files.map(f => `
    <div class="item">
      <div>
        <div class="item-title"><a href="#" class="file-open" data-cid="${cid}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type || "")}）</span>${f.saved ? ` <span class="file-saved">${esc(t("files.saved"))}</span>` : ""}</div>
        <div class="file-path">${esc(f.path || "/")}</div>
      </div>
    </div>`).join("");
}

/* 后台按本课程刷新一次文件列表。失败静默 —— 保留已渲染的缓存，不把标签打回空态。 */
async function hubRefreshFiles(cid){
  const s = settings();
  if (!s.canvas_url || !s.canvas_token) return;
  const r = await api("list_files", { ...s, course_ids: [cid], download_dir: downloadDir() });
  if (r.ok !== true) return;
  const fresh = (r.courses || [])[0];
  if (!fresh) return;
  const i = (fileCourses || []).findIndex(x => x.course_id === cid);
  if (i >= 0) fileCourses[i] = fresh; else fileCourses.push(fresh);
  // 用户可能已经切走或返回列表：只在仍停留在本课程的文件标签时才重渲染
  if (hubCid === cid && hubTab === "files") renderHubFiles(cid);
}
/* 公告子标签。只读 summaryResults 缓存。正文用 .announce-msg.expanded 保持全文 ——
   展开按钮的布线与委托都只扫 #summaries，这里复制一个按钮会点不动（裁定 R4）。 */
function renderHubAnnounce(cid){
  const box = $("hubPanel");
  if (!box) return;
  const g = (summaryResults || []).find(c => c.course_id === cid);
  if (!g){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.announce_unloaded")}</div>`; return; }
  if (g.error){ box.innerHTML = `<div class="muted">${esc(g.error)}</div>`; return; }
  const anns = g.announcements || [];
  if (!anns.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.announce")}</div>`; return; }
  const cvUrl = (settings().canvas_url || "").replace(/\/+$/, "");
  box.innerHTML = anns.map(a => {
    const titleHtml = (cvUrl && a.id)
      ? `<a class="announce-link" href="${escAttr(cvUrl)}/courses/${escAttr(cid)}/announcements/${escAttr(a.id)}" target="_blank" rel="noopener">${esc(a.title)}</a>`
      : esc(a.title);
    return `<div class="item"><div>
      <div class="item-title">${titleHtml} <span class="muted">${esc(String(a.posted_at || "").slice(0, 10))}</span></div>
      <div class="announce-msg-wrap"><div class="announce-msg expanded"><div class="announce-msg-inner">${esc(a.message)}</div></div></div>
    </div></div>`;
  }).join("");
}

/* 公告没有"按单门课拉取"的端点：syncAnnouncements() 是跨全部选中课程的批量操作。
   所以这里只在缓存里没有这门课时才主动同步一次，
   否则每次切到公告标签都跑一遍全量同步，代价过大（裁定 R3）。 */
async function hubRefreshAnnounce(){
  // 该课不在勾选集合里 → 同步永远填不上它（syncAnnouncements 的 course_ids 取自 selectedCourses()），
  // 面板会永久停在「尚未同步」，且每次 TTL 过期都白跑一次全量同步。与 scheduleAutoSync 同一约定。
  if (!selectedCourses().includes(hubCid)) return;
  if ((summaryResults || []).some(c => c.course_id === hubCid)) return;   // 有缓存 → 不重复批量拉
  const ok = await syncAnnouncements();
  if (ok && hubCid != null && hubTab === "announce") renderHubAnnounce(hubCid);
}
/* 待办子标签。只读 todoItems 缓存，不做后台刷新 ——
   /api/todo 没有课程参数，只能整批取（spec §4.4 明确本轮不刷新）。 */
function renderHubTodo(cid){
  const box = $("hubPanel");
  if (!box) return;
  const all = todoItems || [];
  if (!all.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.todo_unloaded")}</div>`; return; }
  const mine = all.filter(i => i.course_id === cid);
  if (!mine.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.todo")}</div>`; return; }
  box.innerHTML = mine.map(it => `
    <div class="item"><div>
      <div class="item-title">${it.html_url
        ? `<a href="${escAttr(it.html_url)}" target="_blank" rel="noopener">${esc(it.title)}</a>`
        : esc(it.title)}
        ${it.overdue ? `<span class="sched-badge err">${esc(t("todo.overdue_badge"))}</span>` : ""}</div>
      <div class="file-path">${esc(it.type || "")}${it.due_at ? " · " + esc(t("announce.due")) + " " + esc(fmtDue(it.due_at)) : ""}${it.points_possible != null ? " · " + esc(t("common.points", { n: it.points_possible })) : ""}</div>
    </div></div>`).join("");
}

/* 讨论子标签。复用既有 topicRowHtml（纯行渲染，不绑容器），不复制一份。
   ⚠️ discussData.by_course 的键经 JSON 往返后是字符串，必须 String(cid) 取用。 */
function renderHubDiscuss(cid){
  const box = $("hubPanel");
  if (!box) return;
  if (!discussData){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; return; }
  const key = String(cid);
  const errs = discussData.errors || {};
  if (errs[key] !== undefined){ box.innerHTML = `<div class="muted">${t("discuss.course_fail")}${esc(errs[key])}</div>`; return; }
  const by = discussData.by_course || {};
  const list = by[key];
  if (list === undefined){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss_unloaded")}</div>`; return; }
  if (!list.length){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.discuss")}</div>`; return; }
  box.innerHTML = list.map(topicRowHtml).join("");
}

/* 后台按本课程刷新讨论。只替换这一门课的结果，不动其它课程的缓存。 */
async function hubRefreshDiscuss(cid){
  const s = settings();
  if (!s.canvas_url || !s.canvas_token) return;
  const r = await api("discussions", { canvas_url: s.canvas_url, canvas_token: s.canvas_token, course_ids: [cid] });
  if (r.ok !== true) return;
  const key = String(cid);                                    // 同上：字符串键
  const prev = discussData || { by_course: {}, errors: {} };
  const by = Object.assign({}, prev.by_course || {});
  const errs = Object.assign({}, prev.errors || {});
  by[key] = (r.by_course || {})[key] || [];
  if (r.errors && r.errors[key] !== undefined) errs[key] = r.errors[key]; else delete errs[key];
  discussData = { by_course: by, errors: errs };
  if (hubCid === cid && hubTab === "discuss") renderHubDiscuss(cid);
  refreshBadges();                                            // 讨论未读数影响侧栏徽章
}
