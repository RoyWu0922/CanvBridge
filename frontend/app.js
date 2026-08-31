
const $ = (id) => document.getElementById(id);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const KEY = ["canvasUrl","canvasToken","llmBaseUrl","llmApiKey","llmModel","downloadDir"];
const ALERTS = [[0,"alert.none"],[5,"alert.min5"],[10,"alert.min10"],[30,"alert.min30"],[60,"alert.hour1"],[1440,"alert.day1"]];

function loadSettings(){ KEY.forEach(k=>{ const v=localStorage.getItem("sc_"+k); if(v) $(k).value=v; }); }
function saveSettings(){ KEY.forEach(k=>localStorage.setItem("sc_"+k, $(k).value)); }
function settings(){
  saveSettings();
  return { canvas_url:$("canvasUrl").value.trim(), canvas_token:$("canvasToken").value.trim(),
           llm_base_url:$("llmBaseUrl").value.trim(), llm_api_key:$("llmApiKey").value.trim(),
           llm_model:$("llmModel").value.trim(), language:LANG() };
}
function downloadDir(){ saveSettings(); return $("downloadDir").value.trim() || "~/Downloads/Canvas课程文件"; }

const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const short = iso => `${+iso.slice(5,7)}/${+iso.slice(8,10)}`;
function defaultRange(){
  const now = new Date();
  const start = new Date(now); start.setDate(now.getDate()-7);
  $("inpStart").value = fmt(start);
  $("inpEnd").value = fmt(now);
}
function refreshPill(){
  const st=$("inpStart").value, en=$("inpEnd").value;
  if(!st || !en){ $("rangePill").textContent = t("range.placeholder"); return; }
  if(st>en){ $("rangePill").textContent = t("range.invalid"); return; }
  const days = Math.round((new Date(en)-new Date(st))/86400000)+1;
  $("rangePill").textContent = `${short(st)} → ${short(en)} · ${t("range.days", {n: days})}`;
}
function range(){
  const st=$("inpStart").value, en=$("inpEnd").value;
  if(!st || !en){ setStatus(t("status.need_date"),"err"); return null; }
  if(st>en){ setStatus(t("status.date_invalid"),"err"); return null; }
  return { start_date:st, end_date:en };
}

function setStatus(msg, kind, ms){
  const s=$("status");
  s.textContent=msg;
  s.className="banner show banner-"+(kind==="err"?"err":kind==="ok"?"ok":"info");
  clearTimeout(s._t); s._t=setTimeout(()=>s.classList.remove("show"), ms||4500);
}
async function api(path, body, method){
  let r;
  try { r = await fetch("/api/"+path, { method: method||"POST",
    headers:{"Content-Type":"application/json"}, body: body===undefined?undefined:JSON.stringify(body) }); }
  catch(e){ return { ok:false, error:t("status.backend_fail") }; }
  try { return await r.json(); }
  catch(e){ return { ok:false, error:t("status.parse_fail", {s: r.status}) }; }
}
async function withBusy(text, button, fn){
  $("overlayText").textContent=text;
  $("overlay").classList.add("show");
  if(button) button.disabled=true;
  try { return await fn(); }
  finally { $("overlay").classList.remove("show"); if(button) button.disabled=false; }
}
function fillSelect(id, names){
  const sel=$(id); sel.innerHTML="";
  names.forEach(n=>{ const o=document.createElement("option"); o.value=o.textContent=n; sel.appendChild(o); });
}
function fillAlert(id){
  const sel=$(id||"selAlert");
  sel.innerHTML="";
  ALERTS.forEach(([v,key])=>{ const o=document.createElement("option"); o.value=v; o.textContent=t(key); sel.appendChild(o); });
}
function fillCourseFilter(selId, names){
  const sel = $(selId); const prev = sel.value;
  sel.innerHTML = "";
  const all = document.createElement("option");
  all.value = ""; all.textContent = t(selId === "selAnnounceCourse" ? "announce.filter.all" : "files.filter.all");
  sel.appendChild(all);
  const uniq = [...new Set(names.filter(Boolean))];
  uniq.forEach(n => {
    const o = document.createElement("option"); o.value = o.textContent = n; sel.appendChild(o);
  });
  sel.value = prev && [...sel.options].some(o => o.value === prev) ? prev : "";
  sel.disabled = uniq.length === 0;
}
function esc(t){ const d=document.createElement("div"); d.textContent = t==null?"":String(t); return d.innerHTML; }
function escAttr(t){ return esc(t).replace(/"/g, "&quot;"); }
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

/* 设置弹窗 */
function openSettings(){ $("settingsModal").hidden=false; setTimeout(()=>$("canvasUrl").focus(), 60); refreshAimsUi(); }
function closeSettings(){ $("settingsModal").hidden=true; }
$("btnSettings").onclick = openSettings;
$("btnCloseSettings").onclick = closeSettings;
$("settingsModal").querySelector(".modal-backdrop").addEventListener("click", closeSettings);
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && !$("settingsModal").hidden) closeSettings(); });

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
    if(r.ok !== true){
      setStatus(t("status.aims_login_fail") + (r.error||""), "err", 8000);
      setBanwebStatusText(t("status.aims_need_login_after_fail"), "err");
      return false;
    }
    await checkBanwebStatus();
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
let detailBanweb = null;         // 从课表打开的详情：Banweb 课程块（code/section/crn/credits/course）

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
function closeDetail(){ $("detailModal").hidden = true; }
$("btnCloseDetail").onclick = closeDetail;
$("detailModal").querySelector(".modal-backdrop").addEventListener("click", closeDetail);
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("detailModal").hidden) closeDetail(); });

