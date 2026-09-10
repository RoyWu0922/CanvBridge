/* 通用工具：DOM 查找、后端调用、状态提示、格式化。
   被 app.js（板块）与 shell.js（壳层）共用。
   依赖 i18n.js 的 t()，须在其之后加载。 */

const $ = (id) => document.getElementById(id);
const $$ = (sel) => [...document.querySelectorAll(sel)];
const KEY = ["canvasUrl","canvasToken","llmBaseUrl","llmApiKey","llmModel","downloadDir"];
const ALERTS = [[0,"alert.none"],[5,"alert.min5"],[10,"alert.min10"],[30,"alert.min30"],[60,"alert.hour1"],[120,"alert.hour2"],[360,"alert.hour6"],[720,"alert.hour12"],[1440,"alert.day1"]];

function loadSettings(){ KEY.forEach(k=>{ const v=localStorage.getItem("sc_"+k); if(v) $(k).value=v; }); }
function saveSettings(){ KEY.forEach(k=>localStorage.setItem("sc_"+k, $(k).value)); }
function settings(){
  saveSettings();
  return { canvas_url:$("canvasUrl").value.trim(), canvas_token:$("canvasToken").value.trim(),
           llm_base_url:$("llmBaseUrl").value.trim(), llm_api_key:$("llmApiKey").value.trim(),
           llm_model:$("llmModel").value.trim(), language:LANG() };
}
function downloadDir(){ saveSettings(); return $("downloadDir").value.trim() || "~/Downloads/Canvas课程文件"; }

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

const fmt = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const short = iso => `${+iso.slice(5,7)}/${+iso.slice(8,10)}`;
