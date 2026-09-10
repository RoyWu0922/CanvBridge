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
  const item = $(`#nav .nav-item[data-page="${page}"]`);
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

/* 首页：Task 6 实现 */
function initHome(){}

switchPage("home");
if($("canvasUrl").value && $("canvasToken").value) autoLoadOnOpen();
