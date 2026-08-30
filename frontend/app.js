
const $ = (id) => document.getElementById(id);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const KEY = ["canvasUrl","canvasToken","llmBaseUrl","llmApiKey","llmModel","downloadDir"];
const ALERTS = [[0,"无提醒"],[5,"提前 5 分钟"],[10,"提前 10 分钟"],[30,"提前 30 分钟"],[60,"提前 1 小时"],[1440,"提前 1 天"]];

function loadSettings(){ KEY.forEach(k=>{ const v=localStorage.getItem("sc_"+k); if(v) $(k).value=v; }); }
function saveSettings(){ KEY.forEach(k=>localStorage.setItem("sc_"+k, $(k).value)); }
function settings(){
  saveSettings();
  return { canvas_url:$("canvasUrl").value.trim(), canvas_token:$("canvasToken").value.trim(),
           llm_base_url:$("llmBaseUrl").value.trim(), llm_api_key:$("llmApiKey").value.trim(),
           llm_model:$("llmModel").value.trim() };
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
  if(!st || !en){ $("rangePill").textContent = "请选择起止日期"; return; }
  if(st>en){ $("rangePill").textContent = "⚠️ 开始日期晚于结束日期"; return; }
  const days = Math.round((new Date(en)-new Date(st))/86400000)+1;
  $("rangePill").textContent = `${short(st)} → ${short(en)} · 共 ${days} 天`;
}
function range(){
  const st=$("inpStart").value, en=$("inpEnd").value;
  if(!st || !en){ setStatus("请先选择开始和结束日期","err"); return null; }
  if(st>en){ setStatus("开始日期不能晚于结束日期","err"); return null; }
  return { start_date:st, end_date:en };
}

function setStatus(msg, kind, ms){
  const s=$("status");
  s.textContent=msg;
  s.className="banner show banner-"+(kind==="err"?"err":kind==="ok"?"ok":"info");
  clearTimeout(s._t); s._t=setTimeout(()=>s.classList.remove("show"), ms||4500);
}
async function api(path, body){
  let r;
  try { r = await fetch("/api/"+path, { method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body||{}) }); }
  catch(e){ return { ok:false, error:"无法连接后端，请确认服务已启动" }; }
  try { return await r.json(); }
  catch(e){ return { ok:false, error:"后端响应无法解析（HTTP "+r.status+"）" }; }
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
  ALERTS.forEach(([v,label])=>{ const o=document.createElement("option"); o.value=v; o.textContent=label; sel.appendChild(o); });
}
function esc(t){ const d=document.createElement("div"); d.textContent = t==null?"":String(t); return d.innerHTML; }

/* 设置弹窗 */
function openSettings(){ $("settingsModal").hidden=false; setTimeout(()=>$("canvasUrl").focus(), 60); }
function closeSettings(){ $("settingsModal").hidden=true; }
$("btnSettings").onclick = openSettings;
$("btnCloseSettings").onclick = closeSettings;
$("settingsModal").querySelector(".modal-backdrop").addEventListener("click", closeSettings);
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && !$("settingsModal").hidden) closeSettings(); });

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
  if(!s.canvas_url||!s.canvas_token){ setStatus("请填写 Canvas URL 和 Token","err"); return; }
  await withBusy("正在测试连接…", $("btnTest"), async ()=>{
    const r=await api("test_connection", s);
    setStatus(r.ok ? `连接成功，共 ${r.courses.length} 门课程` : "连接失败："+(r.error||""), r.ok?"ok":"err");
  });
};
$("btnLoadCalendars").onclick = async () => {
  await withBusy("正在读取日历与提醒列表…", $("btnLoadCalendars"), async ()=>{
    const [cal, list] = await Promise.all([api("calendars"), api("reminder_lists")]);
    fillSelect("selCalendar", cal.calendars||[]);
    fillSelect("selList", list.lists||[]);
    if(cal.ok===false) setStatus("日历读取失败："+cal.error, "err");
    else if(list.ok===false) setStatus("提醒列表读取失败："+list.error, "err");
    else setStatus(`已刷新：${(cal.calendars||[]).length} 个日历、${(list.lists||[]).length} 个提醒列表`, "ok");
  });
};

