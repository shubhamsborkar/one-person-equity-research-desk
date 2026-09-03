/* Research Desk — shared chrome. The ONE nav list (now a sidebar rail: adding a
   tab here adds it on every page), the theme switcher, the Cmd-K command
   palette and the bottom alert bar. Active tab is derived from the URL,
   including the ?list= regions of /watch. */
"use strict";
(function () {
  /* ---- persisted chrome state, applied before first paint ---------------- */
  const THEMES = ["graphite", "alpha"];
  let store = { getItem: () => null, setItem: () => {} };
  try { store = window.localStorage; } catch (e) { /* file:// etc. */ }
  const savedTheme = store.getItem("desk_theme");
  if (THEMES.includes(savedTheme)) document.documentElement.dataset.theme = savedTheme;
  let rail = store.getItem("desk_rail");
  if (rail !== "min" && rail !== "full") rail = window.innerWidth < 900 ? "min" : "full";
  document.documentElement.dataset.rail = rail;

  /* ---- the ONE tab list: [href, label, group, icon] ---------------------- */
  const I = {
    deskin: '<path d="M2.5 2.5h11v11h-11z"/><path d="M8 2.5v11M2.5 8h11"/>',
    deskus: '<path d="M8 1.5v13"/><path d="M11 3.5H6.8a2 2 0 000 4h2.4a2 2 0 010 4H5"/>',
    risk: '<path d="M2.5 11.5a5.5 5.5 0 0111 0"/><path d="M8 11.5l2.6-3.6"/>',
    watch: '<path d="M1.8 8s2.3-4.2 6.2-4.2S14.2 8 14.2 8s-2.3 4.2-6.2 4.2S1.8 8 1.8 8z"/><circle cx="8" cy="8" r="1.9"/>',
    list: '<path d="M5.4 4h9M5.4 8h9M5.4 12h9"/><path d="M2 4h.01M2 8h.01M2 12h.01"/>',
    globe: '<circle cx="8" cy="8" r="6.2"/><path d="M1.8 8h12.4"/><path d="M8 1.8c1.9 1.7 2.7 3.9 2.7 6.2S9.9 12.5 8 14.2c-1.9-1.7-2.7-3.9-2.7-6.2S6.1 3.5 8 1.8z"/>',
    macro: '<path d="M2.5 13.5V9M6.2 13.5V4.5M9.9 13.5V7M13.6 13.5V2.5"/>',
    funds: '<rect x="2" y="5" width="12" height="8.5" rx="1.5"/><path d="M5.5 5V3.6A1.1 1.1 0 016.6 2.5h2.8a1.1 1.1 0 011.1 1.1V5"/>',
    flow: '<path d="M8.8 1.5L3.5 9h3.7l-1 5.5L11.5 7H7.8z"/>',
    short: '<path d="M2 4.5l4.6 4.6 2.6-2.6 4.8 4.8"/><path d="M14 8.5v2.8h-2.8"/>',
    capitol: '<path d="M2.5 13.5h11"/><path d="M4 13.5V7m2.7 6.5V7m2.6 6.5V7m2.7 6.5V7"/><path d="M2.5 7L8 2.5 13.5 7z"/>',
    chain: '<circle cx="3.5" cy="12" r="1.8"/><circle cx="8" cy="4" r="1.8"/><circle cx="12.5" cy="12" r="1.8"/><path d="M4.5 10.4L7 5.8m2 0l2.5 4.6M5.3 12h5.4"/>',
  };
  const TABS = [
    /* Labels: "Home" is your broker account (whatever market), "US" is the US public-record desk. Rename here. */
    ["/", "Desk · Home", "Desks", I.deskin],
    ["/usdesk", "Desk · US", "Desks", I.deskus],
    ["/risk", "Risk", "Desks", I.risk],
    ["/watch", "Watch · Home", "Watchlists", I.watch],
    ["/watch?list=us", "Watch · US", "Watchlists", I.list],
    ["/watch?list=global", "Global", "Watchlists", I.globe],
    ["/funds", "Funds", "Intelligence", I.funds],
    ["/flow", "Flow", "Intelligence", I.flow],
    ["/short", "Short", "Intelligence", I.short],
    ["/capitol", "Capitol", "Intelligence", I.capitol],
    ["/macro", "Macro", "Market", I.macro],
    ["/chain", "Chain", "Market", I.chain],
  ];
  function current() {
    const p = location.pathname;
    if (p === "/watch") {
      const l = new URLSearchParams(location.search).get("list");
      return l === "us" ? "/watch?list=us" : l === "global" ? "/watch?list=global" : "/watch";
    }
    return p === "/index.html" ? "/" : p;
  }
  const svg = d => `<svg viewBox="0 0 16 16" aria-hidden="true">${d}</svg>`;

  /* ---- sidebar rail ------------------------------------------------------ */
  function buildRail() {
    if (document.getElementById("siderail")) return;
    const cur = current();
    const groups = [];
    for (const [href, label, group, icon] of TABS) {
      let g = groups[groups.length - 1];
      if (!g || g.name !== group) { g = { name: group, items: [] }; groups.push(g); }
      g.items.push({ href, label, icon, on: href === cur });
    }
    const el = document.createElement("aside");
    el.id = "siderail";
    el.innerHTML =
      '<a class="rbrand" href="/"><span class="rlogo">RD</span>' +
      '<span class="rname">RESEARCH <b>DESK</b></span></a>' +
      '<div class="rgroups">' +
      groups.map(g =>
        `<div class="rgt">${g.name}</div>` +
        g.items.map(it =>
          `<a class="rlink${it.on ? " on" : ""}" href="${it.href}" title="${it.label}">` +
          `${svg(it.icon)}<span>${it.label}</span></a>`).join("")
      ).join("") +
      '</div>' +
      '<div class="rfoot">' +
      '<button id="cmdkbtn" title="Jump anywhere (⌘K)"><span class="rk">⌘</span><span>Command · K</span></button>' +
      '<button id="themebtn" title="Cycle theme"><span class="rk">◐</span><span>Theme · <b id="themename"></b></span></button>' +
      '<button id="railbtn" title="Collapse sidebar ( [ )"><span class="rk" id="railglyph">⟨</span><span>Collapse</span></button>' +
      '</div>' +
      '<button id="railedge" title="Collapse / expand the sidebar ( [ )">‹</button>';
    document.body.prepend(el);
    document.getElementById("railbtn").onclick = toggleRail;
    document.getElementById("railedge").onclick = toggleRail;
    document.getElementById("cmdkbtn").onclick = () => openCmdk(true);
    const tb = document.getElementById("themebtn");
    tb.onclick = () => {
      const curT = document.documentElement.dataset.theme || "graphite";
      const next = THEMES[(THEMES.indexOf(curT) + 1) % THEMES.length];
      document.documentElement.dataset.theme = next;
      store.setItem("desk_theme", next);
      syncFoot();
    };
    syncFoot();
  }
  function syncFoot() {
    const t = document.getElementById("themename");
    if (t) t.textContent = document.documentElement.dataset.theme || "graphite";
    const min = document.documentElement.dataset.rail === "min";
    const g = document.getElementById("railglyph");
    if (g) g.textContent = min ? "⟩" : "⟨";
    const e = document.getElementById("railedge");
    if (e) e.textContent = min ? "›" : "‹";
    const b = document.getElementById("railbtn");
    if (b) {
      const lbl = b.querySelector("span:last-child");
      if (lbl) lbl.textContent = min ? "Expand" : "Collapse";
    }
  }
  function toggleRail() {
    const next = document.documentElement.dataset.rail === "min" ? "full" : "min";
    document.documentElement.dataset.rail = next;
    store.setItem("desk_rail", next);
    syncFoot();
    window.dispatchEvent(new Event("resize"));   // charts re-measure their width
  }

  /* ---- command palette: pages + live ticker search ----------------------- */
  let ckOpen = false, ckSel = 0, ckItems = [], ckSeq = 0;
  function buildCmdk() {
    if (document.getElementById("cmdk")) return;
    const el = document.createElement("div");
    el.id = "cmdk";
    el.innerHTML = '<div class="ck"><input placeholder="Jump to a page, or type a ticker…" ' +
      'spellcheck="false" autocomplete="off"><div class="ckr"></div></div>';
    document.body.appendChild(el);
    el.addEventListener("mousedown", e => { if (e.target === el) openCmdk(false); });
    const inp = el.querySelector("input");
    inp.addEventListener("input", () => queueSearch(inp.value));
    inp.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); moveSel(-1); }
      else if (e.key === "Enter") { e.preventDefault(); go(ckItems[ckSel]); }
      else if (e.key === "Escape") openCmdk(false);
    });
  }
  function openCmdk(open) {
    buildCmdk();
    ckOpen = open;
    const el = document.getElementById("cmdk");
    el.classList.toggle("open", open);
    if (open) {
      const inp = el.querySelector("input");
      inp.value = ""; inp.focus();
      renderCk({ pages: TABS.map(([href, label]) => ({ href, label })), us: [], in: [] });
    }
  }
  function go(item) {
    if (item) location.href = item.href;
  }
  function moveSel(d) {
    if (!ckItems.length) return;
    ckSel = (ckSel + d + ckItems.length) % ckItems.length;
    document.querySelectorAll("#cmdk .cki").forEach((n, i) =>
      n.classList.toggle("sel", i === ckSel));
  }
  let ckTimer = null;
  function queueSearch(q) {
    clearTimeout(ckTimer);
    ckTimer = setTimeout(() => runSearch(q.trim()), 200);
  }
  async function runSearch(q) {
    const seq = ++ckSeq;
    const pages = TABS.filter(([, label]) =>
      !q || label.toLowerCase().includes(q.toLowerCase()))
      .map(([href, label]) => ({ href, label }));
    if (q.length < 2) { renderCk({ pages, us: [], in: [] }); return; }
    let us = [], ind = [];
    try {
      const [ru, ri] = await Promise.all([
        fetch("/api/search?q=" + encodeURIComponent(q) + "&list=us").then(r => r.json()),
        fetch("/api/search?q=" + encodeURIComponent(q) + "&list=in").then(r => r.json()),
      ]);
      us = (ru.results || []).slice(0, 5);
      ind = (ri.results || []).slice(0, 5);
    } catch (e) { /* offline page search still works */ }
    if (seq !== ckSeq || !ckOpen) return;
    renderCk({ pages, us, in: ind });
  }
  function renderCk(d) {
    const r = document.querySelector("#cmdk .ckr");
    if (!r) return;
    ckItems = []; ckSel = 0;
    let html = "";
    const section = (title, rows) => {
      if (!rows.length) return;
      html += `<div class="ckh">${title}</div>`;
      for (const it of rows) {
        html += `<div class="cki" data-i="${ckItems.length}"><span class="nm">${it.nm}</span>` +
          (it.sub ? `<span>${it.sub}</span>` : "") +
          (it.ex ? `<span class="ex">${it.ex}</span>` : "") + "</div>";
        ckItems.push(it);
      }
    };
    section("Pages", d.pages.map(p => ({ nm: p.label, href: p.href, ex: "page" })));
    section("Tickers · US", d.us.map(t => ({
      nm: t.code, sub: t.name, ex: t.exch, href: "/t?symbol=" + encodeURIComponent(t.code) })));
    section("Tickers · Home", d.in.map(t => ({
      nm: t.code, sub: t.name, ex: t.exch,
      href: "/t?symbol=" + encodeURIComponent(t.code) + "&region=in" })));
    r.innerHTML = html || '<div class="ckempty">Nothing matches.</div>';
    r.querySelectorAll(".cki").forEach(n => {
      n.onclick = () => go(ckItems[+n.dataset.i]);
      n.onmousemove = () => { ckSel = +n.dataset.i;
        r.querySelectorAll(".cki").forEach((m, i) => m.classList.toggle("sel", i === ckSel)); };
    });
    const first = r.querySelector(".cki");
    if (first) first.classList.add("sel");
  }

  /* ---- global keys: ⌘K palette, [ rail toggle ---------------------------- */
  function typing(e) {
    const t = e.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
  }
  document.addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); openCmdk(!ckOpen); return;
    }
    if (ckOpen && e.key === "Escape") { openCmdk(false); return; }
    if (e.key === "[" && !typing(e) && !e.metaKey && !e.ctrlKey) toggleRail();
  });

  /* ---- bottom alert bar --------------------------------------------------
     Any page carrying <div id="alertbar"></div> gets the shared alert strip:
     a slim fixed bar at the bottom (count + latest), click to expand the
     full deduped list. */
  function initAlertBar() {
    const root = document.getElementById("alertbar");
    if (!root) return;
    root.innerHTML =
      '<div class="apanel"></div>' +
      '<div class="abar"><span class="acnt mono">0</span>' +
      '<span class="alatest"><span class="adot"></span><span class="txt"></span></span>' +
      '<span class="achev">alerts ▴</span></div>';
    const bar = root.querySelector(".abar");
    const panel = root.querySelector(".apanel");
    const chev = root.querySelector(".achev");
    let open = false;
    bar.onclick = () => {
      open = !open;
      panel.style.display = open ? "block" : "none";
      chev.textContent = open ? "hide ▾" : "alerts ▴";
    };
    async function pull() {
      try {
        const r = await fetch("/api/alerts");
        if (!r.ok) return;
        const d = await r.json();
        const seen = new Set(), rows = [];
        for (const a of d.active || []) {
          if (seen.has(a.text)) continue;
          seen.add(a.text);
          rows.push(a);
        }
        if (!rows.length) { root.style.display = "none"; return; }
        root.style.display = "block";
        if (!document.body.dataset.padded) {
          document.body.style.paddingBottom =
            (parseInt(getComputedStyle(document.body).paddingBottom) || 0) + 44 + "px";
          document.body.dataset.padded = "1";
        }
        const today = new Date().toLocaleDateString("sv-SE");
        const hot = rows.some(a => a.level === "hot");
        const cnt = root.querySelector(".acnt");
        cnt.textContent = rows.length;
        cnt.className = "acnt mono" + (hot ? " hot" : "");
        root.querySelector(".alatest .adot").className = "adot " + (rows[0].level || "");
        root.querySelector(".alatest .txt").textContent = rows[0].text;
        panel.innerHTML = rows.map(a => {
          const when = (a.date && a.date !== today) ? a.date.slice(5) + " " + a.ts : a.ts;
          return `<div class="arow"><span class="adot ${a.level || ""}"></span>` +
                 `<span class="txt">${a.text}</span><span class="atime mono">${when}</span></div>`;
        }).join("");
      } catch (e) { /* bar just stays as-is */ }
    }
    pull();
    setInterval(pull, 60000);
  }

  function boot() { buildRail(); initAlertBar(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  /* ---- heartbeat: if the desk restarts under an open page, say so and reload
     the page the moment it answers again ------------------------------------ */
  (function () {
    var fails = 0, wasDown = false, bar = null;
    function showBar() {
      if (bar) return;
      bar = document.createElement("div");
      bar.id = "desk-reconnect";
      bar.textContent = "The desk is restarting. This page reconnects by itself.";
      bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:9999;padding:8px 14px;background:#b8860b;color:#111;font:600 13px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;text-align:center";
      document.body.appendChild(bar);
    }
    setInterval(function () {
      fetch("/api/ping", { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error("down");
        if (wasDown) { location.reload(); return; }
        fails = 0;
      }).catch(function () {
        fails++;
        if (fails >= 2) { wasDown = true; showBar(); }
      });
    }, 5000);
  })();
})();
