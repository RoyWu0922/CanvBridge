/* 壳层：左侧导航、未读徽章、首页、设置页、主题、启动引导。
   依赖 util.js（$ / api / setStatus …）与 app.js（各板块的 init* 函数）。
   必须最后加载。 */

const PAGES = ["home","announce","schedule","todo","files","grades","courses","settings"];

/* 各板块的首次进入初始化（app.js 提供，均为函数声明，运行时可见）。
   注意 settings 也在这里 —— 见 Step 4 第 2 条：openSettings() 不能自己调
   switchPage()，否则 switchPage("settings") → openSettings() → switchPage()
   会无限递归。让它只做「进入设置页后要同步的那几件事」。 */
const PAGE_INIT = {
  schedule: () => initScheduleTab(),
  todo:     () => initTodoTab(),
  grades:   () => initGradesTab(),
  home:     () => initHome(),
  settings: () => openSettings(),
};

let currentPage = "";

function switchPage(name){
  if(!PAGES.includes(name)) name = "home";
  const prev = currentPage;
  currentPage = name;

  PAGES.forEach(p => { const el = $("page-" + p); if (el) el.hidden = (p !== name); });
  $$("#nav .nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === name));

  /* 「切走即已读」：离开公告页 = 已看过其上内容 → 记已读、徽章消失。
     启动落在首页，新公告徽章会一直亮到用户切走那一刻。 */
  if(prev === "announce" && name !== "announce") markAnnounceSeen();
  else if(name === "announce" && !countNewAnnounce()){
    $$("#summaries .item.is-new").forEach(el => el.classList.remove("is-new"));
    $$("#summaries .badge-new").forEach(el => el.remove());
  }

  refreshBadges();
  const init = PAGE_INIT[name];
  if (init) init();
}

$("nav").addEventListener("click", e => {
  const b = e.target.closest(".nav-item");
  if (b) switchPage(b.dataset.page);
});

/* 在侧栏项右侧画/收未读徽标 */
function setNavBadge(page, n){
  const item = document.querySelector(`#nav .nav-item[data-page="${page}"]`);
  if (!item) return;
  let b = item.querySelector(".nav-badge");
  if (n <= 0){ if (b) b.remove(); item.removeAttribute("title"); return; }
  if (!b){ b = document.createElement("span"); b.className = "nav-badge"; item.appendChild(b); }
  b.textContent = n > 99 ? "99+" : String(n);
  b.hidden = false;
  item.title = t(page === "todo" ? "unread.todo" : "unread.announce", { n });
}

/* 更新两个侧栏项的未读徽章（纯展示）。已读动作由 switchPage / loadTodo 触发 */
function refreshBadges(){
  setNavBadge("announce", countNewAnnounce());
  setNavBadge("todo", countNewTodo());
}

/* 启动引导：先落首页，再静默拉数据（不遮罩、失败不报错） */
async function autoLoadOnOpen(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token) return;   // 未配置 → 首页显示引导卡，不发请求
  const r = await api("courses", s);
  if(!r.ok) return;
  courseList = r.courses;
  renderCourseCheckboxes(courseList);
  await syncAnnouncements();                      // 内部会 refreshBadges()
  if (typeof initHome === "function") initHome();
}

/* 首页：卡片式首页（统计卡 + 速览卡），并发拉取且会话内只拉一次 */
let homeLoaded = false;      // 本次会话是否已拉过首页数据
let homeDdlCount = null;     // DDL 计数缓存（null = 尚未拉取，卡片保持「—」）
let homeExamCount = null;    // 考试计数缓存（同上）

/* 点统计卡 / 「更多」跳到对应板块 */
$("page-home").addEventListener("click", e => {
  const go = e.target.closest("[data-goto]");
  if (go) switchPage(go.dataset.goto);
});

/* 显隐首页两张速览卡：未配置 Canvas 时隐藏，配置后恢复（幂等） */
function setHomeListCards(visible){
  $$("#page-home .list-card").forEach(el => { el.hidden = !visible; });
}