let courseList=[], summaryResults=[], displayResults=[], fileCourses=[];
$("btnLoadCourses").onclick = async () => {
  const s=settings();
  if(!s.canvas_url||!s.canvas_token){ setStatus("请先填写 Canvas 配置","err"); return; }
  await withBusy("正在加载课程…", $("btnLoadCourses"), async ()=>{
    const r=await api("courses", s);
    if(!r.ok){ setStatus("加载课程失败："+r.error,"err"); return; }
    courseList=r.courses;
    $("courseCheckboxes").innerHTML = courseList.map(c=>
      `<label class="chip"><input type="checkbox" checked data-id="${c.id}"> ${esc(c.name)}</label>`).join("");
    setStatus(`已加载 ${courseList.length} 门课程`,"ok");
  });
};
function selectedCourses(){ return [...document.querySelectorAll("#courseCheckboxes input:checked")].map(i=>Number(i.dataset.id)); }

$("btnSync").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  const rng=range(); if(!rng) return;
  if(!ids.length){ setStatus("请先勾选要同步的课程","err"); return; }
  await withBusy("正在同步公告并生成总结…", $("btnSync"), async ()=>{
    const r=await api("sync_announcements", { ...s, course_ids:ids, ...rng });
    if(!r.ok){ setStatus("同步失败："+r.error,"err"); return; }
    summaryResults=r.courses;
    if(!$("filterStart").value) $("filterStart").value=rng.start_date;
    if(!$("filterEnd").value) $("filterEnd").value=rng.end_date;
    renderSummaries();
    setStatus(`同步完成：${summaryResults.length} 门课程已总结`,"ok");
    switchTab("tabAnnounce");
  });
};
function renderSummaries(){
  const f = filterVisible();
  displayResults = summaryResults.map(c=>({
    ...c,
    calendar_events:(c.calendar_events||[]).filter(e=>dayWithin(e.start,f)),
    reminders:(c.reminders||[]).filter(e=>dayWithin(e.due_date,f)),
  }));
  if(!displayResults.length){
    $("summaries").innerHTML = `<div class="empty"><span class="big">📭</span>还没有同步结果。先在上方选择时间范围、勾选课程，点击「同步并总结」。</div>`;
    return;
  }
  const evCount = (shown,total) => (f && total>0) ? `${shown}<span class="count"> / ${total}</span>` : `${shown}`;
  $("summaries").innerHTML = displayResults.map((c,ci)=>{
    const evs=c.calendar_events, rms=c.reminders;
    const evTotal=(summaryResults[ci].calendar_events||[]).length;
    const rmTotal=(summaryResults[ci].reminders||[]).length;
    return `
    <div class="course-card">
      <div class="course-name">${esc(c.course_name)}</div>
      ${c.warning?`<div style="color:var(--err);font-size:12.5px;margin-bottom:6px">⚠️ ${esc(c.warning)}</div>`:""}
      <div class="summary">${esc(c.summary_cn)}</div>
      <div class="sub-label">📅 日历事件（${evCount(evs.length,evTotal)}）</div>
      ${evs.map((e,ei)=>`
        <div class="item"><input type="checkbox" class="ev" data-ci="${ci}" data-ei="${ei}">
          <div><div class="item-title">${esc(e.title)}</div>
          <div class="file-path">${esc(e.start)} → ${esc(e.end)}${e.location?` · ${esc(e.location)}`:""}</div></div></div>`).join("")}
      <div class="sub-label">✅ 提醒（${evCount(rms.length,rmTotal)}）</div>
      ${rms.map((e,ei)=>`
        <div class="item"><input type="checkbox" class="rm" data-ci="${ci}" data-ei="${ei}">
          <div><div class="item-title">${esc(e.title)}</div><div class="file-path">截止 ${esc(e.due_date)}</div></div></div>`).join("")}
    </div>`;
  }).join("");
}
$("chkShowAll").addEventListener("change", refreshFilterState);
$("filterStart").addEventListener("change", refreshFilterState);
$("filterEnd").addEventListener("change", refreshFilterState);