async function openCourseDetail(canvasId, banwebCourse){
  detailBanweb = banwebCourse || null;
  detailMeetings = detailBanweb ? (detailBanweb.meetings || []) : [];
  detailSummary = "";
  detailCourse = null;
  detailAssignments = [];
  const s = settings();
  if (canvasId && s.canvas_url && s.canvas_token) {
    const r = await api("course_detail", { canvas_url:s.canvas_url, canvas_token:s.canvas_token,
                                           course_id:canvasId });
    if (r.ok === true) detailCourse = r.course;   // 失败则退化为仅 Banweb 数据
    try { await ensureAssignments([canvasId]); }  // 详情作业区数据
    catch (e) { /* 详情仍展示，作业区留空 */ }
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
  let syl = "";
  if (c && c.syllabus_text) {
    syl = `<div class="detail-section"><div class="sub-label">${t("detail.syllabus")}</div>
         <div class="detail-syllabus">${esc(c.syllabus_text)}</div>
         <button id="btnSummarize" class="btn btn-ghost">${t("detail.summarize")}</button>
         ${summaryHtml}</div>`;
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
              : t("detail.no_due")}${a.points_possible != null ? ` · ${esc(String(a.points_possible))} pts` : ""}</div>
        </a>`).join("")
    : c ? `<div class="muted">${t("detail.no_assignments")}</div>`
        : "";
  const banwebOnly = c ? "" : `<div class="detail-banweb-only">${t("detail.banweb_only")}</div>`;
  $("detailBody").innerHTML = `
    <div class="detail-head">${head}</div>
    ${bwMeta ? `<div class="detail-meta">${bwMeta}</div>` : ""}
    ${paceHtml}
    ${links}
    ${profLine}
    ${banwebOnly}
    ${loc}
    ${syl}
    ${c ? `<div class="detail-section"><div class="sub-label">${t("detail.assignments")}</div>${asg}</div>` : ""}`;
}

/* 标签页 */
function switchTab(target){
  $$(".tab").forEach(x=>{ const on=x.dataset.target===target; x.classList.toggle("active",on); x.setAttribute("aria-selected",on); });
  $$(".tab-panel").forEach(p=>{ p.hidden = p.id!==target; });
}
$$(".tab").forEach(b=> b.addEventListener("click", ()=>{
  const target=b.dataset.target;
  switchTab(target);
  if(target==="tabSchedule") initScheduleTab();
}));

/* 事件日期筛选 */
function filterVisible(){
  if($("chkShowAll").checked) return null;          // 显示全部
  const st=$("filterStart").value, en=$("filterEnd").value;
  if(!st && !en) return null;
  return { st, en };
}
function dayWithin(iso, f){
  if(!f) return true;
  const d=(iso||"").slice(0,10);
  if(!d) return false;
  if(f.st && d<f.st) return false;
  if(f.en && d>f.en) return false;
  return true;
}
function refreshFilterState(){
  const all=$("chkShowAll").checked;
  $("filterStart").disabled = all;
  $("filterEnd").disabled = all;
  renderSummaries();
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
    $("courseCheckboxes").innerHTML = courseList.map(c=>
      `<label class="chip"><input type="checkbox" checked data-id="${c.id}"> ${esc(c.name)}</label>`).join("");
    setStatus(t("status.courses_loaded", {n: courseList.length}),"ok");
  });
};
function selectedCourses(){ return [...document.querySelectorAll("#courseCheckboxes input:checked")].map(i=>Number(i.dataset.id)); }

$("btnSync").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  const rng=range(); if(!rng) return;
  if(!ids.length){ setStatus(t("status.need_select_course"),"err"); return; }
  await withBusy(t("status.syncing"), $("btnSync"), async ()=>{
    const r=await api("sync_announcements", { ...s, course_ids:ids, ...rng, language:LANG() });
    if(!r.ok){ setStatus(t("status.sync_fail")+r.error,"err"); return; }
    summaryResults=r.courses;
    if(!$("filterStart").value) $("filterStart").value=rng.start_date;
    if(!$("filterEnd").value) $("filterEnd").value=rng.end_date;
    renderSummaries();
    setStatus(t("status.sync_done", {n: summaryResults.length}),"ok");
    switchTab("tabAnnounce");
  });
};
function renderSummaries(){
  const f = filterVisible();
  fillCourseFilter("selAnnounceCourse", summaryResults.map(c => c.course_name));
  const courseSel = $("selAnnounceCourse").value;
  let src = summaryResults;
  if (courseSel) src = summaryResults.filter(c => c.course_name === courseSel);
  displayResults = src.map((c) => {
    const orig = summaryResults.indexOf(c);
    return { ...c, _orig: orig,
      calendar_events:(c.calendar_events||[]).filter(e=>dayWithin(e.start,f)),
      reminders:(c.reminders||[]).filter(e=>dayWithin(e.due_date,f)) };
  });
  if(!displayResults.length){
    $("summaries").innerHTML = `<div class="empty">${t("announce.empty")}</div>`;
    return;
  }
  const evCount = (shown,total) => (f && total>0) ? `${shown}<span class="count"> / ${total}</span>` : `${shown}`;
  $("summaries").innerHTML = displayResults.map((c,ci)=>{
    const evs=c.calendar_events, rms=c.reminders;
    const evTotal=(summaryResults[c._orig].calendar_events||[]).length;
    const rmTotal=(summaryResults[c._orig].reminders||[]).length;
    return `
    <div class="course-card">
      <div class="course-name">${c.course_id
          ? `<a href="#" class="course-detail-link" data-cid="${c.course_id}">${esc(c.course_name)}</a>`
          : esc(c.course_name)}</div>
      ${c.warning?`<div style="color:var(--err);font-size:12.5px;margin-bottom:6px">${esc(c.warning)}</div>`:""}
      <div class="summary">${esc(c.summary)}</div>
      <div class="sub-label">${t("announce.calendar_events")}（${evCount(evs.length,evTotal)}）</div>
      ${evs.map((e,ei)=>`
        <div class="item"><input type="checkbox" class="ev" data-ci="${ci}" data-ei="${ei}">
          <div><div class="item-title">${esc(e.title)}</div>
          <div class="file-path">${esc(e.start)} → ${esc(e.end)}${e.location?` · ${esc(e.location)}`:""}</div></div></div>`).join("")}
      <div class="sub-label">${t("announce.reminders")}（${evCount(rms.length,rmTotal)}）</div>
      ${rms.map((e,ei)=>`
        <div class="item"><input type="checkbox" class="rm" data-ci="${ci}" data-ei="${ei}">
          <div><div class="item-title">${esc(e.title)}</div><div class="file-path">${t("announce.due")} ${esc(e.due_date)}</div></div></div>`).join("")}
    </div>`;
  }).join("");
}
$("chkShowAll").addEventListener("change", refreshFilterState);
$("filterStart").addEventListener("change", refreshFilterState);
$("filterEnd").addEventListener("change", refreshFilterState);
$("selAnnounceCourse").addEventListener("change", renderSummaries);

$("summaries").addEventListener("click", (e) => {
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
    else { detailSummary = r.summary; renderDetail(); }
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

$("btnListFiles").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  if(!ids.length){ setStatus(t("status.need_course"),"err"); return; }
  switchTab("tabFiles");
  await withBusy(t("status.loading_files"), $("btnListFiles"), async ()=>{
    const r=await api("list_files",{ ...s, course_ids:ids, download_dir:downloadDir() });
    if(!r.ok){ setStatus(t("status.files_fail")+r.error,"err"); return; }
    fileCourses=r.courses; renderFiles();
    setStatus(t("status.files_loaded", {n: fileCourses.length}),"ok");
  });
};
function renderFiles(){
  const filter=$("inpTypeFilter").value.toLowerCase().trim().replace(/^\./,"");
  fillCourseFilter("selFileCourse", fileCourses.map(c => c.name));
  const courseSel = $("selFileCourse").value;
  const shown = fileCourses
    .filter(c => !courseSel || c.name === courseSel)
    .map(c => ({ ...c, _orig: fileCourses.indexOf(c), files:(c.files||[]).filter(f=>{
      if(!filter) return true;
      return (f.content_type||"").toLowerCase().includes(filter)
          || (f.display_name||"").toLowerCase().endsWith("."+filter);
    })}));
  $("filesArea").innerHTML = shown.map((c)=>`
    <div class="course-card">
      <div class="course-name">${esc(c.name)} ${c.error?`<span style="color:var(--err);font-size:12px">（${esc(c.error)}）</span>`:""}</div>
      ${(c.files||[]).map(f=>`
        <div class="item"><input type="checkbox" class="fl" data-ci="${c._orig}" data-fi="${f.file_id}" ${f.saved?"":"checked"}>
          <div><div class="item-title">${esc(f.display_name)} <span class="muted">（${esc(f.content_type)}）</span>${f.saved?` <span class="file-saved">${esc(t("files.saved"))}</span>`:""}</div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`).join("")}
    </div>`).join("") || `<div class='muted' style='padding:12px 0'>${t("files.empty")}</div>`;
}
$("inpTypeFilter").oninput = renderFiles;
$("selFileCourse").addEventListener("change", renderFiles);
$("btnSelectAllFiles").onclick = ()=>document.querySelectorAll(".fl").forEach(i=>i.checked=true);
$("btnDownloadFiles").onclick = async () => {
  const s=settings();
  const items=[...document.querySelectorAll(".fl:checked")].map(i=>{
    const c=fileCourses[Number(i.dataset.ci)];
    const f=c.files.find(x=>x.file_id===Number(i.dataset.fi));
    return { course_id:c.course_id, file_id:f.file_id, dest_path:f.dest_path }; });
  if(!items.length){ setStatus(t("status.no_file"),"err"); return; }
  await withBusy(t("status.downloading", {n: items.length}), $("btnDownloadFiles"), async ()=>{
    const r=await api("download_files",{ ...s, download_dir:downloadDir(), items });
    if(!r.ok){ setStatus(t("status.download_fail")+r.error,"err"); return; }
    const failed=(r.failed||[]).length;
    const sk=(r.skipped||[]).length;
    setStatus(t("status.download_done", {a:r.downloaded.length, b:failed, s:sk}), failed===0?"ok":"err");
    renderFiles();
  });
};

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
    if(!$("selTerm").options.length) loadTerms();
  } else if(r.status==="needs_login"){
    loginBtn.hidden=false;
    if(!aimsAutoTried){
      // 已存凭据 → 静默自动登录一次（不弹窗）；未存 → 提示手动
      const cr = await api("banweb/credentials/status", undefined, "GET");
      if(cr.ok===true && cr.has_credentials){
        aimsAutoTried = true;
        setBanwebStatusText(t("status.aims_logging_in"), "muted");
        await runAutoLogin(false);
        return;   // runAutoLogin 成功时已重查状态
      }
      aimsAutoTried = true;
    }
    setBanwebStatusText(t("status.banweb_need_login"),"err");
    startBanwebPoll();
  } else if(r.status==="opening"){
    setBanwebStatusText(t("status.banweb_opening"),"muted");
    loginBtn.hidden=true;
    startBanwebPoll();
  } else {
    setBanwebStatusText(t("status.banweb_unknown"),"err");
    loginBtn.hidden=true;
    startBanwebPoll();
  }
}
$("btnBanwebLogin").onclick = async () => {
  await withBusy(t("status.opening_login"), $("btnBanwebLogin"), async ()=>{
    const r=await api("banweb/open_login");
    if(r.ok===true) setStatus(t("status.login_opened"),"ok",6000);
    else setStatus(t("status.login_fail")+(r.error||""),"err");
    checkBanwebStatus();
  });
};
function startBanwebPoll(){ if(banwebPollTimer) return; banwebPollTimer=setInterval(checkBanwebStatus, 3000); }
function stopBanwebPoll(){ if(banwebPollTimer){ clearInterval(banwebPollTimer); banwebPollTimer=null; } }
async function loadTerms(){
  const r=await api("banweb/terms");
  if(r.ok!==true){
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
  const sel=$("selTerm"); sel.innerHTML="";
  (r.terms||[]).forEach(t=>{ const o=document.createElement("option"); o.value=t.value; o.textContent=t.label; sel.appendChild(o); });
  if(banwebSchedule.term){
    const match=[...sel.options].find(o=>o.value===banwebSchedule.term);
    if(match) sel.value=match.value;
  }
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
const PALETTE = ["#2563eb","#0891b2","#7c3aed","#db2777","#ea580c",
                 "#16a34a","#ca8a04","#dc2626","#4f46e5","#0d9488"];
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
  renderSchedule();
  checkBanwebStatus();
}

$("btnLang").onclick = () => {
  localStorage.setItem("sc_lang", LANG() === "zh" ? "en" : "zh");
  applyLang();
};
applyLang();
loadSettings();
defaultRange();
fillSelect("selCalendar", []); fillSelect("selList", []);
fillAlert();
renderSchedule();
$("inpStart").addEventListener("change", refreshPill);
$("inpEnd").addEventListener("change", refreshPill);
refreshPill();
$("filterStart").value=$("inpStart").value;
$("filterEnd").value=$("inpEnd").value;
refreshFilterState();
