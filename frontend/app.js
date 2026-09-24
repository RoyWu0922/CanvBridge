
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
  refreshGmailUi();
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
/* 自动登录失败 → 弹窗问「是否手动登录」。确认才开可见窗口，取消则停在退登态。
   并发的询问共用同一个 promise（3s 轮询与用户点「重新登录」可能撞上），不叠弹窗。 */
let loginPromptResolve = null;
function confirmManualLogin(){
  const m = $("loginPrompt");
  if(!m) return Promise.resolve(false);   // 弹层缺失（旧页面缓存）→ 当作「取消」，绝不擅自开窗
  if(loginPromptResolve) return loginPromptResolve;
  loginPromptResolve = new Promise(resolve => {
    const done = yes => {
      loginPromptResolve = null;
      m.hidden = true;
      resolve(yes);
    };
    // 遮罩 / ✕ /「稍后再说」一律算取消；只有「手动登录」为确认
    m.querySelector(".modal-backdrop").onclick = () => done(false);
    $("btnLoginPromptClose").onclick = () => done(false);
    $("btnLoginPromptNo").onclick    = () => done(false);
    $("btnLoginPromptYes").onclick   = () => done(true);
    m.hidden = false;
    $("btnLoginPromptYes").focus();
  });
  return loginPromptResolve;
}
/* 供 Esc 关闭复用（挂进 app.js 已有的全局 keydown，不另起监听器） */
function dismissLoginPrompt(){ const m=$("loginPrompt"); if(m && !m.hidden) $("btnLoginPromptNo").click(); }
/* 自动登录失败后的统一出口：先问，确认才开手动登录窗口 */
async function offerManualLogin(autoError){
  if(!await confirmManualLogin()){
    setStatus(t("status.aims_manual_declined"), "info", 6000);
    return false;
  }
  const w = await api("banweb/open_login");
  if(w.ok === true){ setStatus(t("status.login_opened"), "ok", 6000); return true; }
  setStatus(t("status.login_fail") + (w.error || autoError || ""), "err");
  return false;
}
/* 只做无头自动登录；失败时由调用方在 withBusy 结束后再问手动登录
   （弹层 z-index 低于 #overlay 忙碌遮罩，在遮罩里弹会被盖住）。 */
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
  // 返回值：null=保存失败 / true=自动登录成功 / false=自动登录失败（需要手动登录）
  const ok = await withBusy(t("status.aims_saving"), $("btnSaveAims"), async ()=>{
    const r = await api("banweb/credentials", { username, password });
    if(r.ok !== true){ setStatus(t("status.aims_save_fail") + (r.error||""), "err"); return null; }
    setStatus(t("status.aims_saved_ok"), "ok", 4000);
    await refreshAimsUi();
    return await runAutoLogin(true);   // 保存后立即自动登录
  });
  if(ok === false) await offerManualLogin();   // withBusy 已退出，弹窗不会被忙碌遮罩盖住
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

/* 学校邮箱（Gmail）凭据：应用专用密码存本机钥匙串，后端**永不**回传密码 ——
   status 只回「有没有」与地址，故密码框只能靠用户每次现填，绝不用服务器响应回填。
   这个函数有**三态**：有凭据 / 没凭据 / **读失败**。第三态必须是个看得见的错误，
   不能退化成第二态 —— 见下面 fail 分支的注释。 */
