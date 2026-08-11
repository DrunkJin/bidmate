const state = { profile: null, notices: [], savedOnly: false, loading: false };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>\"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const money = (value) => value ? new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 }).format(value) + "원" : "금액 미정";
const deadlineText = (days) => days === 0 ? "오늘 마감" : `D-${days}`;

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  let data;
  try { data = await response.json(); } catch { throw new Error("서버 응답을 확인할 수 없습니다."); }
  if (!response.ok) throw new Error(data.error || "요청을 처리하지 못했습니다.");
  return data;
}

function toast(message) {
  const el = $("#toast"); el.textContent = message; el.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
}

function renderSkeleton() {
  $("#noticeList").innerHTML = Array.from({length: 4}, () => `<div class="notice skeleton"><i></i><div><b></b><span></span></div></div>`).join("");
}

async function loadAll() {
  renderSkeleton();
  const [profile, stats, health] = await Promise.all([api("/api/profile"), api("/api/stats"), api("/api/health")]);
  state.profile = profile;
  $("#companyName").textContent = profile.company_name; $("#sidebarCompany").textContent = profile.company_name;
  $("#totalStat").textContent = stats.total; $("#matchStat").textContent = stats.strong_matches; $("#heroMatch").textContent = stats.strong_matches;
  $("#closingStat").textContent = stats.closing_soon; $("#savedStat").textContent = stats.saved; $("#savedBadge").textContent = stats.saved;
  if (health.data_mode === "live-ready") { $("#sourceStatus").textContent = "나라장터 API 연결됨"; $("#sourceDescription").textContent = "동기화 버튼으로 최신 용역 공고를 가져오세요."; $(".live-dot").classList.add("connected"); }
  await loadNotices();
}

async function loadNotices() {
  renderSkeleton();
  const params = new URLSearchParams({ q: $("#searchInput").value.trim(), category: $("#categoryFilter").value, saved: state.savedOnly, sort: $("#sortFilter").value });
  try { state.notices = await api(`/api/notices?${params}`); renderNotices(); } catch (error) { $("#noticeList").innerHTML = `<div class="empty"><strong>공고를 불러오지 못했어요.</strong><span>${esc(error.message)}</span><button onclick="loadNotices()">다시 시도</button></div>`; }
}

function renderNotices() {
  const list = $("#noticeList"); $("#resultSummary").textContent = `조건에 맞는 공고 ${state.notices.length}건`;
  if (!state.notices.length) { list.innerHTML = `<div class="empty"><strong>조건에 맞는 공고가 없어요.</strong><span>검색어나 필터를 바꿔 보세요.</span></div>`; return; }
  list.innerHTML = state.notices.map((n) => `<article class="notice" data-id="${esc(n.id)}" tabindex="0">
    <div class="score ${n.match_score >= 70 ? "high" : ""}" style="--score:${n.match_score * 3.6}deg"><span>${n.match_score}</span></div>
    <div class="notice-main"><div class="notice-meta"><span class="pill">${esc(n.category)}</span><span class="pill ${n.missing_requirements.length ? "warning" : "good"}">${n.missing_requirements.length ? "조건 확인 필요" : "참가 가능"}</span></div><h3>${esc(n.title)}</h3><span class="agency">${esc(n.agency)} · ${esc(n.region)}</span></div>
    <div class="notice-data budget"><span>추정 사업 금액</span><strong>${money(n.budget)}</strong></div>
    <div class="notice-data deadline"><span>마감</span><strong class="${n.days_left <= 3 ? "urgent" : ""}">${deadlineText(n.days_left)} · ${esc(n.deadline.slice(5).replace("-", "."))}</strong></div>
    <button class="save-button ${n.saved ? "saved" : ""}" data-save="${esc(n.id)}" aria-label="${n.saved ? "관심 공고 해제" : "관심 공고 저장"}">${n.saved ? "♥" : "♡"}</button>
  </article>`).join("");
  list.querySelectorAll(".notice").forEach((el) => { el.addEventListener("click", (e) => { if (!e.target.closest("[data-save]")) openNotice(el.dataset.id); }); el.addEventListener("keydown", (e) => { if (e.key === "Enter") openNotice(el.dataset.id); }); });
  list.querySelectorAll("[data-save]").forEach((btn) => btn.addEventListener("click", () => toggleSave(btn.dataset.save)));
}

