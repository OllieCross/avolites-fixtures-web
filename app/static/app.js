(() => {
  "use strict";
  const PAGE = 60;
  const MAX_ZIP = 300;
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const state = { q: "", maker: "", status: "", sort: "name", offset: 0, total: 0, loading: false, seq: 0 };
  const selected = new Set();
  let makers = [];
  let pageIds = [];

  // ---- URL state -----------------------------------------------------------
  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.maker = p.get("maker") || "";
    state.status = p.get("status") || "";
    state.sort = p.get("sort") || "name";
    $("q").value = state.q;
    $("maker").value = state.maker;
    $("status").value = state.status;
    $("sort").value = state.sort;
  }
  function writeUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.maker) p.set("maker", state.maker);
    if (state.status) p.set("status", state.status);
    if (state.sort !== "name") p.set("sort", state.sort);
    const s = p.toString();
    history.replaceState(null, "", s ? "?" + s : location.pathname);
  }

  // ---- helpers -------------------------------------------------------------
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  function fmtAgo(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 2) return "just now";
    if (mins < 90) return mins + " min ago";
    const h = Math.round(mins / 60);
    if (h < 48) return h + " h ago";
    return Math.round(h / 24) + " days ago";
  }
  function modesText(modes) {
    if (!modes.length) return "—";
    const max = Math.max(...modes.map((m) => m.c));
    return modes.length + (modes.length === 1 ? " mode" : " modes") + (max ? ", up to " + max + " ch" : "");
  }
  function modesTitle(modes) {
    return modes.map((m) => m.n + (m.c ? " (" + m.c + " ch)" : "")).join("\n");
  }
  function badge(kind) {
    return el("span", "badge " + kind, kind);
  }

  // ---- fixtures table ------------------------------------------------------
  function matchedMaker() {
    const v = state.maker.trim().toLowerCase();
    if (!v) return null;
    return makers.find((m) => m.company.toLowerCase() === v) || null;
  }

  function params(extra) {
    const p = new URLSearchParams();
    if (state.q.trim()) p.set("q", state.q.trim());
    const m = matchedMaker();
    if (m) p.set("company", m.company);
    else if (state.maker.trim()) p.set("maker", state.maker.trim());
    if (state.status) p.set("status", state.status);
    p.set("sort", state.sort);
    p.set("limit", PAGE);
    for (const k in extra) p.set(k, extra[k]);
    return p;
  }

  async function load(reset) {
    if (reset) {
      state.offset = 0;
      $("rows").replaceChildren();
      pageIds = [];
      writeUrl();
    }
    const seq = ++state.seq;
    state.loading = true;
    let data;
    try {
      const r = await fetch("/api/fixtures?" + params({ offset: state.offset }));
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
    } catch (e) {
      if (seq === state.seq) showEmpty("Could not load fixtures. Try again in a moment.");
      return;
    }
    if (seq !== state.seq) return;
    state.loading = false;
    state.total = data.total;
    const tbody = $("rows");
    for (const it of data.items) {
      tbody.appendChild(row(it));
      pageIds.push(it.id);
    }
    state.offset += data.items.length;
    $("count").textContent = data.total
      ? "Showing " + state.offset.toLocaleString() + " of " + data.total.toLocaleString() + " fixtures"
      : "";
    $("more").hidden = state.offset >= data.total;
    $("table").hidden = !data.total;
    if (!data.total) showEmpty(emptyMessage());
    else $("empty").hidden = true;
    syncSelectPage();
  }

  function emptyMessage() {
    if (!window.__ready) return "The library is being imported for the first time. Check back in a few minutes.";
    return "No fixtures match these filters.";
  }
  function showEmpty(msg) {
    $("table").hidden = true;
    $("more").hidden = true;
    const e = $("empty");
    e.textContent = msg;
    e.hidden = false;
  }

  function row(it) {
    const tr = el("tr");
    if (it.badge === "removed") tr.classList.add("is-removed");
    if (selected.has(it.id)) tr.classList.add("sel");
    tr.dataset.id = it.id;

    const tdSel = el("td", "c-sel");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = selected.has(it.id);
    cb.setAttribute("aria-label", "Select " + it.company + " " + it.name);
    cb.addEventListener("change", () => toggle(it.id, cb.checked, tr));
    tdSel.appendChild(cb);

    const tdMaker = el("td", "maker", it.company);
    const tdModel = el("td");
    tdModel.appendChild(el("span", "model", it.name));
    if (it.badge) tdModel.appendChild(badge(it.badge));

    const tdModes = el("td", "c-modes");
    const mt = el("span", "modes-line", modesText(it.modes));
    if (it.modes.length) mt.title = modesTitle(it.modes);
    tdModes.appendChild(mt);

    const tdRev = el("td", "c-rev", fmtDate(it.revised));

    const tdDl = el("td", "c-dl");
    const a = el("a", "btn", "Download");
    a.href = "/download/" + it.id;
    a.setAttribute("aria-label", "Download " + it.company + " " + it.name + " .d4");
    tdDl.appendChild(a);

    tr.append(tdSel, tdMaker, tdModel, tdModes, tdRev, tdDl);
    return tr;
  }

  // ---- selection / zip -----------------------------------------------------
  function toggle(id, on, tr) {
    if (on) {
      if (selected.size >= MAX_ZIP) {
        alert("ZIP downloads are limited to " + MAX_ZIP + " fixtures.");
        if (tr) tr.querySelector("input").checked = false;
        return;
      }
      selected.add(id);
    } else selected.delete(id);
    if (tr) tr.classList.toggle("sel", on);
    updateBar();
    syncSelectPage();
  }
  function updateBar() {
    const n = selected.size;
    $("selbar").hidden = n === 0;
    $("selcount").textContent = n + (n === 1 ? " fixture selected" : " fixtures selected");
    $("zip").href = n ? "/zip?ids=" + [...selected].join(",") : "#";
  }
  function syncSelectPage() {
    const box = $("selpage");
    const n = pageIds.filter((id) => selected.has(id)).length;
    box.checked = pageIds.length > 0 && n === pageIds.length;
    box.indeterminate = n > 0 && n < pageIds.length;
  }
  $("selpage").addEventListener("change", (e) => {
    for (const tr of $("rows").children) {
      const id = Number(tr.dataset.id);
      const cb = tr.querySelector("input");
      if (cb.checked === e.target.checked) continue;
      if (e.target.checked && selected.size >= MAX_ZIP) break;
      cb.checked = e.target.checked;
      if (e.target.checked) selected.add(id);
      else selected.delete(id);
      tr.classList.toggle("sel", e.target.checked);
    }
    updateBar();
    syncSelectPage();
  });
  $("clearsel").addEventListener("click", () => {
    selected.clear();
    for (const tr of $("rows").children) {
      tr.classList.remove("sel");
      tr.querySelector("input").checked = false;
    }
    updateBar();
    syncSelectPage();
  });
  $("zip").addEventListener("click", (e) => { if (!selected.size) e.preventDefault(); });

  // ---- filters -------------------------------------------------------------
  let timer;
  function debounced() {
    clearTimeout(timer);
    timer = setTimeout(() => load(true), 220);
  }
  $("q").addEventListener("input", (e) => { state.q = e.target.value; debounced(); });
  $("maker").addEventListener("input", (e) => { state.maker = e.target.value; debounced(); });
  $("status").addEventListener("change", (e) => { state.status = e.target.value; load(true); });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; load(true); });
  $("more").addEventListener("click", () => load(false));

  // ---- tabs / changelog ----------------------------------------------------
  let changesLoaded = false;
  function showTab(name) {
    for (const t of ["fixtures", "changes"]) {
      $("tab-" + t).setAttribute("aria-selected", String(t === name));
      $("panel-" + t).hidden = t !== name;
    }
    if (name === "changes" && !changesLoaded) loadChanges();
  }
  $("tab-fixtures").addEventListener("click", () => showTab("fixtures"));
  $("tab-changes").addEventListener("click", () => showTab("changes"));

  async function loadChanges() {
    const box = $("changes");
    box.textContent = "Loading…";
    try {
      const r = await fetch("/api/changes");
      const list = await r.json();
      changesLoaded = true;
      box.replaceChildren();
      if (!list.length) {
        box.appendChild(el("div", "empty", "No changes recorded yet. The first import is the baseline; new and updated fixtures show up here after later checks."));
        return;
      }
      const byDay = new Map();
      for (const c of list) {
        const day = c.ts.slice(0, 10);
        if (!byDay.has(day)) byDay.set(day, []);
        byDay.get(day).push(c);
      }
      for (const [day, items] of byDay) {
        const sec = el("div", "day");
        sec.appendChild(el("h3", null, fmtDate(day) + " — " + items.length + (items.length === 1 ? " change" : " changes")));
        const ul = el("ul");
        for (const c of items) {
          const li = el("li");
          li.appendChild(badge(c.kind));
          const label = c.company + " — " + c.name;
          if (c.id) {
            const a = el("a", null, label);
            a.href = "/download/" + c.id;
            li.appendChild(a);
          } else li.appendChild(el("span", null, label));
          ul.appendChild(li);
        }
        sec.appendChild(ul);
        box.appendChild(sec);
      }
    } catch (e) {
      box.textContent = "Could not load the changelog.";
    }
  }

  // ---- header --------------------------------------------------------------
  async function loadMeta() {
    try {
      const [s, m] = await Promise.all([
        fetch("/api/stats").then((r) => r.json()),
        fetch("/api/manufacturers").then((r) => r.json()),
      ]);
      window.__ready = s.ready;
      makers = m;
      const dl = $("makers");
      for (const x of m) {
        const o = el("option");
        o.value = x.company;
        o.label = x.company + " (" + x.n + ")";
        dl.appendChild(o);
      }
      const parts = [
        s.total.toLocaleString() + " fixtures",
        s.manufacturers.toLocaleString() + " manufacturers",
      ];
      if (s.source) parts.push("library: " + s.source);
      parts.push("last checked " + fmtAgo(s.last_check) + (s.last_status === "error" ? " (check failed, retrying)" : ""));
      $("meta").textContent = parts.join(" · ");
    } catch (e) {
      $("meta").textContent = "Status unavailable";
    }
  }

  readUrl();
  loadMeta().then(() => load(true));
})();