async function refreshGmailUi(){
  const el = $("gmailSavedHint"); if(!el) return;
  const r = await api("mail/gmail/credentials/status", undefined, "GET");
  const fail = r.ok !== true;
  const has  = !fail && r.has_credentials === true;
  /* 只有真问到答案时才动地址与清除按钮 ——「没问出来」和「没保存过」是两回事（见下）。
     AIMS 的 fail 分支同样什么都不动，这是本仓的既有写法。读失败时若照旧往下写，
     三件事同时发生：用户刚存好的地址当场消失；清除按钮被藏起来，而 DELETE 并**不依赖**
     这次读，等于在唯一读不通的时候把唯一的出路也堵了；openSettings() 是**不 await**
     地调本函数的，慢响应回来时会把用户正敲到一半的地址抹掉。 */
  if(!fail){
    $("gmailAddress").value = has ? (r.address || "") : "";
    $("btnClearGmail").hidden = !has;
  }
  $("gmailAppPassword").value = "";   // 与 AIMS 同款：刷新即清空，不给密码留在 DOM 里的机会
  el.textContent = has  ? t("status.gmail_saved_hint")
                 : fail ? t("status.gmail_status_fail") + (r.error||"")
                 : "";
  /* 读失败 ≠ 没保存过。`api()` 在传输层失败或响应不是 JSON 时**不抛错**，只回 {ok:false}，
     所以这里必须自己判 r.ok；否则一次读失败会渲染成「从没配置过」：地址框空着、清除按钮
     藏着、提示空着 —— 与全新安装**逐字相同**。用户分不清「凭据没了」与「没问出来」，
     还会把已经存好的应用专用密码再填一遍（同一个密码存两处是真实风险）。
     提示行是 muted 的、不够显眼，故同时亮横幅：与兄弟处理器同款
     `t(...) + (r.error||"") + "err"`（见 btnSaveGmail / btnClearGmail）。 */
  if(fail) setStatus(t("status.gmail_status_fail") + (r.error||""), "err");
}
$("btnSaveGmail").onclick = async () => {
  const address = $("gmailAddress").value.trim();
  const app_password = $("gmailAppPassword").value;
  if(!address || !app_password){ setStatus(t("status.gmail_need_both"), "err"); return; }
  await withBusy(t("status.gmail_saving"), $("btnSaveGmail"), async ()=>{
    /* 这两个端点失败时也回 HTTP 200，正文里是 {ok:false, error} —— 判据只能取正文的 ok，
       不能看状态码（与 AIMS 那两条同形）。地址按原样送，后端自己会去空白。 */
    const r = await api("mail/gmail/credentials", { address, app_password });
    if(r.ok !== true){ setStatus(t("status.gmail_save_fail") + (r.error||""), "err"); return; }
    $("gmailAppPassword").value = "";   // 存成功即清空：密码不留在 DOM 里
    setStatus(t("status.gmail_saved_ok"), "ok", 4000);   // 先亮一次：重读要等一次网络往返
    await refreshGmailUi();
    /* 重读那一步自己失败时，refreshGmailUi 会亮 err 横幅 —— setStatus 是「后写的赢」，
       它会把上面刚亮的「已保存」当场盖掉：凭据明明已经写进钥匙串了，用户看见的却是一片红，
       于是把已经存好的应用专用密码又填一遍（同一个密码存两处，正是本轮要防的事）。
       所以刷完再断言一次**本次操作自己的**结果：读失败由提示行报出来（见 refreshGmailUi
       的 fail 分支）；横幅按「后写的赢」归上面那次成功提示 —— K1 钉住成功信号仍在，
       K2 钉住失败文案仍在提示行。上面那次 setStatus 保留，不清算，
       但「你这一步成功了」不因为回读失败而消失。 */
    setStatus(t("status.gmail_saved_ok"), "ok", 4000);
  });
};
$("btnClearGmail").onclick = async () => {
  await withBusy(t("status.gmail_clearing"), $("btnClearGmail"), async ()=>{
    const r = await api("mail/gmail/credentials", undefined, "DELETE");
    if(r.ok !== true){ setStatus(t("status.gmail_clear_fail") + (r.error||""), "err"); return; }
    $("gmailAddress").value=""; $("gmailAppPassword").value="";
    setStatus(t("status.gmail_cleared"), "ok");   // 先亮一次：重读要等一次网络往返
    await refreshGmailUi();
    /* 与保存同款：回读失败不能把「已清除」这个**本次操作自己的**结果盖成红色读失败
       —— 凭据确实删掉了，用户却以为没删掉，会再点一次。读失败由提示行报出；
       横幅同上，归「已清除」（L1 钉住成功信号仍在，L2 钉住失败文案仍在提示行）。 */
    setStatus(t("status.gmail_cleared"), "ok");
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
  if (!$("loginPrompt").hidden){ dismissLoginPrompt(); return; }   // Esc = 取消手动登录
  /* #confirmPrompt 也要能被 Esc 关掉，且语义必须是**取消**：走 #btnConfirmNo 的 onclick，
     也就是 askConfirm 里那个 `done(false)`（与 ✕ / 取消 / 点遮罩同一出口 —— promise 必须
     落地、confirmResolve 必须清空，绝不留下悬着的 promise）。判据用 DOM 的 hidden 而不是
     状态变量，与本处上两条同形，也避开晚声明的 `confirmResolve`。 */
  if (!$("confirmPrompt").hidden){ $("btnConfirmNo").onclick(); return; }
  if (!$("detailModal").hidden) closeDetail();
  if(!$("mailDetail").hidden){ closeMailDetail(); return; }
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
    renderCourseHubList();   // 课程中心列表跟着更新
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
  renderCourseHubList();
});
$("btnClearIgnore").onclick = () => {
  localStorage.removeItem(COURSE_IGNORE_KEY);
  if (courseList.length) renderCourseCheckboxes(courseList);
  fillIgnoreCourses();          // 先按新的忽略集重画忽略区，再让课程中心照同一份数据重排
  renderCourseHubList();
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
   为什么不直接拿 Canvas 文件对象自带的 url 在新标签打开：那个 url 带 download_frd，
   浏览器打开会被 Canvas 判定为附件下载、无法内联渲染；所以由后端
   /api/module_file_stream 用 token 流式取回字节、按真实 content-type 回传，
   前端 fetch→blob 再交给浏览器内置查看器。
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
  /* 后端出错时同样是 200 + JSON（main.py 约定），所以 Content-Type 是判据，不能只看 resp.ok。
     但后端是把 Canvas 文件对象的真实 content-type 原样回传的 —— 真·JSON 文件同样报
     application/json，只凭它判错会让「点 .json 文件名」静默退化成下载（M28）。
     所以再确认一层「响应体确实是后端约定的错误体」：ok === false 或带字符串 error。 */
  if (!resp.ok || ctype === "application/json"){
    let j = null;
    // 用 clone() 判体：json() 会吃掉 body，直接在 resp 上读会让后面的 resp.blob() 报
    // “body stream already read”，真·JSON 文件仍旧打不开。
    try { j = await resp.clone().json(); } catch (e) { /* 不是 JSON（或解析失败）→ 按内容照常处理 */ }
    if (j && (j.ok === false || typeof j.error === "string")) return { ok: false, error: j.error || "" };
    if (!resp.ok) return { ok: false, error: "" };   // HTTP 真失败且不是错误体（如网关的 HTML 页）→ 仍算失败
    // 走到这里 = 200 + application/json 且不是错误体 → 就是个真·JSON 文件，落下去照常内联打开
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
     window.opener 反向操控本页。代价是弹窗被拦时无法探测；这与既有的 btnModuleOpenPage 的 onclick 里
     （同样的调用且忽略返回值）取舍一致，用户已接受。 */
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
      const failMsg = t("file.open_fail") + (r.error || "");
      setStatus(failMsg, "err");
      const d = await downloadFileTo(courseId, fileId, name, courseName, moduleName);
      /* 降级下载成功也**不覆盖**上面那条错误（M9）：用户点的是「在浏览器里打开」，
         它却变成了下载 —— 这件事被一句「已下载」抹掉，用户就永远不知道内联打开失效了。
         两条并成一句显示（仍是 err 色，因为用户要的动作确实失败了）。 */
      if (d.ok) setStatus(failMsg + " " + t("file.downloaded", {p: d.dest}), "err");
    });
  } catch (e) {
    // 不把内部异常的 e.message 原样透给用户（M11）——那是实现措辞，不是用户能用的信息。
    setStatus(t("file.open_fail_unexpected"), "err");
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
/* 文件夹展开状态（键为 "<courseId>:<folderId>"）。与 expandedFiles 分开：
   那个管课程卡片的展开，这个管卡片内部文件夹的展开。 */
const expandedFolders = new Set();
/* 把 /api/list_files 的 files[] 与 folders[] 合成一棵树。
   返回 { folders: 根层文件夹节点, files: 挂在根层的散文件 }；节点形如
   { id, name, position, folders:[], files:[] }。

   folders 可能是 undefined —— 后端在 no_files 与 error 两条分支上不带这个键
   （虽然 main.py 现在补了 folders: []，这里仍按容缺写：那两类课程今天显示的
   是一句弱提示，不能因为建树整块白掉）。 */
function buildFileTree(files, folders){
  const list = Array.isArray(folders) ? folders : [];
  const nodes = new Map();
  list.forEach(fo => nodes.set(fo.id, {
    id: fo.id, name: fo.name || "", position: fo.position, parent: fo.parent_folder_id,
    folders: [], files: [],
  }));
  /* Canvas 的课程根文件夹（parent_folder_id 为 null）**不作为一个可见层**：课程本身就是根。
     它的子文件夹与直接挂在它下面的文件都提升到根层 —— 判据与后端 build_folder_path 剥根时
     用的完全一致，因此对 Canvas 实际会返回的数据，屏幕上的层级与磁盘上的路径层级都对得上。
     例外只有一种：父链成环。那时本函数把环上的成员整个提到根层，而后端只把链就地截断、
     被截断的链头 parent 仍非 null，磁盘路径于是多留一段 —— 屏幕上显示 A > f，磁盘上却是
     …/B/A/f。Canvas 不会返回这种数据，所以这里只是把话说准，不是要改代码。
     也正因如此，Canvas 文件页上看到的顶层层级（Week 1 / Week 3 …）与这里一致，不会多出
     一个 course files 行。rootIds 为空（Canvas 返回形态与预期不符）时，这个「提升」自动
     不发生 —— 退回不提升的旧行为，而不是把路径算错。 */
  const rootIds = new Set(list.filter(fo => fo && fo.parent_folder_id === null).map(fo => fo.id));
  const roots = [], loose = [];
  nodes.forEach(n => {
    if (rootIds.has(n.id)) return;                    // 课程根本身不成节点
    const p = nodes.get(n.parent);
    const parentIsRoot = rootIds.has(n.parent);       // 父是课程根 → 本节点提升到根层
    if (p && !parentIsRoot && p !== n && !parentChainCycles(nodes, n)) p.folders.push(n);
    else roots.push(n);
  });
  (files || []).forEach(f => {
    /* 根下的文件（folder_id 指向课程根）与找不到归属的文件，都挂根层 —— 不丢文件。 */
    const n = rootIds.has(f.folder_id) ? null : nodes.get(f.folder_id);
    (n ? n.files : loose).push(f);
  });
  sortTree(roots, loose);
  return { folders: roots, files: loose };
}
/* n 的父链上是否回环到 n 自身。seen 保证最多走一遍，绝不会无限循环。 */
function parentChainCycles(nodes, n){
  const seen = new Set();
  let cur = nodes.get(n.parent);
  while (cur){
    if (cur === n) return true;
    if (seen.has(cur)) return false;      // 撞进别人的环 → 这条链到此为止
    seen.add(cur);
    cur = nodes.get(cur.parent);
  }
  return false;
}
/* 文件夹按 Canvas 的 position 升序；取不到 position 的排在有值的后面，彼此按名字。
   文件按 display_name —— 我们没抓文件的 position，按名字排每次渲染顺序一致，
   比依赖 API 返回顺序这种隐含约定好。 */
function sortTree(folders, files){
  const pos = n => (n.position === null || n.position === undefined) ? Infinity : n.position;
  folders.sort((a, b) => (pos(a) - pos(b)) || a.name.localeCompare(b.name));
  files.sort((a, b) => String(a.display_name || "").localeCompare(String(b.display_name || "")));
  folders.forEach(n => sortTree(n.folders, n.files));
}
/* 递归渲染一棵文件夹树。fileRow(f, depth) 由调用方给，负责生成文件行的 HTML
   （侧栏带勾选框、课程中心不带）。fkey(folderId) 生成展开状态的键。
   传入 { folders, files } 形状的伪根即可 —— buildFileTree 的返回值就是这个形状。 */
function treeRows(node, depth, expanded, fkey, fileRow){
  const dirs = (node.folders || []).map(ch => {
    const k = fkey(ch.id);
    const open = expanded.has(k);
    const caret = open ? t("files.collapse") : t("files.expand");
    return `
      <div class="folder-row" style="padding-left:${depth * 14}px">
        <button type="button" class="fcaret" data-fkey="${escAttr(k)}" aria-expanded="${open}" title="${escAttr(caret)}">▸</button>
        <span class="folder-name">${esc(ch.name)}</span>
      </div>
      <div class="fchildren"${open ? "" : " hidden"}>${treeRows(ch, depth + 1, expanded, fkey, fileRow)}</div>`;
  }).join("");
  const rows = (node.files || []).map(f => fileRow(f, depth)).join("");
  return dirs + rows;
}
function renderFiles(){
  const filter=$("inpTypeFilter").value.toLowerCase().trim().replace(/^\./,"");
  fillCourseFilter("selFileCourse", fileCourses.map(c => c.name));
  const courseSel = $("selFileCourse").value;
  const shown = fileCourses
    .filter(c => !courseSel || c.name === courseSel)
    .map(c => {
      // 课程根不渲染成行，所以它不算「有文件夹」（否则会出现展开后为空的 caret）
      const nf=(c.folders||[]).filter(fo => fo && fo.parent_folder_id !== null);
      return { ...c, _orig: fileCourses.indexOf(c),
               _empty:(c.files||[]).length===0 && nf.length===0,
               files:(c.files||[]).filter(f=>{
        if(!filter) return true;
        return (f.content_type||"").toLowerCase().includes(filter)
            || (f.display_name||"").toLowerCase().endsWith("."+filter);
      })}; });
  if(!shown.length){
    $("filesArea").innerHTML = `<div class='muted' style='padding:12px 0'>${t("files.empty")}</div>`;
    updateSelectAllBtn();
    return;
  }
  $("filesArea").innerHTML = shown.map((c)=>{
    const cfs=c.files||[];
    const folders=c.folders||[];
    /* 文件行保留原有的 .fl 勾选框与 file-open 直链（updateSelectAllBtn 与
       refreshCourseChecks 是按 .fl 全局/按卡片统计的，文件夹行绝不能带 .fl，
       否则文件夹会被算成一个文件）。 */
    const fileRow=(f, depth)=>`
        <div class="item" style="padding-left:${depth*14}px"><input type="checkbox" class="fl" data-ci="${c._orig}" data-fi="${f.file_id}" ${f.saved?"":"checked"}>
          <div><div class="item-title"><a href="#" class="file-open" data-ci="${c._orig}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type)}）</span>${f.saved?` <span class="file-saved">${esc(t("files.saved"))}</span>`:""}</div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`;
    const tree=buildFileTree(cfs, folders);
    const rows=treeRows(tree, 0, expandedFolders, (fid)=>`${c.course_id}:${fid}`, fileRow);
    const has = cfs.length>0 || folders.some(fo => fo && fo.parent_folder_id !== null);
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
  const fc=e.target.closest(".fcaret");
  if(fc){
    const k=fc.dataset.fkey;
    const kids=fc.closest(".folder-row").nextElementSibling;   // 紧邻的 .fchildren
    const wasOpen = kids && !kids.hidden;
    if(kids) kids.hidden = wasOpen;
    fc.setAttribute("aria-expanded", String(!wasOpen));
    fc.title = wasOpen ? t("files.expand") : t("files.collapse");
    if(wasOpen) expandedFolders.delete(k); else expandedFolders.add(k);
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
    return { course_id:c.course_id, file_id:f.file_id,
             dest_path:f.dest_path, legacy_path:f.legacy_path||"" }; });
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
/* todoItems 初值就是 []，加载后真为 0 条也还是 [] —— 光看它区分不了「未加载」与
   「已加载但全局 0 条」。课程中心的待办标签要据此选空态文案，所以另立一个显式标志。
   凡成功拿到**完整一整批** todoItems 的路径都要置真，现共两处：loadTodo() 与
   bgFetchTodoBadge()。注意 bgFetchTodoBadge 自身不渲染待办列表，但它填的 todoItems
   会被 renderHubTodo 读走 —— 漏置真会让待办标签误报「尚未加载」，故必须一并置真。 */
let todoLoaded = false;
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
      todoItems = r.items;
      /* 同一端点 /api/todo、同样是**完整成功的一整批** → 与 loadTodo 成功路径语义相同，
         故必须一并置真，否则课程中心的待办标签会把「已在内存里的待办」误报成「尚未加载」。
         （此批为空时 todoItems=[] + todoLoaded=true → 「没有待办」，正是正确语义。） */
      todoLoaded = true;
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
    todoLoaded = true;      // 成功拿到过一整批 → 之后 0 条就是「真的没有」，不是「还没加载」（M20）
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
        // 失败 → 问是否手动登录（aimsAutoTried 已置位，轮询不会重复触发自动登录）
        if(await runAutoLogin(false) === false) await offerManualLogin();
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
  // 「重新登录」一律先无头自动登录：已存凭据即静默完成、不开窗；
  // 失败（未存凭据/密码错误/后端忙/MFA）才问用户要不要手动登录，而不是一上来就开窗。
  aimsAutoTried = true;   // 抑制 3s 轮询重复触发，避免并发双登录
  const loginBtn = $("btnBanwebLogin");
  // 返回值：null=自动登录成功 / 字符串=失败原因（可能为空串）
  const autoError = await withBusy(t("status.aims_logging_in"), loginBtn, async ()=>{
    const auto = await api("banweb/auto_login");
    if(auto.ok !== true) return auto.error || "";
    aimsAutoTried = false;                 // 成功 → 直接进入已登录态
    if(loginBtn) loginBtn.hidden = true;
    setBanwebStatusText(t("status.banweb_ok"), "ok");
    stopBanwebPoll();
    loadTerms();
    setStatus(t("status.banweb_ok"), "ok", 4000);
    return null;
  });
  if(autoError === null) return;           // 成功，无需手动登录
  await offerManualLogin(autoError);       // 失败 → 先问再开窗（withBusy 已退出）
  checkBanwebStatus();
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
    // 失败 → 问是否手动登录（本函数是进课表页的主路径，不走 checkBanwebStatus）
    if(await runAutoLogin(false) === false) await offerManualLogin();
  } else {
    await checkBanwebStatus();
  }
}

/* 图书馆检索的会话状态。
   **声明在这里、而不是末尾那段图书馆代码里**：本文件顶层的 `applyLang()`
   已经会调用 `renderLibraryPage()`（钩子见 i18n.js 的 applyLang），而**函数声明是提升的**
   —— `typeof x === "function"` 守门拦不住它，于是它会去读状态；状态若还压在文件末尾的
   `let` 里，这一读就是 TDZ ReferenceError（实机验证抓到的白屏级错误，`node --check` 查不出来）。
   本文件里凡 applyLang() 会碰的状态都在那个 `applyLang();` 之前声明（`hubCid` 那类不被它碰的才放在后面）。
   翻页要靠 parsed 原样回传 —— 后端第 2 页起不调 LLM，所以这份 parsed 必须与结果同源。
   `busy` 是检索在途标记（防连点重复消费 LLM，见 runLibrarySearch）。 */
let libraryState = { parsed: null, page: 1, loaded: false, areas: [], last: null, busy: false };

const LIBRARY_MAX_PAGES = 5;   /* 与后端 library_client.MAX_PAGES 一致；后端才是权威 */

/* 学校邮箱的页面状态。
   **声明在这里、而不是末尾那段邮箱代码里**：与上面的 `libraryState` 同一个道理 ——
   本文件顶层的 `applyLang()` 会经 i18n.js 的钩子调用 renderMail()，而 renderMail
   是**函数声明**（提升），`typeof` 守门拦不住它；状态若压在文件末尾的 `let` 里，
   这一读就是 TDZ ReferenceError，会让本文件顶层余下代码全部不执行（`node --check` 查不出来）。
   邮箱**不依赖 AIMS 登录态**：取信走 Gmail IMAP（应用专用密码存本机钥匙串），
   因而失败路径既不发手动登录问询、也不开浏览器窗口 —— 后端返回的文案已指向设置页，
   由 renderMail() 呈现。spec §11「认证失败文案直接指向设置页重填；不重试」。 */
/* 列表窗口的天数：列表请求、AI 总结、空态文案**共用这一个数** ——
   三处各写一个 7 的话，改一处就会让「空态说 7 天、实际查了 14 天」这种错静默存在。 */
const MAIL_DAYS = 7;
let mailState = { mails: [], groups: null, loading: false, error: "" };

/* 邮件规则分类页的状态。**声明在这里、而不是文件末尾那段页面代码里**：与上面的
   `mailState` 同一个道理 —— 本文件顶层的 `applyLang()`（就在下面几行）会经 i18n.js
   的钩子调用 renderMailTodo()，而它要读这些顶层 `let`。压在文件末尾就是
   「读到还在 TDZ 的 let」⇒ **整页白屏**，而 `typeof` 守卫照旧为真、`node --check` 也查不出。
   判据是「这个函数读了哪些顶层 let，那些声明在**最早**调用时点之前吗」—— 不只是
   **新增**状态会踩：给既有函数加一个更早的调用时点同样致命。 */
/* 窗口天数。**`let` 而不是 `const`**：工具条上的天数选择要能改它。
   待办跨周，默认 14 天比邮件页的 7 天长。
   **仍然必须声明在这段早声明区里** —— renderMailTodo() 会读它取空态文案，
   而 renderMailTodo() 在下面几行那次顶层 applyLang() 里就会被调用到。 */
let mtDays = 14;
const MT_RULES_KEY = "sc_mailRules";
const MT_DONE_KEY  = "sc_mailDone";
let mtRules = [];                                 // 规则（localStorage 的镜像）
let mtState = { mails: [], labels: {}, loading: false, error: "", warnings: [],
                items: {}, extracting: false, extractWarnings: [], missing: 0 };

/* 详情弹层「当前这一封」的状态（`id` + `detail` + `detailId`）。
   **声明在这里、而不是末尾那段邮箱代码里**：与上面的 `mailState` 同一个道理，而且这一条
   更要命 —— `renderMailDetail` 会被本文件顶层的 `applyLang()`（经 i18n.js 的钩子）**无参**
   调用，而它第一件事就是读 `mailDetailState.detail` 判断要不要重渲（读状态在无参调用上也
   会发生，`typeof` 守门拦不住）。状态若压在文件末尾的 `let` 里，那一读就是 TDZ
   ReferenceError（`Cannot access 'mailDetailState' before initialization`），本文件顶层
   余下代码**全部不执行** = 白屏级；`node --check` 查不出来 —— 这条是
   `tools/check_mail_frontend.mjs` 逐文件 `runInContext` 的加载步骤实跑抓到的。
   `detail` = 上一次渲染过的详情，供语言切换后的**无参**重渲取用；
   `detailId` 是「缓存里这份详情属于哪一封」：`id` 会被 `openMailDetail` 在**发请求之前**改掉，
   而详情要等响应回来才有。失败与在途两种情况下缓存里留的都是**上一封** ——
   `renderMailDetail` 的无参入口（i18n 钩子）必须靠这个比对才知道该不该画。
   三个字段**只在进程内存里**：不落盘、不进任何请求体、不跨会话（弹层关闭时也不清缓存 ——
   读到旧缓存不会画错内容。**注意是哪一道闸在拦**：承重的是 `detailId !== id` **单独一道**
   （`check_mail_frontend.mjs` 实测：删掉它立刻 2 条红）；`el.hidden` 与 `!d` 目前**都没有
   「只有它才拦得住」的路径**（删掉任一条，93 条断言照旧全绿）—— 两个关弹层的入口
   （`closeMailDetail`、遮罩点击）都会顺手清 `id`，于是「关掉」这条路径上 id 那道闸必然成立。
   留着 `el.hidden` 是**纵深防御**：将来若多出一条「只隐藏、不清 id」的路径，它就是唯一
   拦得住的那一处，而不是「两道闸各自拦得住」。加清理反而会多
   一处「每个改 `id` 的地方都得记得清」的状态改动，那正是本缺陷的成因形态）。 */
let mailDetailState = { id: "", detail: null, detailId: "" };

$("btnLang").onclick = () => {
  localStorage.setItem("sc_lang", LANG() === "zh" ? "en" : "zh");
  applyLang();
  renderSummaries();                     // 重渲动态文案（AI 总结按钮等）
  renderFiles();                         // 重渲文件全选按钮标签
  refreshBadges();                       // applyLang 会清掉页签内子节点，重画红点徽标
  renderCourseHubList();                 // 课程中心列表：内容是动态拼的，data-i18n 刷不到
  renderCourseHub();                     // 详情页标题/标签/面板：同上（无目标课程时它自己早退）
  if (courseList.length) renderCourseCheckboxes(courseList);   // 设置页课程 chip 的「详情/忽略」，同上
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
  if (r.ok){
    courseList = r.courses || [];
    /* 勾选区必须一并填：selectedCourses() 是从 #courseCheckboxes 的 DOM 里读的，
       只填 courseList 会造成「课程中心列得出课、设置页一门都勾不上」——
       而 syncAnnouncements / scheduleAutoSync / btnListFiles 全都以它为输入。 */
    renderCourseCheckboxes(courseList);
  } else {
    setStatus(t("status.courses_fail") + (r.error || ""), "err");   // 失败必须说失败，不能装成「没有课程」
  }
  renderCourseHubList(!r.ok);
}

/* 失败与「一门课都没有」是两件事：前者要说加载失败，后者才是 hub.empty。
   loadFailed 由 loadCourseHubList() 显式传入，其余调用点不传（默认假），
   所以这里没有粘滞状态，返回列表页 / 从缓存重绘都不会留下永久红字。
   判定顺序：必须排在「未配置」之后 —— 未配置时该说 hub.need_config，不是失败。 */
function renderCourseHubList(loadFailed){
  const box = $("hubListArea");
  if (!box) return;
  const s = settings();
  if (!s.canvas_url || !s.canvas_token){
    box.innerHTML = `<div class="empty">${t("hub.need_config")}</div>`;
    return;
  }
  if (loadFailed){
    box.innerHTML = `<div class="empty">${t("hub.load_fail")}</div>`;
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
    b.tabIndex = on ? 0 : -1;                    // 漫游焦点：只有活动标签进 Tab 序列
    if (on) $("hubPanel").setAttribute("aria-labelledby", b.id);
  });
  renderHubTab();
}
$("hubTabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab-item");
  if (b) switchHubTab(b.dataset.tab);
});
/* 漫游焦点（roving tabindex）的键盘一半：左右方向键在两个标签间移动、Home/End 到首尾。
   焦点跟着走，随后 browser 的 click 语义由我们直接调 switchHubTab 复现。
   不做焦点陷阱、不引第三方库 —— 与仓库既有的轻量做法一致。 */
