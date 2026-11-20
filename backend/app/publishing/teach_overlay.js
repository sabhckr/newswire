// NEWSWIRE "teach once" overlay. Run in every frame of the setup browser window, in a JavaScript
// world of its own (the website's scripts cannot see or call it).
// The top frame shows a bar with the current instruction; every frame captures the click that
// answers it, without letting the page act on that click (a Publish button is not pressed).
// It tells Python what happened with __nwTeach(json) and Python answers by calling
// __nwTeachSet(state).
(() => {
  if (globalThis.__nwTeachInstalled) return;
  globalThis.__nwTeachInstalled = true;
  const isTop = window === window.top;
  let state = { mode: "idle" };
  let bar = null;
  let hovered = null;
  let quietUntil = 0;
  // The bar can cover a website's own top menu, so the person can send it to the bottom edge.
  // Remembered for this window (sessionStorage), because every new page starts this script again.
  let atBottom = false;
  try {
    atBottom = sessionStorage.getItem("__nw_teach_bar") === "bottom";
  } catch (err) {
    /* storage is blocked on this page */
  }

  // <css-path>
  const unique = (sel) => {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch (e) {
      return false;
    }
  };
  const generatedId = (id) => /\d{4,}|^[0-9]|[:[\]]|^(ember|react|vue|mui|rc|radix)-?/i.test(id);

  // A selector that finds the same element next time: id, a telling attribute, or a short path.
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id && !generatedId(el.id) && unique("#" + CSS.escape(el.id))) return "#" + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    for (const a of ["name", "data-testid", "data-test", "aria-label", "placeholder", "title", "data-name"]) {
      const v = el.getAttribute(a);
      if (v && v.length < 120) {
        const s = `${tag}[${a}="${v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"]`;
        if (unique(s)) return s;
      }
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      if (cur.id && !generatedId(cur.id) && unique("#" + CSS.escape(cur.id))) {
        parts.unshift("#" + CSS.escape(cur.id));
        break;
      }
      let part = cur.tagName.toLowerCase();
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === cur.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur) + 1})`;
      }
      parts.unshift(part);
      if (unique(parts.join(" > "))) break;
      cur = parent;
    }
    return parts.join(" > ");
  }
  // </css-path>

  // The element the person meant: the input behind a label, the whole rich-text editor rather than
  // the paragraph inside it, the file input inside an upload box.
  function pick(target) {
    let el = target;
    if (!el || !el.closest) return el;
    const label = el.closest("label");
    if (label) {
      const forId = label.getAttribute("for");
      const linked = forId && document.getElementById(forId);
      if (linked) return linked;
      const inner = label.querySelector("input,textarea,select");
      if (inner) return inner;
    }
    const editable = el.closest("[contenteditable]");
    if (editable && editable.isContentEditable) {
      let root = editable;
      while (root.parentElement && root.parentElement.isContentEditable) root = root.parentElement;
      return root;
    }
    const field = el.closest("input,textarea,select,button,a,[role=button],[role=textbox]");
    if (field) return field;
    const files = el.querySelectorAll ? el.querySelectorAll("input[type=file]") : [];
    if (files.length === 1) return files[0];
    return el;
  }

  function describe(el) {
    return {
      selector: cssPath(el),
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute("type") || "").toLowerCase(),
      editable: !!el.isContentEditable,
      text: (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().slice(0, 60),
      url: location.href,
    };
  }

  function highlight(el) {
    if (hovered && hovered !== el) hovered.style.outline = hovered.__nwOutline || "";
    if (el && el !== hovered) {
      el.__nwOutline = el.style.outline;
      el.style.outline = "3px solid #f59e0b";
    }
    hovered = el;
  }

  const tell = (message) => {
    try {
      globalThis.__nwTeach(JSON.stringify(message));
    } catch (err) {
      /* the window is closing */
    }
  };
  const refresh = () => tell({ type: "state" });
  const picking = () => state.mode === "pick";
  const inBar = (t) => bar && t && bar.contains(t);
  const block = (e) => {
    if (!picking() || inBar(e.target)) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
  };
  for (const type of ["mousedown", "mouseup", "pointerdown", "pointerup", "submit", "dblclick"]) {
    document.addEventListener(type, block, true);
  }
  document.addEventListener(
    "click",
    (e) => {
      if (!picking() || inBar(e.target)) return;
      block(e);
      // The second half of a double-click must not answer the next question.
      if (Date.now() < quietUntil) return;
      const el = pick(e.target);
      highlight(null);
      tell({ type: "pick", step: state.step, info: describe(el) });
    },
    true,
  );
  document.addEventListener(
    "mouseover",
    (e) => {
      if (picking() && !inBar(e.target)) highlight(pick(e.target));
    },
    true,
  );

  function button(label, action, primary) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.style.cssText =
      "margin-left:8px;padding:6px 12px;border-radius:6px;border:1px solid #fff;font:600 13px system-ui;cursor:pointer;" +
      (primary ? "background:#fff;color:#1e3a8a;" : "background:transparent;color:#fff;");
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      tell({ type: "action", step: state.step, name: action });
    });
    return b;
  }

  function moveButton() {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = atBottom ? "\u2191 Move bar up" : "\u2193 Move bar down";
    b.title = "Move this bar out of the way of the website's own menu";
    b.style.cssText =
      "margin-left:8px;padding:6px 12px;border-radius:6px;border:1px solid rgba(255,255,255,.55);font:600 13px system-ui;" +
      "cursor:pointer;background:transparent;color:#fff;";
    b.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      atBottom = !atBottom;
      try {
        sessionStorage.setItem("__nw_teach_bar", atBottom ? "bottom" : "top");
      } catch (err) {
        /* storage is blocked on this page */
      }
      render();
    });
    return b;
  }

  // What the bar currently shows. Redrawing replaces its buttons, and a button replaced between
  // "mouse down" and "mouse up" swallows the click, so the bar is only redrawn when this changes.
  let shown = "";

  function render() {
    if (!isTop || !document.body) return;
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "__nw_teach_bar";
      bar.style.cssText =
        "position:fixed;left:0;right:0;top:0;z-index:2147483647;background:#1e3a8a;color:#fff;" +
        "font:14px/1.4 system-ui,sans-serif;padding:10px 14px;display:flex;align-items:center;flex-wrap:wrap;gap:4px;" +
        "box-shadow:0 2px 10px rgba(0,0,0,.35)";
    }
    const reattached = !bar.isConnected;
    if (reattached) document.documentElement.appendChild(bar);
    const showing = [state.mode, state.step, state.total, state.message, !!state.optional, atBottom].join("\u0001");
    if (showing === shown && !reattached && bar.childElementCount) return;
    shown = showing;
    bar.style.top = atBottom ? "auto" : "0";
    bar.style.bottom = atBottom ? "0" : "auto";
    bar.replaceChildren();
    const text = document.createElement("div");
    text.style.cssText = "flex:1;min-width:240px";
    const step = state.total ? `Step ${state.step + 1} of ${state.total}: ` : "";
    text.innerHTML = "<b>Samyabadi setup</b> · ";
    text.appendChild(document.createTextNode(step + (state.message || "")));
    bar.appendChild(text);
    if (state.mode === "page") bar.appendChild(button("This is the page", "page", true));
    if (state.mode === "pick" && state.optional) bar.appendChild(button("Skip", "skip", false));
    if (state.mode === "pick" || state.mode === "page" || state.mode === "login") {
      bar.appendChild(moveButton());
      bar.appendChild(button("Cancel", "cancel", false));
    }
  }

  globalThis.__nwTeachSet = (next) => {
    if (!next) return;
    if (next.step !== state.step || next.mode !== state.mode) quietUntil = Date.now() + 400;
    state = next;
    if (!picking()) highlight(null);
    render();
  };
  if (isTop) {
    document.addEventListener("DOMContentLoaded", refresh);
    setInterval(refresh, 600);
  } else {
    setInterval(refresh, 1200);
  }
  refresh();
})();