$("btnWriteCalendar").onclick = async () => {
  const cal=$("selCalendar").value;
  const amVal = $("selAlert").value ? Number($("selAlert").value) : null;
  if(!cal){ setStatus("请先刷新并选择要写入的日历","err"); return; }
  const evs=[...document.querySelectorAll(".ev:checked")].map(i=>{
    const c=displayResults[Number(i.dataset.ci)]; return c.calendar_events[Number(i.dataset.ei)]; });
  if(!evs.length){ setStatus("没有选中要写入的日历事件","err"); return; }
  await withBusy(`正在写入 ${evs.length} 条日历事件…`, $("btnWriteCalendar"), async ()=>{
    let n=0;
    for(const e of evs){
      const r=await api("add_calendar_event",{ calendar_name:cal, title:e.title, start:e.start,
        end:e.end, location:e.location||"", notes:e.notes||"", alert_minutes:amVal });
      if(r.ok) n++; else setStatus("写入失败："+r.error,"err");
    }
    const alertLabel = amVal ? "，已设提醒" : "";
    setStatus(`已写入 ${n}/${evs.length} 条日历事件${alertLabel}`, n===evs.length?"ok":"err");
  });
};
$("btnWriteReminders").onclick = async () => {
  const list=$("selList").value;
  if(!list){ setStatus("请先刷新并选择提醒列表","err"); return; }
  const rms=[...document.querySelectorAll(".rm:checked")].map(i=>{
    const c=displayResults[Number(i.dataset.ci)]; return c.reminders[Number(i.dataset.ei)]; });
  if(!rms.length){ setStatus("没有选中要写入的提醒","err"); return; }
  await withBusy(`正在写入 ${rms.length} 条提醒…`, $("btnWriteReminders"), async ()=>{
    let n=0;
    for(const e of rms){
      const r=await api("add_reminder",{ list_name:list, title:e.title, due_date:e.due_date, notes:e.notes||"" });
      if(r.ok) n++; else setStatus("写入失败："+r.error,"err");
    }
    setStatus(`已写入 ${n}/${rms.length} 条提醒`, n===rms.length?"ok":"err");
  });
};