$("hubTabs").addEventListener("keydown", (e) => {
  const tabs = $$("#hubTabs .tab-item");
  const cur = tabs.findIndex(b => b.dataset.tab === hubTab);
  if (cur < 0) return;
  let next = null;
  if (e.key === "ArrowRight") next = (cur + 1) % tabs.length;
  else if (e.key === "ArrowLeft") next = (cur - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = tabs.length - 1;
  if (next == null) return;
  e.preventDefault();
  switchHubTab(tabs[next].dataset.tab);
  tabs[next].focus();
});
/* 课程中心里的文件名点击：与侧栏文件页共用 openFileSmart，行为完全一致 */
$("hubPanel").addEventListener("click", (e) => {
  const fo = e.target.closest(".file-open");
  if (!fo) return;
  e.preventDefault();
  openFileSmart(Number(fo.dataset.cid), Number(fo.dataset.fid), fo.dataset.name, hubCourseName(), "", null);
});

/* 课程中心「还没下载」清单的勾选收集。勾选框是 .hfl 而不是 .fl —— 侧栏的
   updateSelectAllBtn/refreshCourseChecks 按 document.querySelectorAll(".fl") 全局
   统计，而 #filesArea 与 #hubPanel 同时在 DOM 里，用 .fl 会把这里的勾选算进侧栏。 */
function hubSelectedFiles(cid){
  const c = (fileCourses || []).find(x => x.course_id === cid);
  if (!c) return [];
  return [...document.querySelectorAll("#hubPanel .hfl:checked")].map(i => {
    const f = (c.files || []).find(x => x.file_id === Number(i.dataset.fid));
    return f ? { course_id: cid, file_id: f.file_id,
                 dest_path: f.dest_path, legacy_path: f.legacy_path || "" } : null;
  }).filter(Boolean);
}
/* 「下载所选」的可用态：一个都没勾就禁用。按钮每次重渲都是新的，所以按 class 取，
   不用 id —— 动态渲染的元素在 index.html 里没有对应 id，用 $() 取会被 check_dom_ids 拦。 */
function hubSyncDownloadBtn(){
  const btn = document.querySelector("#hubPanel .hub-dl-go");
  if (btn) btn.disabled = hubSelectedFiles(hubCid).length === 0;
}
$("hubPanel").addEventListener("change", (e) => {
  if (e.target.classList && e.target.classList.contains("hfl")) hubSyncDownloadBtn();
});
/* 课程中心的下载走与侧栏同一个端点。逐条发（端点一次只收一批 items，逐条发好报进度），
   完成后重渲本标签 —— saved 会从后端刷新回来，清单条目随之消失、计数随之减少。 */
$("hubPanel").addEventListener("click", async (e) => {
  // 课程中心的文件夹 caret。侧栏那份委托绑在 #filesArea 上，这里复用了同一个
  // .fcaret 类但复用不了那个监听器，必须自己来一份。键与 renderHubFiles 传给
  // treeRows 的 fkey 一致（"<courseId>:<folderId>"），因此展开态与侧栏共享。
  const fc = e.target.closest(".fcaret");
  if (fc){
    const k = fc.dataset.fkey;
    const kids = fc.closest(".folder-row").nextElementSibling;   // 紧邻的 .fchildren
    if (kids && kids.classList.contains("fchildren")){
      const wasOpen = !kids.hidden;
      kids.hidden = wasOpen;
      fc.setAttribute("aria-expanded", String(!wasOpen));
      fc.title = wasOpen ? t("files.expand") : t("files.collapse");
      if (wasOpen) expandedFolders.delete(k); else expandedFolders.add(k);
    }
    return;
  }
  const btn = e.target.closest(".hub-dl-go");
  if (!btn) return;
  const items = hubSelectedFiles(hubCid);
  if (!items.length) return;
  const s = settings();
  btn.disabled = true;
  const downloaded = [], skipped = [], failed = [];
  try {
    for (const it of items) {
      const r = await api("download_files",
        { ...s, download_dir: downloadDir(), items: [it] });
      if (r.ok !== true) { failed.push({ file_id: it.file_id, error: r.error || "" }); continue; }
      downloaded.push(...(r.downloaded || []));
      skipped.push(...(r.skipped || []));
      failed.push(...(r.failed || []));
    }
    const doneMsg = t("status.download_done",
      { a: downloaded.length, b: failed.length, s: skipped.length });
    setStatus(doneMsg, failed.length === 0 ? "ok" : "err");
    await hubRefreshFiles(hubCid);
  } finally {
    hubSyncDownloadBtn();
  }
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
  /* 后台刷新是 fire-and-forget（两个调用点都不 await），所以异常必须在这里就地兜住。
     真实可抛点：settings() → saveSettings() 的 localStorage.setItem 在配额满 / 隐私模式下会抛，
     而 hubRefresh* 一进来就调它 —— 不兜就是一个未处理的 promise rejection
     （控制台报错、标签停在旧内容、没有任何用户可见反应）。兜一处，三个刷新共用。 */
  const run = { files: () => hubRefreshFiles(hubCid),
                discuss: () => hubRefreshDiscuss(hubCid),
                announce: () => hubRefreshAnnounce() }[hubTab];
  if (run) run().catch(() => {});
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
  const folders = c.folders || [];
  /* 课程根（parent_folder_id 为 null）由 buildFileTree 提升成根层、自身不成行，
     所以它不算「有文件夹」—— 否则只剩一个课程根、零文件的课会绕过下面这条空态分支，
     treeHtml 与 panel 都是空串，整块标签被清空（比现在只剩一句提示还糟）。
     判据与 renderFiles 的 has/_empty 完全一致。 */
  if (!files.length && !folders.some(fo => fo && fo.parent_folder_id !== null)){
    // 后端在 Canvas 对空文件区返回 403 时会带 no_files:true（backend/main.py 的 list_files）——
    // 那种「确实为空 / 未对学生开放」用侧栏文件页的同一句措辞，比笼统的 hub.tab_empty.files 准。
    // 两条分支各写一遍取词调用、而不是把它塞进三元表达式：check_i18n_keys.mjs 的悬空引用扫描
    // 只认「t 后面紧跟一个引号字面量」这种形状，写成条件表达式后那两个键就查不到定义了。
    // （同理，本注释里也不能出现 t 加引号的字样 —— 那个扫描读的是原文，连注释一起读。）
    box.innerHTML = c.no_files
      ? `<div class="muted">${t("files.no_files")}</div>`
      : `<div class="muted">${t("hub.tab_empty.files")}</div>`;
    return;
  }
  // 上段：文件夹树，文件行不带勾选框（点文件名仍是 openFileSmart 直开）
  const row = (f, depth) => `
    <div class="item" style="padding-left:${depth*14}px">
      <div>
        <div class="item-title"><a href="#" class="file-open" data-cid="${cid}" data-fid="${escAttr(f.file_id)}" data-name="${escAttr(f.display_name)}" title="${escAttr(t("file.open"))}">${esc(f.display_name)}</a> <span class="muted">（${esc(f.content_type || "")}）</span>${f.saved ? ` <span class="file-saved">${esc(t("files.saved"))}</span>` : ""}</div>
        <div class="file-path">${esc(f.path || "/")}</div>
      </div>
    </div>`;
  const tree = buildFileTree(files, folders);
  const treeHtml = treeRows(tree, 0, expandedFolders, (fid)=>`${cid}:${fid}`, row);
  // 下段：还没下载的，可勾选下载
  const missing = files.filter(f => !f.saved);
  const panel = missing.length ? `
    <div class="hub-dl-sep"></div>
    <div class="sub-label">${t("files.undownloaded")} (${missing.length})</div>
    <div class="hub-dl-list">${missing.map(f => `
      <label class="hub-dl-row"><input type="checkbox" class="hfl" data-fid="${escAttr(f.file_id)}">
        <span class="hub-dl-name">${esc(f.display_name)}</span>
        <span class="hub-dl-path">${esc(f.path || t("files.root"))}</span></label>`).join("")}</div>
    <div class="hub-dl-go-wrap"><button type="button" class="btn btn-primary hub-dl-go" disabled>${esc(t("btn.download"))}</button></div>` : "";
  box.innerHTML = treeHtml + panel;
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
  if (!g){
    /* 批量同步只覆盖「已勾选课程」（syncAnnouncements 的 course_ids 取自 selectedCourses()），
       未勾选的课点多少次「加载课程」也填不上它 —— 所以这里必须区分两种空态，
       各自给对动作，而不是一律叫人去点「加载课程」（M22）。 */
    // 两条分支同样分开写取词调用（理由见 renderHubFiles 里那段注释）
    box.innerHTML = selectedCourses().includes(cid)
      ? `<div class="muted">${t("hub.tab_empty.announce_unloaded")}</div>`
      : `<div class="muted">${t("hub.tab_empty.announce_unchecked")}</div>`;
    return;
  }
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
  if (!todoLoaded){ box.innerHTML = `<div class="muted">${t("hub.tab_empty.todo_unloaded")}</div>`; return; }
  const all = todoItems || [];
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

/* ---------- 图书馆检索（第一阶段，零登录） ---------- */

/* 会话状态 `libraryState` 与 `LIBRARY_MAX_PAGES` 都声明在文件上部（applyLang() 那一段
   之前）—— 原因见那里的注释。两者都在 renderLibraryPage 的传递调用图上，放在这里会被
   顶层 applyLang() 提前读到。 */

function initLibraryTab(){
  if (libraryState.loaded) return;      // 只绑一次
  libraryState.loaded = true;

  /* 两个入口给的是**两种不同语义**，所以要显式区分（见 runLibrarySearch 的注释）：
     检索按钮/回车 = 用户给了新输入，必须重新解析；上/下一页 = 同一份结果，绝不重解析。 */
  $("librarySearchBtn").onclick = () => runLibrarySearch(1, "query");
  $("libraryQuery").addEventListener("keydown", e => {
    if (e.key === "Enter") runLibrarySearch(1, "query");
  });
  $("libraryPrev").onclick = () => runLibrarySearch(libraryState.page - 1, "page");
  $("libraryNext").onclick = () => runLibrarySearch(libraryState.page + 1, "page");
  loadLibraryAreas();
}

/* 领域清单：拿不到就**整个不渲染选择器**（spec §4.3，用户裁定）。
   不显示空下拉、不显示禁用态、不显示报错 —— 就当这个功能不存在。
   判据只有 `available` 一个：空 areas 与抓取失败不是一回事，前端不去猜。 */
async function loadLibraryAreas(){
  const r = await api("library/areas", {});
  libraryState.areas = (r.ok && r.available && r.areas) ? r.areas : [];
  renderLibraryAreas();
}

function renderLibraryAreas(){
  const sel = $("libraryArea");
  const prev = sel.value;
  if (!libraryState.areas.length){ sel.hidden = true; return; }
  sel.innerHTML = `<option value="">${t("library.area_any")}</option>` +
    libraryState.areas.map(a => `<option value="${escAttr(a.code)}">${esc(a.name)}</option>`).join("");
  /* 切语言会重跑这里。用户选的领域不能因为一次语言切换就被重置。 */
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  sel.hidden = false;
}

/* 切语言时由 applyLang() 调用：只重画内存里已有的东西，**不发起网络请求**。
   没有这一条，结果列表/页码/领域下拉会一直停在旧语言，直到重启应用
   —— 与 selTodoAlert 那个旧bug 是同一类（见 i18n.js 里的注释）。 */
function renderLibraryPage(){
  renderLibraryAreas();
  if (!libraryState.last) return;
  renderLibraryBlock($("libraryLocal"), libraryState.last.local);
  renderLibraryBlock($("libraryExternal"), libraryState.last.external);
  renderLibraryParsed(libraryState.last.parsed);
  renderLibraryWarnings(libraryState.last.warnings);
  renderLibraryPager(libraryState.last);
}

/* `mode` 是**入口**，不是「有没有 parsed」：
     "query" = 检索按钮 / 回车 —— 用户给了新输入 → 发 text，后端重新解析；
     "page"  = 上一页 / 下一页 —— 同一份结果换个页码 → 原样回传 parsed，绝不重解析。
   判据必须是「谁发起的」，不能是 `if (libraryState.parsed)`：用户先搜了一次（parsed 已
   有值）、再改输入框搜新问题，按「有 parsed 就用 parsed」就会**拿上一次的解析结果去搜
   新问题** —— 比原本的缺陷更糟（用户以为在搜新东西，实际搜的是旧的）。
   反过来，按 `page > 1` 分流同样是错的：从第 2 页点「上一页」回到 page=1 会退回解析
   分支，既违反「翻页绝不重新调 LLM」，又多花一次真实 LLM 的钱，还会让结果集中途漂移。 */
async function runLibrarySearch(page, mode){
  if (page < 1) return;
  /* 请求在途就忽略这一次触发：**第 1 页请求会调用真实 LLM**，连点两下就是两次真实花费。
     回车与按钮走的是同一个函数，所以守卫必须在这里，光把按钮置灰挡不住回车。
     本仓同类写法见 loadTerms() 的 termsLoading 守卫。 */
  if (libraryState.busy) return;

  /* 只带 LLM 三件套 + 本页参数。**绝不带 language**（本阶段硬约束 R18）：
     规格 §6 的编译规则是穷举式的、没有 language 条，「用任意语言描述需求」
     指的是 text 本身由 LLM 处理，不是让用户选语言。canvas_url / canvas_token
     与检索无关，一并从略。 */
  const s = settings();
  const body = { llm_base_url: s.llm_base_url, llm_api_key: s.llm_api_key,
                 llm_model: s.llm_model, page };

  if (mode === "query"){
    /* 新输入 → 解析路径：发原文，后端调 LLM。 */
    body.text = $("libraryQuery").value.trim();
    const area = $("libraryArea").hidden ? "" : $("libraryArea").value;
    /* 空输入的早退放在**清 parsed 之前**：这一次没有真要发出去的请求，
       就不该把状态改掉 —— 否则屏幕上还挂着上一次的结果、翻页却已经没得可翻
       （点了没反应，也不报错）。 */
    if (!body.text){ setStatus(t("library.need_input"), "err"); return; }
    if (area) body.area = area;
    /* 上一次的 parsed **必须在发请求前清掉**：这一份对应的是上一个问题，留着它，
       这次失败之后（或任何一次翻页）就会被当成当前查询的解析结果用出去。 */
    libraryState.parsed = null;
  } else {
    /* 翻页 → 复用路径：原样回传 parsed，后端不调 LLM（spec §5.3 的加粗不变量）。
       **包括翻回第 1 页**：第 1 页也走这里，只有用户重新检索才会再次解析。 */
    if (!libraryState.parsed) return;      // 没有解析结果就没有可翻的页
    body.parsed = libraryState.parsed;
  }

  $("libraryLocal").textContent = t("library.loading");
  $("libraryExternal").textContent = t("library.loading");

  /* 在途标记只圈住请求本身（上面几处早退还没置位，不会漏成永久锁）。 */
  libraryState.busy = true;
  $("librarySearchBtn").disabled = true;
  let r;
  try {
    r = await api("library/search", body);
  } finally {
    libraryState.busy = false;
    $("librarySearchBtn").disabled = false;
  }

  /* r.ok 为 undefined 时也走这里（后端 pydantic 校验失败返 422、体是 {detail:[…]}，
     没有 ok/error —— 全应用既有约定，本阶段不改后端），所以错误分支不许依赖
     r.error 存在。 */
  if (!r.ok){
    setStatus(r.error || "error", "err");
    /* 失败要**连内存状态一起复位**，光清 DOM 不够：只清 DOM 的话，用户一点切换语言，
       applyLang → renderLibraryPage 就会把**上一次检索的旧结果**重新画出来 ——
       当前明明是一次失败，屏幕上却"复活"了陈旧结果。
       parsed 一并清掉：留着它，下一次翻页就会拿这次**失败查询**的解析结果去检索。 */
    libraryState.last = null;
    libraryState.parsed = null;
    $("libraryLocal").textContent = "";
    $("libraryExternal").textContent = "";
    $("libraryParsed").hidden = true;
    $("libraryWarnings").hidden = true;
    $("libraryPager").hidden = true;
    return;
  }

  /* 只有解析路径才更新 parsed（翻页路径原样带回的就是后端归一化后的同一份，
     不需要、也不该在这里重设）。 */
  if (mode === "query") libraryState.parsed = r.parsed;
  libraryState.page = page;
  libraryState.last = r;
  renderLibraryPage();
}

/* 「理解为：…」+ spec §6 的补偿：`parsed.areas` 一并回显。
   area 在 §4.1 修正后**只是提示词**（代码不强制、LLM 可以无视），所以唯一能让人
   看见「LLM 到底有没有照做」的窗口就是这里。没有这一行，用户选了领域、界面看着
   生效，实际被无视也无人能知 —— 那正是 §6 要求回显的原因。
   **这份 areas 必须是 LLM 的原样输出**：后端两处代码回填（prompt 侧与端点侧）已删，
   否则用户选的 area 会永远出现在这里，「照做」与「被无视」显示得一模一样，
   这个观察面等于不存在。areas 的值来自 LLM ⇒ **入 DOM 必须转义**（本文件统一纪律：
   文本位 esc()、属性位 escAttr()；本分支曾因 area 下拉漏转义出过真 XSS）。 */
function renderLibraryParsed(parsed){
  const el = $("libraryParsed");
  const text = parsed && parsed.explanation;
  const areas = (parsed && parsed.areas) || [];
  /* 两个都空才整个不显示：LLM 降级时 explanation 与 areas 都是空的（没有 LLM 答案，
     不该凭空显示什么），降级本身由 `llm_degraded` 警告那行负责可见。 */
  if (!text && !areas.length){ el.hidden = true; return; }
  let html = "";
  if (text) html += esc(t("library.parsed", { s: text }));
  if (areas.length){
    if (html) html += " ";
    html += esc(t("library.areas", { s: areas.join(", ") }));
  }
  el.innerHTML = html;
  el.hidden = false;
}

/* 警告是**码**不是句子，在这里翻译 —— 后端回句子的话英文界面会冒中文。 */
function renderLibraryWarnings(warnings){
  const el = $("libraryWarnings");
  if (!warnings || !warnings.length){ el.hidden = true; return; }
  el.textContent = warnings.map(w => t("library.warn." + w)).join(" · ");
  el.hidden = false;
}

/* 上游字符串入 DOM 的纪律：**文本位一律 esc()，属性位一律 escAttr()**。
   这些书名/作者/期刊来自图书馆 API，直接拼进 innerHTML 等于让第三方数据决定 DOM。
   it.url 虽由后端 record_url() 用固定模板拼出、两处插值都过 quote()（理论上引号
   进不来），这里仍按本文件统一写法过 escAttr() —— 属性位不留任何例外。 */
function renderLibraryBlock(el, block){
  if (!block){ el.textContent = ""; return; }
  const count = `<div class="lib-meta">${t("library.count", { n: block.count })}</div>`;
  if (!block.items || !block.items.length){
    el.innerHTML = count + `<div class="lib-meta">${t("library.empty")}</div>`;
    return;
  }
  el.innerHTML = count + block.items.map(it => {
    const meta = [it.creator, it.year, it.journal].filter(Boolean).join(" · ");
    const title = it.url
      ? `<a href="${escAttr(it.url)}" target="_blank" rel="noopener">${esc(it.title || it.record_id)}</a>`
      : `${esc(it.title || "—")} <span class="lib-meta">${t("library.no_link")}</span>`;
    return `<div class="lib-item"><div>${title}</div>
            <div class="lib-meta">${esc(meta)}</div></div>`;
  }).join("");
}

function renderLibraryPager(r){
  const pager = $("libraryPager");
  if (!r){ pager.hidden = true; return; }
  const has = (r.local && r.local.items && r.local.items.length) ||
              (r.external && r.external.items && r.external.items.length);
  if (!has){ pager.hidden = true; return; }
  $("libraryPageInfo").textContent = t("library.page", { p: libraryState.page, m: LIBRARY_MAX_PAGES });
  $("libraryPrev").disabled = libraryState.page <= 1;
  /* has_more 由**后端**算好（spec §5.3）—— 前端不自己推算，否则上限一改
     前端就骗人。两栏任何一个还有下一页就允许继续翻。 */
  const more = (r.local && r.local.has_more) || (r.external && r.external.has_more);
  $("libraryNext").disabled = !more;
  pager.hidden = false;
}

/* ---------- 学校邮箱 ---------- */
/* 页面状态 `mailState` 声明在文件上部（applyLang() 那一段、`libraryState` 旁边）——
   理由同上：applyLang() 会经 i18n.js 的钩子调用 renderMail()，状态不能压在文件末尾。 */

function initMailTab(){
  if(mailState.mails.length || mailState.loading){ renderMail(); return; }
  // 首次进页没有遮罩。取信失败**不发登录问询** —— 走的是 IMAP、没有浏览器，
  // 后端那条 error（认证失败文案已指向设置页）由 renderMail() 原样呈现为错误卡。
  return loadMail();
}

/* 返回布尔：是否成功。失败**不发登录问询**，只把后端给的 error 交给 renderMail()。
   这里返回 promise 只是为了让调用方能等到它结束（首次进页那条续延要靠这个）。 */
async function loadMail(){
  if(mailState.loading) return false;
  mailState.loading = true; mailState.error = ""; mailState.groups = null;
  renderMail();
  const r = await api("mail/list", { days: MAIL_DAYS });
  mailState.loading = false;
  if(r.ok !== true){
    mailState.error = r.error || t("mail.load_failed");
    renderMail();
    return false;          // 后端文案（含认证失败的补救指引）已由 renderMail() 呈现
  }
  mailState.mails = r.mails || [];
  renderMail();
  return true;
}

/* 刷新的唯一出口：遮罩内干活。**不在这里发问** —— 取信走 IMAP，没有浏览器、也不依赖
   AIMS 登录态，所以失败时该做的是让用户看到后端那条文案（认证失败已指向设置页，
   spec §11），而不是开一个 AIMS 登录窗。遮罩外不动弹，故也不再需要 withBusy 之外的续延。 */
async function refreshMail(){
  await withBusy(t("mail.refresh"), $("btnMailRefresh"), loadMail);
}

function renderMail(){
  const box = $("mailList");
  if(!box) return;
  const meta = $("mailMeta");
  if(meta){
    /* `mailState.error` 必须在这条判空里：错误卡与计数说的是两件事，同时出现就是骗人
       —— 载入失败时 mails 仍是上一次的结果（loadMail 只在成功路径里覆写它），
       于是「共 5 封」会挂在错误卡旁边，而那份列表已经不在屏幕上了。 */
    meta.textContent = (mailState.loading || mailState.error || !mailState.mails.length) ? ""
      : t("mail.count", { n: mailState.mails.length });
  }
  if(mailState.error){
    box.innerHTML = `<div class="card mail-error">${esc(mailState.error)}</div>`;
    return;
  }
  if(mailState.loading){
    box.innerHTML = `<div class="card mail-empty">…</div>`;
    return;
  }
  if(!mailState.mails.length){
    box.innerHTML = `<div class="card mail-empty">${esc(t("mail.empty", { n: MAIL_DAYS }))}</div>`;
    return;
  }
  box.innerHTML = mailRenderGroups();
}

/* 行上的 `data-m` —— 点开详情时取行的锚（`mailState.mails` 的下标）。
   **只在索引确实是整数时才写**：`groups` 是**模型输出**，`mail_index` 未必是整数
   （后端只保证越界条目被过滤，前端再兜一层）。写成 `data-m="null"` 也能被读取侧的
   `/^\d+$/` 挡住，但一个看起来像值的坏值放在 DOM 里，迟早有人去 `Number()` 它 ——
   不给就是不给。 */
function mailRowAttr(idx){
  return Number.isInteger(idx) ? ` data-m="${idx}"` : "";
}

/* 不再渲染未读标记：来源改为 Gmail 转发件后，「已读」只反映转发账号的收件状态，
   与用户是否处理过无关 —— 一律显示未读是假信息。 */
function mailRowHtml(m, i){
  return `<div class="card mail-row"${mailRowAttr(i)} role="button" tabindex="0"
      title="${escAttr(t("mail.detail_open"))}">
    <div class="mail-row-head"><span class="mail-subject">${esc(m.subject || t("mail.no_subject"))}</span></div>
    <div class="mail-row-sub">${esc(m.sender || "")} · ${esc(fmtLocal(m.received))}</div>
  </div>`;
}

/* ISO8601(UTC) → 本地可读串；解析不了就原样返回，绝不显示 Invalid Date */
function fmtLocal(iso){
  if(!iso) return "";
  const d = new Date(iso);
  if(isNaN(d.getTime())) return iso;
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ---------- 学校邮箱：详情弹层 ---------- */

/* `mailDetailState` 的 `id` 是「当前这一封」的凭据：**迟到的响应不许顶掉后打开的那一封**。
   用户点了 A、又点了 B，A 的响应晚到（IMAP 一次往返可能好几秒）——
   没有这道守卫，弹层会在 B 的标题下面显示 A 的正文，是**静默的错内容**。
   （这个状态本身声明在文件上部 `applyLang();` **之前** —— 理由见那里的注释。） */

function closeMailDetail(){
  const el = $("mailDetail");
  if(el) el.hidden = true;
  mailDetailState.id = "";        // 关掉即作废
}
$("mailDetailClose").onclick = closeMailDetail;
$("mailDetail").querySelector(".modal-backdrop").addEventListener("click", closeMailDetail);

/* 行 → 详情。取行一律按 `data-m`（`mailState.mails` 的下标），**绝不**按渲染顺序或标题反推。 */
function mailRowTarget(e){
  const el = e.target && e.target.closest ? e.target.closest("[data-m]") : null;
  if(!el) return null;
  const raw = el.dataset.m;
  /* 必须过 `/^\d+$/`：`Number("")` 是 0，一个空 `data-m` 会被静默当成第 0 封。 */
  if(!/^\d+$/.test(raw || "")) return null;
  return mailState.mails[Number(raw)] || null;
}

function openMailDetailFromEvent(e){
  const m = mailRowTarget(e);
  if(m) return openMailDetail(m);
}

async function openMailDetail(m){
  if(!m || !m.id) return;
  const id = m.id;
  mailDetailState.id = id;
  $("mailDetailTitle").textContent = m.subject || t("mail.no_subject");
  $("mailDetailMeta").textContent = `${m.sender || ""} · ${fmtLocal(m.received)}`;
  $("mailDetailBody").innerHTML = `<div class="mail-empty">…</div>`;
  $("mailDetailAtts").innerHTML = "";
  $("mailDetail").hidden = false;
  const r = await api("mail/detail", { id });
  if(mailDetailState.id !== id) return;      // 期间又开了别的 / 已关掉 ⇒ 丢弃
  if(r.ok !== true){
    $("mailDetailBody").innerHTML =
      `<div class="card mail-error">${esc(t("mail.detail_failed") + (r.error || ""))}</div>`;
    $("mailDetailAtts").innerHTML = "";
    return;
  }
  renderMailDetail(r.detail || {});
}

/* ---------- 学校邮箱：详情渲染 + 单个附件下载 ---------- */

/* 正文 → 安全 HTML：**先按原始文本切开，再逐段转义**，最后把链接那几段换成 <a>。

   顺序不能反：先 `esc()` 再切，`&` 变成 `&amp;` 会让 `?a=1&b=2` 在 `https?://\S+`
   里断在 `&` 前（链接只剩前半截），而 `&amp;` 里的分号也会被当成正文；
   两次转义还会把 `&amp;amp;` 这类东西显出来。**绝不能把未转义的正文直接塞进 innerHTML。**

   只认 `https?://`：`javascript:` / `data:` 一律不成为链接。三道守卫是**独立**的：
   ① 这里的正则；② `href` 走 `escAttr`；③ 点击走既有的 `openExternal`（自带 /^https?:/i）。 */
function mailLinkify(text){
  if(!text) return "";
  const s = String(text);
  const re = /https?:\/\/[^\s<>"']+/g;      // 局部：带 /g 的正则不能跨调用共享 lastIndex
  let out = "", last = 0;
  for(let m; (m = re.exec(s)); ){
    /* 句末标点不算链接的一部分（`见 https://x.edu/a.` 的句号）——
       不剥的话 href 会带上它，链接照开但落到 404。只剥尾部这些，不做别的解读。 */
    const url = m[0].replace(/[.,;:!?)\]}]+$/, "");
    if(!url) continue;                       // 整段是标点 ⇒ 不是链接（exec 已前进，不会死循环）
    out += esc(s.slice(last, m.index));
    out += `<a href="${escAttr(url)}" rel="noreferrer">${esc(url)}</a>`;
    last = m.index + url.length;             // 剥掉的那几个标点留在原地，下一轮回正文
  }
  return out + esc(s.slice(last));
}

/* 字节数 → 人读串。`null` / 非数字**留白**，**绝不显示「0 B」** ——
   显示 0 B 是把「不知道」说成「是空的」（spec §4：size 解不出给 None）。 */
function fmtBytes(n){
  if(typeof n !== "number" || !isFinite(n) || n < 0) return "";
  if(n < 1024) return `${n} B`;
  if(n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/* 附件清单。`index` 取自条目自己（`a.index`），**不是**渲染序号：
   后端按同一把锚取附件，渲染序号在清单被重排时会与它对不上。取不到就早退，绝不编一个。 */
function mailAttsHtml(atts){
  const label = `<div class="mail-detail-label">${esc(t("mail.attachments"))}</div>`;
  if(!atts.length){
    return `${label}<div class="mail-empty">${esc(t("mail.no_attachments"))}</div>`;
  }
  const rows = atts.map(a => {
    const idx = a && a.index;
    if(!Number.isInteger(idx) || idx < 0) return "";
    return `<div class="mail-att">
      <span class="mail-att-name">${esc(a.name || "")}</span>
      <span class="mail-att-size">${esc(fmtBytes(a.size))}</span>
      <button type="button" class="btn btn-xs" data-att="${idx}"
        >${esc(t("mail.download"))}</button></div>`;
  }).join("");
  return label + rows;
}

/* `detail` 省略时沿用上一次的 —— 语言切换后 `i18n.js` 的 `applyLang` 钩子就**无参**调它
   （范式同 `renderDetail`）。弹层没开、或还没有任何详情 ⇒ 纯 no-op。 */
function renderMailDetail(detail){
  if(detail){
    mailDetailState.detail = detail;
    mailDetailState.detailId = mailDetailState.id;   // 记下这份详情属于哪一封
  }
  const d = mailDetailState.detail;
  const el = $("mailDetail");
  /* 第四条早退：**缓存必须属于当前打开的那一封**。只看 `el.hidden` 不够 ——
     `openMailDetail` 的失败分支从不写缓存，在途期间也还没写，两种情况下缓存里都是上一封，
     照画就会把上一封的正文与附件清单挂在这一封的标题下（点它的下载按钮 = 用这一封的 id
     去取上一封的 index，静默落盘一个与按钮名字无关的文件）。与 `openMailDetail` 那道迟到守卫同一条规矩。 */
  if(!d || !el || el.hidden || mailDetailState.detailId !== mailDetailState.id) return;
  const body = d.body || "";
  $("mailDetailBody").innerHTML = body
    ? `<div class="mail-detail-body">${mailLinkify(body)}</div>`
    : `<div class="mail-empty">${esc(t("mail.empty_body"))}</div>`;
  $("mailDetailAtts").innerHTML = mailAttsHtml(d.attachments || []);
}

/* 单个下载。防重入由 `withBusy` 负责（它在**第一个 await 之前**同步置灰按钮，与写入路径
   同一条纪律）；`index` 与 `id` 都取自**点中的那个按钮与当前打开的那一封**。 */
$("mailDetailAtts").addEventListener("click", async e => {
  const b = e.target.closest("button[data-att]");
  if(!b) return;
  const raw = b.dataset.att;
  if(!/^\d+$/.test(raw || "")) return;
  const id = mailDetailState.id;
  if(!id) return;
  await withBusy(t("mail.download"), b, async () => {
    const r = await api("mail/attachment",
      { id, index: Number(raw), download_dir: downloadDir() });
    if(r.ok === true) setStatus(t("mail.saved_to", { path: r.path || "" }), "ok");
    else setStatus(t("mail.download_failed") + (r.error || ""), "err");
  });
});

$("btnMailRefresh").onclick = () => refreshMail();

/* ---------- 学校邮箱：AI 两级分组 + 逐条写入 ---------- */

/* 通用确认弹层。
   **并发的询问不复用同一个 promise**（裁定 R2）：`askConfirm` 的返回值就是「这一次点击
   要不要写」的答案。若第二个询问直接拿到第一个弹窗的 promise，它的调用方就会收到
   **别人**的确认结果 —— 落在写入路径上，等于替用户写入一条他从没确认过的条目。
   方向必须闭合：拿不准就当取消、绝不擅自写入（与 `!m` 那条同一原则）。
   并发时**弹窗本身仍只开一个**（与 confirmManualLogin 同一范式）。 */
let confirmResolve = null;
function askConfirm(titleText, bodyText){
  const m = $("confirmPrompt");
  if(!m) return Promise.resolve(false);              // 弹层缺失 → 一律当取消，绝不擅自写入
  if(confirmResolve) return Promise.resolve(false);  // 已有询问在开 → 后来者当取消，不搭便车
  confirmResolve = new Promise(resolve => {
    const done = yes => { confirmResolve = null; m.hidden = true; resolve(yes); };
    $("confirmPromptTitle").textContent = titleText;
    $("confirmPromptBody").textContent = bodyText;
    m.querySelector(".modal-backdrop").onclick = () => done(false);
    $("btnConfirmClose").onclick = () => done(false);
    $("btnConfirmNo").onclick    = () => done(false);
    $("btnConfirmYes").onclick   = () => done(true);
    m.hidden = false;
    $("btnConfirmYes").focus();
  });
  return confirmResolve;
}

/* AI 总结：取信 + 两级归类 + 摘要/DDL 都由后端一次做完（POST /api/mail/summarize）。
   失败一律是 HTTP 200 + `{ok:false,error,reason}`（`api()` 的契约），故只判 `ok`，不判状态码。
   `reason` 是后端在失败路径上多报的一个键（`"mail"` = 取信阶段、`"llm"` = AI 阶段）：
   同一个 `except` 把两段合成一条失败通道时前端无法区分，于是 Gmail 认证失败会被
   报成「AI 总结失败」—— 用户照着去查 API key，方向是错的。分流是为了让两段各自
   显示对的那条文案（下面是 setStatus 那一行）；**两段都不发登录问询** —— 取信走
   IMAP、没有浏览器，失败时该看的是设置页，不是 AIMS 登录窗。 */
async function mailSummarize(){
  if(!mailState.mails.length){ setStatus(t("mail.need_refresh"), "err"); return; }
  const s = settings();
  let failReason = "", failMsg = "";
  await withBusy(t("mail.summarize"), $("btnMailSummary"), async () => {
    const r = await api("mail/summarize", {
      llm_base_url: s.llm_base_url, llm_api_key: s.llm_api_key, llm_model: s.llm_model,
      days: MAIL_DAYS, language: LANG() });
    if(r.ok !== true){
      failReason = r.reason || "llm";        // 老后端不带 reason：当 AI 阶段（下面据此选显示哪段文案）
      failMsg = r.error || "";
      setStatus((failReason === "mail" ? t("mail.load_failed") : t("mail.ai_failed")) + failMsg, "err");
      return;
    }
    mailState.mails = r.mails || mailState.mails;
    mailState.groups = r.groups || [];
    /* 警告整列照放，但分隔符跟语言走（裁定 R6）：后端文案是按 language 本地化的，
       硬编码「；」会让英文界面冒出中文标点。分隔符不值得一个 i18n 键 —— R6 说的
       「不新增键」只指这一处（下方写入路径那条 `mail.ddl_incomplete` 不在此列：
       它是写给用户看的一句完整的话，必须双语）。 */
    if((r.warnings || []).length)
      setStatus(r.warnings.join(LANG() === "zh" ? "；" : "; "), "info", 8000);
    renderMail();
  });
  /* 两个阶段都**不**发登录问询：上面的 setStatus 已经把后端那条 error 呈现给用户了。
     取信失败是 Gmail 侧的问题（认证失败文案已指向设置页重填，spec §11），开一个 AIMS
     Chrome 窗口对邮件无效 —— 那正是这份设计要消灭的东西；AI 失败是 API key / 模型服务
     的问题，指去重登 AIMS 是另一种「指错方向」（同 memory 里 AIMS 那条坑）。 */
}

/* 两级分组：课程 → 类型。还没点过 AI 总结（groups 为空）时退回 Task 7 的平铺列表。 */
function mailRenderGroups(){
  const gs = mailState.groups || [];
  if(!gs.length) return mailState.mails.map(mailRowHtml).join("");
  let html = "";
  gs.forEach((g, gi) => {
    html += `<div class="card mail-group"><h3>${esc(g.course || t("mail.group_other"))}</h3>`;
    (g.types || []).forEach((ty, ti) => {
      html += `<div class="mail-type"><h4>${esc(ty.type || t("mail.uncategorized"))}</h4>`;
      (ty.items || []).forEach((it, ii) => {
        /* 对回原文一律按 `mail_index`（= `mailState.mails` 里的下标，后端 enumerate 赋的
           `index`），**绝不靠标题匹配**：同题邮件不止一封，标题不是锚。 */
        const m = mailState.mails[it.mail_index];
        if(!m) return;                    // 越界（后端已过滤，前端再兜一层）
        html += mailItemHtml(it, m, gi, ti, ii);
      });
      html += `</div>`;
    });
    html += `</div>`;
  });
  return html;
}

function mailItemHtml(it, m, gi, ti, ii){
  /* 按钮走既有 `.btn-xs`（裁定 R8）：`app.css:141` 的 `.btn-xs` 是 `padding:2px 8px;
     font-size:11.5px`，与本处要的形态一致（简报的 `.btn-sm` 只差 0.5px 字号），
     不新增近似重复的规则。 */
  /* 元素是**未经校验的模型输出**（后端只保证这两个键是列表），`null` 占位会让 `e.title`
     抛 TypeError，并把 renderMail 变成静默的未处理 rejection（`mailState.groups` 此时已
     赋值 ⇒ 之后每次 renderMail 都跟着炸）。此处**不能先 filter 再 map**：`ei` 就是
     `data-e`，而点击处理器按**原始**下标 `arr[Number(b.dataset.e)]` 取行，过滤会把按钮
     重新编号、取行却仍按原下标 —— 按钮显示的与它写下去的会是两条不同的 DDL。故按原下标早退。

     契约归属：**元素级校验的契约在这里**（下面两条 `!e || typeof e !== "object"`）。
     后端 `_normalize_groups` 只保证 `calendar_events`/`reminders` 这两个键是 list
     （不是 list 就换成 `[]`），**不保证元素形状** —— 元素可以是 `null` 或任意标量。
     别把这段早退当成冗余删掉：删了就是本段开头描述的那种静默未处理 rejection。 */
  const evs = (it.calendar_events || []).map((e, ei) => {
    if(!e || typeof e !== "object") return "";
    return `<div class="mail-ddl"><span>${esc(e.title || "")} · ${esc(e.start || "")}</span>
       <button type="button" class="btn btn-xs" data-w="ev" data-g="${gi}" data-t="${ti}" data-i="${ii}" data-e="${ei}"
         >${esc(t("mail.write_event"))}</button></div>`;
  }).join("");
  const rms = (it.reminders || []).map((e, ei) => {
    if(!e || typeof e !== "object") return "";
    return `<div class="mail-ddl"><span>${esc(e.title || "")} · ${esc(e.due_date || "")}</span>
       <button type="button" class="btn btn-xs" data-w="rm" data-g="${gi}" data-t="${ti}" data-i="${ii}" data-e="${ei}"
         >${esc(t("mail.write_reminder"))}</button></div>`;
  }).join("");
  const ddl = (evs || rms)
    ? `<div class="mail-ddls"><div class="mail-ddl-label">${esc(t("mail.ddl"))}</div>${evs}${rms}</div>`
    : "";
  const rowAttr = mailRowAttr(it.mail_index);
  /* `tabindex` 与 `data-m` 同生共死：没有锚的行点不开详情，也就不该是键盘的焦点停靠点
     （聚焦了按 `Enter` 什么都不会发生，纯粹多一个 Tab 停靠）。 */
  const rowTab = Number.isInteger(it.mail_index) ? ` tabindex="0"` : "";
  /* 分组行**刻意不给** `role="button"`：ARIA 的 button 角色带 presentational children 语义，
     会把行内既有的「写入日历/提醒」`<button data-w=…>` 从辅助技术里抹平 —— 那是拿既有功能
     换新语义。`tabindex` 只给可达性、没有这个副作用，故只给它。磁贴行（`.mail-row`）内部
     没有交互后代，所以它带 `role="button"` 是对的。 */
  return `<div class="mail-item"${rowAttr}${rowTab}>
    <div class="mail-item-subject">${esc(m.subject || t("mail.no_subject"))}</div>
    <div class="mail-item-sum">${esc(it.summary || "")}</div>${ddl}</div>`;
}

$("btnMailSummary").onclick = () => mailSummarize();

/* 逐条写入：每条都先问一次，确认才写。
   邮件里的日期比公告更容易被 AI 提取错，绝不自动批量写。 */
$("mailList").addEventListener("click", async e => {
  const b = e.target.closest("button[data-w]");
  /* 不是写入按钮 ⇒ 可能是点在某一「行」上（打开详情）。**必须留在同一个监听器里**：
     tools/check_mail_frontend.mjs 的 H0b 断言 `#mailList` 上**恰好一个** click 处理器，
     而它守的是「写入按钮的 data-* 取自它自己那一行」。 */
  if(!b) return openMailDetailFromEvent(e);
  const g  = (mailState.groups || [])[Number(b.dataset.g)];
  const ty = g && (g.types || [])[Number(b.dataset.t)];
  const it = ty && (ty.items || [])[Number(b.dataset.i)];
  if(!it) return;
  const isEv = b.dataset.w === "ev";
  const arr = isEv ? (it.calendar_events || []) : (it.reminders || []);
  const row = arr[Number(b.dataset.e)];
  if(!row) return;

  /* 目标日历/提醒列表**先**校验（裁定 R3）：这两样没选就根本写不成，让用户先确认、
     再被告知「请先刷新并选择…」—— 那次确认毫无意义（用户确认了一个做不到的动作）。 */
  const cal  = $("selCalendar") ? $("selCalendar").value : "";
  const list = $("selList")     ? $("selList").value     : "";
  if(isEv && !cal){ setStatus(t("status.need_calendar"), "err"); return; }
  if(!isEv && !list){ setStatus(t("status.need_list"), "err"); return; }

  /* 必填字段**先**校验（裁定 R4 / F3）：`AddEventRequest` 的 calendar_name/title/start/end
     无默认值，`undefined` 会被 `JSON.stringify` 丢掉 → FastAPI 422 → `r.ok` 与
     `r.error` 双双 undefined → 用户只看到「写入失败：」后面空空如也。
     且必须**是字符串**：只判真值会让 `{"title": 3}` 这类非串通过前端、被 Pydantic 以
     `string_type` 挡成 422，用户又只看到那条空白的「写入失败：」—— 同样的病。
     空串也拦（真值判断即已覆盖，这里显式写出以免被误「简化」掉）。 */
  const isStr = v => typeof v === "string" && v !== "";
  if(!isStr(row.title) || (isEv ? !isStr(row.start) : !isStr(row.due_date))){
    setStatus(t("status.write_fail") + t("mail.ddl_incomplete"), "err"); return;
  }

  /* 防重入（裁定 R5）：`b.disabled = true` 必须在**第一个 await 之前**落地。
     否则双击会在第一次请求返回前发出两次 POST，同一条 DDL 写两遍。
     取消/失败都要把它恢复，否则一次取消会把按钮永久锁死。 */
  b.disabled = true;

  const label = isEv ? `${row.title} · ${row.start || ""}` : `${row.title} · ${row.due_date || ""}`;
  if(!await askConfirm(t(isEv ? "mail.write_event" : "mail.write_reminder"), label)){
    b.disabled = false;                 // 取消 → 按钮恢复可用
    return;
  }
  const r = isEv
    ? await api("add_calendar_event", { calendar_name: cal, title: row.title,
        start: row.start, end: row.end || row.start, location: row.location || "",
        notes: row.notes || "" })
    : await api("add_reminder", { list_name: list, title: row.title,
        due_date: row.due_date, notes: row.notes || "" });
  if(r.ok === true) setStatus(t("mail.wrote", { n: 1 }), "ok");    // 写成 → 保持置灰
  else { setStatus(t("status.write_fail") + (r.error || ""), "err"); b.disabled = false; }
});

/* 键盘可达：`Enter`/`Space` 与点击同路（spec §6）。
   `preventDefault` **只在命中行时**调用 —— 无条件吞掉 Space 会让邮件页没法用空格滚动。 */
$("mailList").addEventListener("keydown", e => {
  if(e.key !== "Enter" && e.key !== " ") return;
  /* 行内的交互元素（写入日历/提醒按钮）自己吃 `Enter`/`Space` —— 与 click 路径的
     `closest("button[data-w]")` 早退同一条规矩。少了这行，键盘激活按钮会被本监听器
     劫持：`preventDefault` 吞掉按钮的合成 click，同时弹出邮件详情（`#mailList` 的 click
     处理器有这道排他，键路径**曾经漏了**）。 */
  if(e.target && e.target.closest && e.target.closest("button, a, input, select, textarea")) return;
  const m = mailRowTarget(e);
  if(!m) return;
  e.preventDefault();
  openMailDetail(m);
});

/* ---------- 邮件规则分类（待办 / 时间表） ---------- */
/* 页面状态 `mtState` / `mtRules` 声明在文件上部（`applyLang()` 那一段、`mailState` 旁边）
   —— 理由同上：applyLang() 会经 i18n.js 的钩子调用 renderMailTodo()。 */

function initMailTodoTab(){
  if(mtState.mails.length || mtState.loading){ renderMailTodo(); return; }
  return loadMailTodo();
}

function renderMailTodo(){
  const box = $("mtTodo");
  if(!box) return;
  const meta = $("mtMeta");
  if(meta){
    /* 错误卡与计数说的是两件事，同时出现就是骗人 —— 与 renderMail 同一条纪律：
       载入失败时 mails 仍是上一次的结果，于是「共 5 封」会挂在错误卡旁边。 */
    meta.textContent = (mtState.loading || mtState.error || !mtState.mails.length) ? ""
      : t("mailtodo.count", { n: mtState.mails.length, m: mtTodoCount() });
  }
  renderMailTodoWarnings();
  const sched = $("mtSchedule");
  if(sched) sched.innerHTML = mtScheduleHtml();
  if(mtState.error){
    box.innerHTML = `<div class="card mail-error">${esc(mtState.error)}</div>`;
    return;
  }
  if(mtState.loading){
    box.innerHTML = `<div class="card mail-empty">…</div>`;
    return;
  }
  if(!mtState.mails.length){
    box.innerHTML = `<div class="card mail-empty">${esc(t("mailtodo.empty", { n: mtDays }))}</div>`;
    return;
  }
  box.innerHTML = mtTodoHtml();
}

function mtTodoCount(){ return 0; }              // Task 8 填真值

function renderMailTodoWarnings(){
  const el = $("mtWarn");
  if(!el) return;
  const codes = [...(mtState.warnings || []), ...(mtState.extractWarnings || [])];
  const msgs = codes.map(w => t("mailtodo.warn." + w, { n: mtState.missing }));
  el.hidden = !msgs.length;
  el.textContent = msgs.join(" · ");
}

function mtTodoHtml(){                           // Task 8 填真值
  return `<div class="card mail-empty">${esc(t("mailtodo.need_classify"))}</div>`;
}

function mtScheduleHtml(){                       // Task 9 填真值
  return "";
}

async function loadMailTodo(){                   // Task 8 接后端
  return true;
}

/* 天数选择：改了立刻重算。**坏值回落到 14**，绝不把 NaN/空串原样传给后端
   （后端 `days: int` 收到 NaN 会抛 422，而 422 体是合法 JSON、前端那条 catch 拦不到）。 */
$("mtDaysSel").addEventListener("change", e => {
  const n = Number(e.target.value);
  mtDays = Number.isFinite(n) && n > 0 ? n : 14;
  loadMailTodo();
});
