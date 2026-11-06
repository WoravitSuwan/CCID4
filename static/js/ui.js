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