$("btnListFiles").onclick = async () => {
  const s=settings(), ids=selectedCourses();
  if(!ids.length){ setStatus("请先勾选课程","err"); return; }
  switchTab("tabFiles");
  await withBusy("正在加载文件列表…", $("btnListFiles"), async ()=>{
    const r=await api("list_files",{ ...s, course_ids:ids, download_dir:downloadDir() });
    if(!r.ok){ setStatus("加载文件失败："+r.error,"err"); return; }
    fileCourses=r.courses; renderFiles();
    setStatus(`已加载 ${fileCourses.length} 门课程的文件`,"ok");
  });
};
function renderFiles(){
  const filter=$("inpTypeFilter").value.toLowerCase().trim().replace(/^\./,"");
  const shown=fileCourses.map(c=>({...c, files:(c.files||[]).filter(f=>{
    if(!filter) return true;
    return (f.content_type||"").toLowerCase().includes(filter)
        || (f.display_name||"").toLowerCase().endsWith("."+filter);
  })}));
  $("filesArea").innerHTML = shown.map((c,ci)=>`
    <div class="course-card">
      <div class="course-name">${esc(c.name)} ${c.error?`<span style="color:var(--err);font-size:12px">（${esc(c.error)}）</span>`:""}</div>
      ${(c.files||[]).map(f=>`
        <div class="item"><input type="checkbox" class="fl" data-ci="${ci}" data-fi="${f.file_id}">
          <div><div class="item-title">${esc(f.display_name)} <span class="muted">（${esc(f.content_type)}）</span></div>
          <div class="file-path">${esc(f.path||"/")}</div></div></div>`).join("")}
    </div>`).join("") || "<div class='muted' style='padding:12px 0'>没有匹配的文件</div>";
}
$("inpTypeFilter").oninput = renderFiles;
$("btnSelectAllFiles").onclick = ()=>document.querySelectorAll(".fl").forEach(i=>i.checked=true);
$("btnDownloadFiles").onclick = async () => {
  const s=settings();
  const items=[...document.querySelectorAll(".fl:checked")].map(i=>{
    const c=fileCourses[Number(i.dataset.ci)];
    const f=c.files.find(x=>x.file_id===Number(i.dataset.fi));
    return { course_id:c.course_id, file_id:f.file_id, dest_path:f.dest_path }; });
  if(!items.length){ setStatus("没有选中要下载的文件","err"); return; }
  await withBusy(`正在下载 ${items.length} 个文件…`, $("btnDownloadFiles"), async ()=>{
    const r=await api("download_files",{ ...s, download_dir:downloadDir(), items });
    if(!r.ok){ setStatus("下载失败："+r.error,"err"); return; }
    const failed=(r.failed||[]).length;
    setStatus(`下载完成：成功 ${r.downloaded.length}，失败 ${failed}`, failed===0?"ok":"err");
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
  if(r.ok!==true){ setBanwebStatusText("无法连接后端：网关检测失败","err"); return; }
  const loginBtn=$("btnBanwebLogin");
  if(r.status==="logged_in"){
    setBanwebStatusText("已登录 AIMS ✓","ok");
    loginBtn.hidden=true;
    stopBanwebPoll();
    if(!$("selTerm").options.length) loadTerms();
  } else if(r.status==="needs_login"){
    setBanwebStatusText("已退登：点「重新登录」打开登录窗口，登录后自动继续…","err");
    loginBtn.hidden=false;
    startBanwebPoll();
  } else if(r.status==="opening"){
    setBanwebStatusText("正在打开 Chrome 登录窗口…","muted");
    loginBtn.hidden=true;
    startBanwebPoll();
  } else {
    setBanwebStatusText("状态未知，稍后重试…","err");
    loginBtn.hidden=true;
    startBanwebPoll();
  }
}
$("btnBanwebLogin").onclick = async () => {
  await withBusy("正在打开登录窗口…", $("btnBanwebLogin"), async ()=>{
    const r=await api("banweb/open_login");
    if(r.ok===true) setStatus("已打开 AIMS 登录窗口，请在新窗口中登录","ok",6000);
    else setStatus("打开登录窗口失败："+(r.error||""),"err");
    checkBanwebStatus();
  });
};
function startBanwebPoll(){ if(banwebPollTimer) return; banwebPollTimer=setInterval(checkBanwebStatus, 3000); }
function stopBanwebPoll(){ if(banwebPollTimer){ clearInterval(banwebPollTimer); banwebPollTimer=null; } }
async function loadTerms(){
  const r=await api("banweb/terms");
  if(r.ok!==true){
    if((r.error||"").includes("尚未登录")){
      setBanwebStatusText("已退登：请在弹出的 Chrome 窗口登录一次，登录后自动继续…","err");
      startBanwebPoll();
    } else {
      // 瞬时故障（如抓取窗口被关）→ 恢复轮询，浏览器重开/登录恢复后会自动重载学期
      setBanwebStatusText("读取学期失败："+(r.error||"")+"，正在自动重试…","err");
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
  if(st==="created") return `<span class="sched-badge ok">已新建</span>`;
  if(st==="updated") return `<span class="sched-badge ok"${old?` title="旧 ${old}"`:""}>已更新${old?`（${old}）`:""}</span>`;
  if(st==="exists") return `<span class="sched-badge exists">已存在 · 跳过</span>`;
  if(st==="error") return `<span class="sched-badge err">写入失败</span>`;
  return "";
}
function renderSchedule(){
  const el=$("schedulePreview");
  const data=banwebSchedule;
  if(!data.courses.length){
    el.innerHTML=`<div class="empty"><span class="big">🗓</span>还没有课表。选好学期后点「抓取课表」。</div>`;
    $("btnWriteSchedule").disabled=true;
    return;
  }
  $("btnWriteSchedule").disabled=false;
  const writableBlocks = new Set(data.courses.filter(c=>(c.meetings||[]).length>0).map(c=>c.code+":"+c.section));
  el.innerHTML = data.courses.map(c=>renderCourseBlock(c, writableBlocks)).join("");
}
function renderCourseBlock(c, writableBlocks){
  const key=c.code+":"+c.section;
  const hasMeetings=(c.meetings||[]).length>0;
  const disabled = !hasMeetings;
  const checked = !disabled && (banwebSchedule.selected.includes(key));
  const res = banwebSchedule.results[key];
  const meetings=(c.meetings||[]).map(m=>
    `<div class="file-path">${esc(m.type||"")} · ${esc(m.days||"")} ${esc(m.time||"")}${m.room?` · ${esc(m.room)}`:""}${m.range?`<br>${esc(m.range)}`:""}${m.instr?` · ${esc(m.instr)}`:""}</div>`).join("");
  return `
  <div class="course-card" style="${disabled?'opacity:.6':''}">
    <div class="course-name">
      <label class="check" style="border:none;padding:0;background:transparent">
        <input type="checkbox" data-key="${esc(key)}" ${checked?"checked":""} ${disabled?"disabled":""}> ${esc(c.code)} ${esc(c.section)} · ${esc(c.course)}
      </label>
      ${!hasMeetings?`<span class="sched-badge muted">无固定上课时间</span>`:schedBadge(res)}
    </div>
    ${meetings?`<div style="margin-top:4px">${meetings}</div>`:""}
  </div>`;
}
function updateSelected(){
  banwebSchedule.selected=[...document.querySelectorAll("#schedulePreview .check input:checked")].map(i=>i.dataset.key);
  saveBanweb();
}
$("btnFetchSchedule").onclick = async () => {
  const term=$("selTerm").value;
  if(!term){ setStatus("请先选择学期","err"); return; }
  await withBusy("正在抓取课表…", $("btnFetchSchedule"), async ()=>{
    const r=await api("banweb/schedule",{ term });
    if(r.ok!==true){ setStatus("抓取失败："+(r.error||""),"err"); return; }
    banwebSchedule={ term, fetchedAt:new Date().toISOString(), courses:r.courses, selected:[], results:{} };
    banwebSchedule.selected = r.courses.filter(c=>(c.meetings||[]).length>0).map(c=>c.code+":"+c.section);
    saveBanweb();
    renderSchedule();
    setStatus(`课表已抓取：${r.courses.length} 个课程块`,"ok");
  });
};
$("schedulePreview").addEventListener("change", (e)=>{ if(e.target.matches("input[type=checkbox]")) updateSelected(); });
$("btnWriteSchedule").onclick = async () => {
  const cal=$("selSchedCalendar").value;
  if(!cal){ setStatus("请先刷新并选择要写入的日历","err"); return; }
  const selected=[...document.querySelectorAll("#schedulePreview .check input:checked")].map(i=>i.dataset.key);
  if(!selected.length){ setStatus("没有选中要写入的课程","err"); return; }
  const amVal = $("selSchedAlert").value ? Number($("selSchedAlert").value) : null;
  await withBusy(`正在同步 ${selected.length} 个课程块…`, $("btnWriteSchedule"), async ()=>{
    const r=await api("banweb/write_calendar",{ calendar_name:cal, courses:banwebSchedule.courses,
      selected, alert_minutes:amVal });
    if(r.ok!==true){ setStatus("写入失败："+(r.error||""),"err"); return; }
    const newRes={}; (r.items||[]).forEach(it=>{
      const block=it.key.split(":").slice(0,2).join(":");
      newRes[block]=it.old_time?{s:it.status,old:it.old_time}:it.status; });
    banwebSchedule.results=newRes;
    saveBanweb();
    renderSchedule();
    setStatus(`同步完成：新建 ${r.created} · 已存在 ${r.exists} · 更新 ${r.updated} · 删除 ${r.removed} · 失败 ${r.errors}`,
      r.errors===0?"ok":"err");
  });
};
$("btnClearSchedule").onclick = () => {
  banwebSchedule={ term:"",fetchedAt:null,courses:[],selected:[],results:{} };
  localStorage.removeItem(BANWEB_KEY);
  renderSchedule();
  setStatus("课表预览已清除","ok");
};
let schedTabInit=false;
async function initScheduleTab(){
  if(schedTabInit) return;
  schedTabInit=true;
  if(!$("selSchedCalendar").options.length){
    const r=await api("calendars");
    fillSelect("selSchedCalendar", r.calendars||[]);
  }
  if(!$("selSchedAlert").options.length) fillAlert("selSchedAlert");
  renderSchedule();
  checkBanwebStatus();
}

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
