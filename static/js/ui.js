/* Small interaction layer, no libraries (works offline):
 * - ripple on buttons, spinner on the clicked submit button (also stops a double submit)
 * - thin progress bar at the top while the next page loads
 * - messages slide in, can be closed, success/info messages close by themselves
 * - numbers marked data-countup count up once
 * Everything respects "reduce motion" in the operating system.
 */
(function () {
  "use strict";
  const calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* keep the scroll position when a sort button, tab or filter reloads the same list
     (elements or forms marked data-keep-scroll) */
  const KEEP = "ui-keep-scroll";
  function keepScroll() {
    try { sessionStorage.setItem(KEEP, JSON.stringify({ path: location.pathname, y: window.scrollY })); } catch (e) {}
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("a[data-keep-scroll], [data-keep-scroll] a")) keepScroll();
  }, true);
  document.addEventListener("change", function (e) {  // selects that submit on change (form.submit() skips "submit")
    if (e.target.closest && e.target.closest("form[data-keep-scroll]")) keepScroll();
  }, true);
  document.addEventListener("submit", function (e) {
    if (e.target.matches && e.target.matches("form[data-keep-scroll]")) keepScroll();
  }, true);
  let restoreTo = null;
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEEP) || "null");
    sessionStorage.removeItem(KEEP);
    if (saved && saved.path === location.pathname && !location.hash) restoreTo = saved.y;
  } catch (e) {}
  if (restoreTo !== null) {
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    const go = function () { window.scrollTo(0, restoreTo); };
    document.addEventListener("DOMContentLoaded", go);
    window.addEventListener("load", go);  // again once fonts and charts have their final size
  }

  /* ripple */
  document.addEventListener("pointerdown", function (e) {
    const btn = e.target.closest && e.target.closest(".btn, .pager-item");
    if (!btn || calm || btn.disabled || e.button !== 0) return;
    const r = btn.getBoundingClientRect(), size = Math.max(r.width, r.height);
    const dot = document.createElement("span");
    dot.className = "ripple";
    dot.style.width = dot.style.height = size + "px";
    dot.style.left = e.clientX - r.left - size / 2 + "px";
    dot.style.top = e.clientY - r.top - size / 2 + "px";
    btn.appendChild(dot);
    dot.addEventListener("animationend", function () { dot.remove(); });
  });

  /* top progress bar */
  const bar = document.createElement("div");
  bar.id = "top-progress";
  document.addEventListener("DOMContentLoaded", function () { document.body.appendChild(bar); });
  let timer = null;
  function startBar() {
    clearInterval(timer);
    let w = 8;
    bar.classList.add("is-active");
    bar.style.width = w + "%";
    timer = setInterval(function () { w += (90 - w) * 0.08; bar.style.width = w + "%"; }, 200);
  }
  function stopBar() {
    clearInterval(timer);
    if (!bar.classList.contains("is-active")) return;
    bar.style.width = "100%";
    setTimeout(function () { bar.classList.remove("is-active"); bar.style.width = "0"; }, 250);
  }
  window.uiProgress = { start: startBar, stop: stopBar };

  document.addEventListener("click", function (e) {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || a.target === "_blank" || a.hasAttribute("download")) return;
    if (/\.(xlsx|zip|docx|pdf)(\?|$)|format=xlsx|\/download\//.test(href)) return;  // a file: the page stays
    if (a.origin && a.origin !== location.origin) return;
    startBar();
  });

  /* busy submit button: spinner + no second click; restored if the page stays (a download, a bad form) */
  let lastSubmitter = null;
  document.addEventListener("click", function (e) {
    const b = e.target.closest && e.target.closest("button, input[type=submit]");
    if (b && b.form) lastSubmitter = b;
  }, true);
  document.addEventListener("submit", function (e) {
    const form = e.target;
    setTimeout(function () {
      if (e.defaultPrevented || form.hasAttribute("data-no-busy") || form.target === "_blank") return;
      const btn = (e.submitter || (lastSubmitter && lastSubmitter.form === form ? lastSubmitter : null)
                   || form.querySelector("button:not([type=button]), input[type=submit]"));
      if (btn && btn.classList.contains("btn")) {
        btn.setAttribute("aria-busy", "true");
        setTimeout(function () { btn.removeAttribute("aria-busy"); stopBar(); }, 12000);
      }
      if (!form.hasAttribute("data-no-progress")) startBar();
    }, 0);
  });
  window.addEventListener("pageshow", function () {  // back/forward cache: nothing is loading any more
    stopBar();
    document.querySelectorAll('[aria-busy="true"]').forEach(function (b) { b.removeAttribute("aria-busy"); });
  });
  document.addEventListener("htmx:beforeRequest", startBar);
  document.addEventListener("htmx:afterRequest", stopBar);

  /* copy buttons: <button data-copy="#textarea-id">, or <button data-copy-text="L6800123456"> for a number
     (works on plain http too, where the clipboard API is missing) */
  document.addEventListener("click", function (e) {
    const btn = e.target.closest && e.target.closest("[data-copy], [data-copy-text]");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();  // inside a clickable table row: copy only, do not open the case
    let src = btn.hasAttribute("data-copy") ? document.querySelector(btn.getAttribute("data-copy")) : null;
    if (btn.hasAttribute("data-copy-text")) {  // a hidden box to select from when the clipboard API is missing
      src = document.createElement("textarea");
      src.value = btn.getAttribute("data-copy-text");
      src.setAttribute("readonly", "");
      src.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(src);
      setTimeout(function () { src.remove(); }, 0);
    }
    if (!src) return;
    const text = "value" in src ? src.value : src.textContent;
    const label = btn.querySelector("[data-copy-label]") || (btn.hasAttribute("data-copy-text") ? null : btn);
    const done = function (ok) {
      if (!label) {  // an icon button: a tick for a moment, the result in its tooltip
        btn.classList.toggle("is-copied", ok);
        btn.title = ok ? "คัดลอกแล้ว" : "คัดลอกไม่ได้";
        clearTimeout(btn._copyTimer);
        btn._copyTimer = setTimeout(function () { btn.classList.remove("is-copied"); btn.title = "คัดลอก"; }, 1600);
        return;
      }
      if (!label.dataset.text) label.dataset.text = label.textContent;
      label.textContent = ok ? "คัดลอกแล้ว ✓" : "คัดลอกไม่ได้ (เลือกข้อความแล้วกด Ctrl+C)";
      btn.classList.toggle("is-copied", ok);
      clearTimeout(btn._copyTimer);
      btn._copyTimer = setTimeout(function () { label.textContent = label.dataset.text; btn.classList.remove("is-copied"); }, 2200);
    };
    const fallback = function () {
      let ok = false;
      if ("select" in src) {
        src.focus(); src.select();
        try { ok = document.execCommand("copy"); } catch (err) {}
      }
      done(ok);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    } else {
      fallback();
    }
  });

  /* column filter dropdowns (<details data-col-filter>): the panel floats under its button (the table scrolls
     sideways, so it is placed with position:fixed), one open at a time, closes on a click outside or Esc */
  function placePanel(d) {
    const panel = panelOf(d), r = d.querySelector("summary").getBoundingClientRect();
    if (!panel) return;
    const w = panel.offsetWidth || 256, margin = 8;
    let left = Math.min(r.left, window.innerWidth - w - margin);
    panel.style.left = Math.max(margin, left) + "px";
    const below = r.bottom + 6, h = panel.offsetHeight;
    panel.style.top = (below + h > window.innerHeight - margin && r.top - h - 6 > margin ? r.top - h - 6 : below) + "px";
  }
  function panelOf(d) { return d._panel || (d._panel = d.querySelector(".col-filter-panel")); }
  document.addEventListener("toggle", function (e) {
    const d = e.target;
    if (!d.matches || !d.matches("[data-col-filter]")) return;
    const panel = panelOf(d);
    if (!d.open) { if (panel && panel.parentElement !== d) d.appendChild(panel); return; }  // back inside: hidden again
    document.querySelectorAll("[data-col-filter][open]").forEach(function (o) { if (o !== d) o.open = false; });
    // shown from <body>: a table heading (sticky, blurred) or an animated page would move a fixed box
    if (panel) document.body.appendChild(panel);
    placePanel(d);
    const first = d.querySelector(".col-filter-panel input:not([type=hidden])");
    if (first) first.focus({ preventScroll: true });
  }, true);
  document.addEventListener("click", function (e) {
    const clear = e.target.closest && e.target.closest("[data-col-clear]");
    if (clear) {
      const panel = clear.closest(".col-filter-panel");
      panel.querySelectorAll("input[type=checkbox]").forEach(function (c) { c.checked = false; });
      panel.querySelectorAll("input:not([type=checkbox]):not([type=hidden])").forEach(function (i) { i.value = ""; });
      const form = document.getElementById(panel.querySelector("[form]").getAttribute("form"));
      if (form) { keepScroll(); form.requestSubmit ? form.requestSubmit() : form.submit(); }
      return;
    }
    document.querySelectorAll("[data-col-filter][open]").forEach(function (d) {
      if (!d.contains(e.target) && !panelOf(d).contains(e.target)) d.open = false;
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    document.querySelectorAll("[data-col-filter][open]").forEach(function (d) { d.open = false; d.querySelector("summary").focus(); });
  });
  ["scroll", "resize"].forEach(function (ev) {
    window.addEventListener(ev, function () { document.querySelectorAll("[data-col-filter][open]").forEach(placePanel); }, true);
  });

  /* keyboard: "/" jumps to the page's search box; Ctrl + K (or ⌘ K) opens the quick search */
  function typing(el) {
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      openCmdk();
      return;
    }
    if (e.key === "/" && !typing(document.activeElement) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const box = document.querySelector('main input[name="q"]');
      if (box) { e.preventDefault(); box.focus(); box.select(); }
    }
  });
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("[data-cmd-open]")) { e.preventDefault(); openCmdk(); }
  });

  /* quick search: the menu's pages, and "search cases for ..." (Enter) */
  function openCmdk() {
    if (document.querySelector(".cmdk-backdrop")) return;
    const links = Array.prototype.map.call(document.querySelectorAll("#sidebar a.side-link"), function (a) {
      return { label: a.textContent.replace(/\s+/g, " ").trim(), href: a.getAttribute("href"), icon: a.querySelector("svg") };
    });
    const searchUrl = document.body.getAttribute("data-search-url");
    const back = document.createElement("div");
    back.className = "cmdk-backdrop";
    back.innerHTML = '<div class="cmdk" role="dialog" aria-modal="true" aria-label="ค้นหาเร็ว">' +
      '<input class="cmdk-input" type="text" placeholder="พิมพ์ชื่อเมนู หรือ เลขรับแจ้ง / ชื่อ / เบอร์ แล้วกด Enter" aria-label="ค้นหาเร็ว">' +
      '<div class="cmdk-list" role="listbox"></div>' +
      '<div class="cmdk-foot"><span><kbd>↑</kbd> <kbd>↓</kbd> เลือก</span><span><kbd>Enter</kbd> เปิด</span><span><kbd>Esc</kbd> ปิด</span><span><kbd>/</kbd> ไปที่ช่องค้นหาของหน้า</span></div></div>';
    document.body.appendChild(back);
    const input = back.querySelector("input"), list = back.querySelector(".cmdk-list");
    let items = [], active = 0;
    function close() { back.remove(); document.removeEventListener("keydown", keys, true); }
    function go(item) { if (!item) return; close(); startBar(); location.href = item.href; }
    function render() {
      const q = input.value.trim().toLowerCase();
      items = links.filter(function (l) { return !q || l.label.toLowerCase().indexOf(q) >= 0; });
      if (q && searchUrl) items.push({ label: "ค้นหาคดี “" + input.value.trim() + "”", href: searchUrl + encodeURIComponent(input.value.trim()), search: true });
      active = Math.min(active, Math.max(items.length - 1, 0));
      list.innerHTML = "";
      items.forEach(function (it, i) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "cmdk-item" + (i === active ? " is-active" : "");
        b.setAttribute("role", "option");
        if (it.icon) b.appendChild(it.icon.cloneNode(true));
        else b.insertAdjacentHTML("beforeend", '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path stroke-linecap="round" d="m21 21-5.2-5.2M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z"/></svg>');
        b.appendChild(document.createTextNode(it.label));
        b.addEventListener("mouseenter", function () { active = i; mark(); });
        b.addEventListener("click", function () { go(it); });
        list.appendChild(b);
      });
      if (!items.length) list.innerHTML = '<div class="px-3 py-6 text-center text-sm text-slate-400">ไม่พบเมนู</div>';
    }
    function mark() {
      Array.prototype.forEach.call(list.children, function (el, i) { el.classList.toggle("is-active", i === active); });
      const el = list.children[active];
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    }
    function keys(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, items.length - 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); mark(); }
      else if (e.key === "Enter") { e.preventDefault(); go(items[active] || (items.length ? items[0] : null)); }
    }
    document.addEventListener("keydown", keys, true);
    input.addEventListener("input", function () { active = 0; render(); });
    back.addEventListener("mousedown", function (e) { if (e.target === back) close(); });
    render();
    input.focus();
  }

  /* messages */
  function closeAlert(el) {
    el.classList.remove("alert-enter");
    el.classList.add("alert-leave");
    el.addEventListener("animationend", function () { el.remove(); }, { once: true });
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-alert]").forEach(function (el, i) {
      el.classList.add("alert-enter");
      el.style.animationDelay = i * 60 + "ms";
      const x = el.querySelector(".alert-close");
      if (x) x.addEventListener("click", function () { closeAlert(el); });
      if (el.dataset.alert === "auto") {
        let left = 6000, t = null;
        const run = function () { t = setTimeout(function () { closeAlert(el); }, left); };
        el.addEventListener("mouseenter", function () { clearTimeout(t); });
        el.addEventListener("mouseleave", run);
        run();
      }
    });

    /* count-up */
    const nums = document.querySelectorAll("[data-countup]");
    if (calm || !nums.length) return;
    nums.forEach(function (el) {
      const text = el.textContent.trim(), target = parseInt(text.replace(/[^\d]/g, ""), 10);
      if (!target || target < 10) return;
      const start = performance.now(), dur = Math.min(900, 400 + target / 20);
      const fmt = function (n) { return n.toLocaleString("en-US"); };
      el.textContent = "0";
      (function step(now) {
        const p = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - p, 3);
        el.textContent = p < 1 ? fmt(Math.round(target * eased)) : text;
        if (p < 1) requestAnimationFrame(step);
      })(start);
    });
  });
})();