function openNotice(id) {
  const n = state.notices.find((item) => item.id === id); if (!n) return;
  $("#noticeDetail").innerHTML = `<button class="modal-close" type="button" data-close="noticeDialog" aria-label="닫기">×</button><p class="eyebrow">${esc(n.id)} · ${esc(n.category)}</p><h2>${esc(n.title)}</h2><p class="form-intro">${esc(n.agency)} · ${esc(n.region)}</p>
    <div class="detail-score"><strong>${n.match_score}%</strong><div><b>우리 회사와 이만큼 맞아요</b><p>${n.match_reasons.map(esc).join(" · ") || "회사 조건을 입력하면 분석해 드려요."}</p></div></div>
    <div class="detail-grid"><div><span>사업 금액</span><strong>${money(n.budget)}</strong></div><div><span>마감일</span><strong>${deadlineText(n.days_left)} · ${esc(n.deadline)}</strong></div><div><span>참가 지역</span><strong>${esc(n.region)}</strong></div><div><span>발주 기관</span><strong>${esc(n.agency)}</strong></div></div>
    <h3>준비할 서류</h3><ul class="checklist">${n.documents.map((doc) => `<li>${esc(doc)}</li>`).join("")}</ul><div class="modal-actions"><button class="outline-button" data-detail-save>${n.saved ? "♥ 관심 공고 해제" : "♡ 관심 공고 저장"}</button><button class="primary-button" id="sourceButton">나라장터 원문 확인 ↗</button></div>`;
  $("#noticeDialog").showModal(); $("#noticeDetail [data-close]").onclick = () => $("#noticeDialog").close(); $("#sourceButton").onclick = () => window.open(n.source_url, "_blank", "noopener");
  $("[data-detail-save]").onclick = async () => { await toggleSave(n.id); $("#noticeDialog").close(); };
}

async function toggleSave(id) { try { const result = await api(`/api/notices/${encodeURIComponent(id)}/save`, { method: "POST", body: "{}" }); toast(result.saved ? "관심 공고에 저장했어요." : "관심 공고에서 제외했어요."); await loadAll(); } catch (error) { toast(error.message); } }
function openProfile() { const p = state.profile, form = $("#profileForm"); form.company_name.value = p.company_name; form.region.value = p.region; form.max_budget.value = p.max_budget; form.categories.value = p.categories.join(", "); form.keywords.value = p.keywords.join(", "); $("#profileError").textContent = ""; $("#profileDialog").showModal(); }

$("#profileForm").addEventListener("submit", async (e) => { e.preventDefault(); const form = e.currentTarget, values = (name) => form[name].value.split(",").map((v) => v.trim()).filter(Boolean); const submit = form.querySelector("[type=submit]"); submit.disabled = true; try { await api("/api/profile", { method: "PUT", body: JSON.stringify({ company_name: form.company_name.value.trim(), region: form.region.value, max_budget: Number(form.max_budget.value), categories: values("categories"), keywords: values("keywords") }) }); $("#profileDialog").close(); toast("회사 조건을 반영해 추천을 갱신했어요."); await loadAll(); } catch (error) { $("#profileError").textContent = error.message; } finally { submit.disabled = false; } });
$("#editProfile").onclick = openProfile; $("#profileNav").onclick = openProfile; $("#mobileMenu").onclick = () => $("#sidebar").classList.toggle("open");
document.querySelectorAll("[data-close]").forEach((btn) => btn.addEventListener("click", () => $("#" + btn.dataset.close).close()));
document.querySelectorAll(".nav-item[data-view]").forEach((btn) => btn.addEventListener("click", () => { document.querySelectorAll(".nav-item").forEach((v) => v.classList.remove("active")); btn.classList.add("active"); state.savedOnly = btn.dataset.view === "saved"; $("#listTitle").textContent = state.savedOnly ? "저장한 관심 공고" : "놓치면 아까운 공고"; $("#currentView").textContent = state.savedOnly ? "/ 관심 공고" : "/ 맞춤 공고"; $("#sidebar").classList.remove("open"); loadNotices(); }));
let searchTimer; $("#searchInput").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadNotices, 250); }); $("#categoryFilter").addEventListener("change", loadNotices); $("#sortFilter").addEventListener("change", loadNotices);
document.querySelectorAll("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));
$("#syncButton").addEventListener("click", async (e) => { const button = e.currentTarget, original = button.textContent; button.disabled = true; button.textContent = "동기화 중…"; try { const result = await api("/api/sync", { method: "POST", body: "{}" }); toast(`최신 공고 ${result.count}건을 불러왔어요.`); await loadAll(); } catch (error) { toast(error.message); } finally { button.disabled = false; button.textContent = original; } });
loadAll().then(() => { const categories = [...new Set(state.notices.map((n) => n.category))].sort(); $("#categoryFilter").insertAdjacentHTML("beforeend", categories.map((c) => `<option>${esc(c)}</option>`).join("")); }).catch((error) => { console.error(error); toast("데이터를 불러오지 못했습니다."); });