async function initHome(){
  const s = settings();
  if(!s.canvas_url || !s.canvas_token){
    $("homeNeedCanvas").hidden = false;
    $("homeStats").hidden = true;
    setHomeListCards(false);
    return;
  }
  $("homeNeedCanvas").hidden = true;
  $("homeStats").hidden = false;
  setHomeListCards(true);

  renderHomeFromCache();          // 先用内存里已有的数据填一遍
  if (!courseList.length) return; // 课程还没加载 → 不拉数据，等 autoLoadOnOpen 加载完再回来（homeLoaded 保持 false）
  if (homeLoaded) return;         // 本次会话已拉过 → 不重复请求
  homeLoaded = true;

  /* 并发拉取；某张卡失败只让那张卡保持「—」，不弹错、不阻断其他卡 */
  await Promise.allSettled([
    (async () => {                                   // 未读公告 + 最新公告列表
      await syncAnnouncements();                     // 内部会 refreshBadges()
      $("statAnnounce").textContent = String(countNewAnnounce());
      renderHomeAnnounceList();
    })(),
    (async () => {                                   // 本周 DDL
      const now = new Date();
      const end = new Date(now); end.setDate(end.getDate() + 30);
      const r = await api("calendar_events", {
        canvas_url: s.canvas_url, canvas_token: s.canvas_token,
        course_ids: selectedCourses(), start_date: fmt(now), end_date: fmt(end) });
      if(r.ok === true && Array.isArray(r.events)){
        const weekEnd = new Date(now); weekEnd.setDate(now.getDate() + 7);
        homeDdlCount = r.events.filter(ev => { const d = new Date(ev.start || ev.due); return d >= now && d <= weekEnd; }).length;
        $("statDdl").textContent = String(homeDdlCount);
      }
    })(),
    (async () => {                                   // 本学期考试
      const r = await api("banweb/exams", {});
      if(r.ok === true && Array.isArray(r.exams)){
        homeExamCount = r.exams.length;
        $("statExam").textContent = String(homeExamCount);
      }
    })(),
  ]);

  renderHomeToday();              // 今日课表（本地缓存，无需网络）
}

/* 今日课程：来自 banwebSchedule 本地缓存（无需网络）。
   AIMS 未登录 → 缓存为空 → 提示去登录；已登录但今日无课 → 另一句提示 */
function renderHomeToday(){
  const today = new Date();
  const letter = ["U","M","T","W","R","F","S"][today.getDay()];   // 0 = 周日，与 DAY_INDEX 同一字母表
  const courses = (banwebSchedule && banwebSchedule.courses) || [];
  if (!courses.length){                       // AIMS 未登录：缓存为空
    $("homeTodayList").innerHTML = `<div class="muted">${t("home.need_aims")}</div>`;
    $("statToday").textContent = "0";
    return;
  }
  const slots = [];
  for (const c of courses)
    for (const m of (c.meetings || [])){
      if (!(m.days_list || []).includes(letter)) continue;
      slots.push({ start: m.start_min, end: m.end_min, room: m.room_short || m.room,
                   label: `${c.code} ${c.section}` });
    }
  slots.sort((a, b) => a.start - b.start);
  $("statToday").textContent = String(slots.length);
  if (!slots.length){ $("homeTodayList").innerHTML = `<div class="muted">${t("home.no_class")}</div>`; return; }
  $("homeTodayList").innerHTML = slots.map(s =>
    `<div class="home-row"><span>${esc(fmtTime(s.start))}–${esc(fmtTime(s.end))}</span>
       <span class="hr-name">${esc(s.label)}</span><em>${esc(s.room || "")}</em></div>`).join("");
}

/* 用内存里已有的结果填（切回首页时立即有内容，不等网络） */
function renderHomeFromCache(){
  $("statAnnounce").textContent = String(countNewAnnounce());
  if (homeDdlCount  !== null) $("statDdl").textContent  = String(homeDdlCount);
  if (homeExamCount !== null) $("statExam").textContent = String(homeExamCount);
  renderHomeAnnounceList();
  renderHomeToday();
}

/* 最新 3 条公告：取自 app.js 的 summaryResults（syncAnnouncements 的产物） */
function renderHomeAnnounceList(){
  const box = $("homeAnnounceList");
  const groups = Array.isArray(summaryResults) ? summaryResults : [];
  const rows = groups
    .flatMap(g => (g.announcements || []).map(a => ({ ...a, course: g.course_name })))
    .sort((a, b) => String(b.posted_at || "").localeCompare(String(a.posted_at || "")))
    .slice(0, 3);
  if (!rows.length){ box.innerHTML = `<div class="muted">${t("home.empty")}</div>`; return; }
  box.innerHTML = rows.map(a =>
    `<div class="home-row"><span class="hr-name">${esc(a.title || "")}</span>
       <em>${esc(short(String(a.posted_at || "").slice(0, 10)))}</em></div>`).join("");
}

switchPage("home");
if($("canvasUrl").value && $("canvasToken").value) autoLoadOnOpen();
