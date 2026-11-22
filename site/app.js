(() => {
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const DATA_URL = DEMO ? "data/demo.json" : "data/promos.json";
  const TZ = "Asia/Bangkok";
  const state = { cat: "all", type: "all", airline: "all", q: "", sort: "new", todayOnly: false };
  let data = { items: [], airlines: {}, status: [] };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bkkDate = (d) => new Date(d).toLocaleDateString("en-CA", { timeZone: TZ });
  const todayStr = bkkDate(Date.now());
  const thDate = (iso) => new Date(iso + (iso.length === 10 ? "T00:00:00+07:00" : ""))
    .toLocaleDateString("th-TH", { timeZone: TZ, day: "numeric", month: "short", year: "2-digit" });
  const daysLeft = (iso) => Math.round((new Date(iso + "T00:00:00+07:00") - new Date(todayStr + "T00:00:00+07:00")) / 864e5);
  const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : "#";

  function airlineName(id) {
    const a = data.airlines[id];
    return a ? a.name_th || a.name : id;
  }

  const catOf = (id) => data.airlines[id]?.category || "airline";
  const inCat = (i) => state.cat === "all" || catOf(i.airline) === state.cat;

  function filtered() {
    const q = state.q.trim().toLowerCase();
    let items = data.items.filter((i) => inCat(i) &&
      (state.type === "all" || i.type === state.type) &&
      (state.airline === "all" || airlineName(i.airline) === airlineName(state.airline)) &&
      (!state.todayOnly || bkkDate(i.first_seen) === todayStr) &&
      (!q || `${i.title} ${i.description} ${airlineName(i.airline)} ${data.airlines[i.airline]?.name || ""}`.toLowerCase().includes(q)));
    if (state.sort === "deadline") {
      items = items.slice().sort((a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999"));
    } else {
      items = items.slice().sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""));
    }
    return items;
  }

  function card(i) {
    const isNew = bkkDate(i.first_seen) === todayStr;
    const left = i.deadline ? daysLeft(i.deadline) : null;
    const badges = [
      `<span class="badge ${i.type === "draw" ? "draw" : ""}">${i.type === "draw" ? "🎁 ชิงโชค" : (catOf(i.airline) === "airline" ? "🎫 โปรตั๋ว" : "🏷️ โปรโมชั่น")}</span>`,
      isNew ? `<span class="badge new">ใหม่วันนี้</span>` : "",
      left !== null && left <= 3 ? `<span class="badge soon">${left <= 0 ? "หมดเขตวันนี้" : `เหลือ ${left} วัน`}</span>` : "",
    ].join("");
    const thumb = i.image
      ? `<img src="${esc(safeUrl(i.image))}" alt="" loading="lazy" onerror="this.remove()">`
      : (i.type === "draw" ? "🎁" : (catOf(i.airline) === "airline" ? "✈️" : "🏷️"));
    return `<a class="card" href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener">
      <div class="thumb">${thumb}</div>
      <div class="body">
        <div class="meta">${badges}</div>
        <h3>${esc(i.title)}</h3>
        ${i.description ? `<p class="desc">${esc(i.description)}</p>` : ""}
        <div class="foot-line"><span>${esc(airlineName(i.airline))}</span>
          <span>${i.deadline ? "ถึง " + thDate(i.deadline) : ""}</span></div>
      </div></a>`;
  }

  function render() {
    const items = filtered();
    $("list").innerHTML = items.map(card).join("");
    const empty = $("empty");
    empty.hidden = items.length > 0;
    empty.textContent = data.items.length
      ? "ไม่พบรายการที่ตรงกับตัวกรอง"
      : "ยังไม่มีข้อมูล ระบบจะดึงโปรโมชั่นรอบแรกหลังเปิดเว็บ";
    const count = (t) => data.items.filter((i) => inCat(i) && (t === "all" || i.type === t) && (state.airline === "all" || airlineName(i.airline) === airlineName(state.airline))).length;
    ["all", "fare", "draw"].forEach((t) => ($("n-" + t).textContent = count(t)));
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.type === state.type));
    document.querySelectorAll(".chip").forEach((b) => {
      b.classList.toggle("active", b.dataset.airline === state.airline);
      b.hidden = b.dataset.airline !== "all" && state.cat !== "all" && catOf(b.dataset.airline) !== state.cat;
    });
    document.querySelectorAll(".cat").forEach((b) => b.classList.toggle("active", b.dataset.cat === state.cat));
  }

  function renderChrome() {
    if (data.updated) {
      $("updated").textContent = "· อัปเดตล่าสุด " + new Date(data.updated).toLocaleString("th-TH",
        { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + " น.";
    }
    const withItems = new Set(data.items.map((i) => i.airline));
    const seenNames = new Set();
    const ids = Object.keys(data.airlines).filter((id) => withItems.has(id))
      .filter((id) => { const n = airlineName(id); if (seenNames.has(n)) return false; seenNames.add(n); return true; });
    $("airlines").innerHTML = [`<button class="chip" data-airline="all">ทุกแหล่ง</button>`]
      .concat(ids.map((id) => `<button class="chip" data-airline="${esc(id)}">${esc(airlineName(id))}</button>`)).join("");
    $("status").innerHTML = (data.status || []).map((s) =>
      `<li>${esc(airlineName(s.airline))}: ${s.ok ? `<span class="ok">ดึงได้ ${s.count} รายการ</span>` : `<span class="fail">ดึงไม่สำเร็จ</span>`}</li>`).join("");
    $("demo-note").hidden = !DEMO;
  }

  document.querySelector(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab"); if (b) { state.type = b.dataset.type; render(); }
  });
  $("airlines").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (b) { state.airline = b.dataset.airline; render(); }
  });
  $("cats").addEventListener("click", (e) => {
    const b = e.target.closest(".cat");
    if (b) { state.cat = b.dataset.cat; state.airline = "all"; render(); }
  });
  $("q").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; render(); });
  $("today-only").addEventListener("change", (e) => { state.todayOnly = e.target.checked; render(); });

  fetch(DATA_URL, { cache: "no-cache" })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((d) => { data = { items: [], airlines: {}, status: [], ...d }; })
    .catch(() => {})
    .finally(() => { renderChrome(); render(); });
})();
