"""The small pieces of JavaScript the engine runs inside pages. Each is a function declaration
called with `Runtime.callFunctionOn`; nothing is left behind in the page."""

from __future__ import annotations

# Shared: is an element actually shown (it has a box and is not visibility:hidden)?
_VISIBLE = """
  const shown = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return getComputedStyle(el).visibility === 'visible';
  };
"""

# Shared: the words a person reads on a control.
_NAME = """
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const nameOf = (el) => clean(el.getAttribute('aria-label')) || clean(el.innerText) || clean(el.value)
    || clean(el.getAttribute('title')) || clean(el.getAttribute('alt'))
    || clean((el.querySelector && el.querySelector('img[alt]') || {alt: ''}).alt);
"""

# (root|null, [css...], mode) -> the first match. mode: "visible" | "any" | "prefer-visible".
# Selectors are tried in order, so the list is a list of preferences.
FIND = """function(root, selectors, mode) {""" + _VISIBLE + """
  const scope = root || document;
  let hidden = null;
  for (const sel of selectors) {
    let found;
    try { found = scope.querySelectorAll(sel); } catch (e) { continue; }
    for (const el of found) {
      if (mode === 'any') return el;
      if (shown(el)) return el;
      if (!hidden) hidden = el;
    }
  }
  return mode === 'prefer-visible' ? hidden : null;
}"""

# (root|null, [css...]) -> how many elements match any of the selectors.
COUNT = """function(root, selectors) {
  const scope = root || document;
  const seen = new Set();
  for (const sel of selectors) {
    try { for (const el of scope.querySelectorAll(sel)) seen.add(el); } catch (e) { /* bad selector */ }
  }
  return seen.size;
}"""

# (root|null, text) -> a visible button-like control whose words are `text` (exact first, then
# containing it; case does not matter).
FIND_BUTTON = """function(root, text) {""" + _VISIBLE + _NAME + """
  const scope = root || document;
  const want = clean(text).toLowerCase();
  if (!want) return null;
  const all = Array.from(scope.querySelectorAll(
    'button, input[type=submit], input[type=button], input[type=reset], input[type=image], [role=button], summary'
  )).filter(shown);
  return all.find((el) => nameOf(el).toLowerCase() === want)
    || all.find((el) => nameOf(el).toLowerCase().includes(want)) || null;
}"""

# (root|null, text) -> the smallest visible element whose own text is exactly `text`.
FIND_TEXT = """function(root, text) {""" + _VISIBLE + _NAME + """
  const scope = root || document;
  const want = clean(text);
  if (!want) return null;
  let best = null;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_ELEMENT);
  for (let el = walker.currentNode; el; el = walker.nextNode()) {
    if (el.nodeType !== 1 || /^(script|style|noscript|template)$/i.test(el.tagName)) continue;
    const own = el.tagName === 'INPUT' ? clean(el.value) : clean(el.innerText);
    if (own === want && shown(el) && (!best || best.contains(el))) best = el;
  }
  return best;
}"""

# (root|null, text, pattern|null) -> a link whose words contain `text`, else match the regex.
FIND_LINK = """function(root, text, pattern) {""" + _NAME + """
  const scope = root || document;
  const links = Array.from(scope.querySelectorAll('a[href], [role=link]'));
  const want = clean(text).toLowerCase();
  if (want) {
    const hit = links.find((a) => nameOf(a).toLowerCase() === want)
      || links.find((a) => nameOf(a).toLowerCase().includes(want));
    if (hit) return hit;
  }
  if (pattern) {
    const re = new RegExp(pattern, 'i');
    return links.find((a) => re.test(nameOf(a))) || null;
  }
  return null;
}"""

IS_VISIBLE = """function(el) {""" + _VISIBLE + """ return shown(el); }"""

# What kind of thing is this, for typing into it.
KIND = """function(el) {
  if (el.isContentEditable) return 'editable';
  const tag = el.tagName.toLowerCase();
  if (tag === 'input') return 'input:' + (el.getAttribute('type') || 'text').toLowerCase();
  return tag;
}"""

# Bring into view, focus and select what is there, so typed text replaces it. Returns the kind.
PREPARE_FILL = """function(el) {
  try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e) { /* detached */ }
  if (el.isContentEditable) {
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return 'editable';
  }
  const tag = el.tagName.toLowerCase();
  if (tag !== 'input' && tag !== 'textarea') return tag;
  el.focus();
  try { el.select(); } catch (e) { /* this input type has no selection */ }
  return tag === 'input' ? 'input:' + (el.getAttribute('type') || 'text').toLowerCase() : tag;
}"""

# Set a value the way a framework notices: the native setter, then input and change events.
SET_VALUE = """function(el, value) {
  if (el.isContentEditable) {
    el.textContent = value;
    el.dispatchEvent(new InputEvent('input', {bubbles: true}));
    return el.textContent;
  }
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
    : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value');
  if (setter && setter.set) setter.set.call(el, value); else el.value = value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return el.value;
}"""

GET_VALUE = """function(el) { return el.isContentEditable ? el.innerText : (el.value === undefined ? '' : String(el.value)); }"""

# Choose the <option> shown as `label` (or with that value). True when one was chosen.
SELECT_OPTION = """function(el, label) {
  if (el.tagName !== 'SELECT') return false;
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const want = clean(label), low = want.toLowerCase();
  const options = Array.from(el.options);
  const option = options.find((o) => clean(o.label || o.text) === want) || options.find((o) => o.value === label)
    || options.find((o) => clean(o.label || o.text).toLowerCase() === low);
  if (!option) return false;
  if (el.multiple) option.selected = true; else el.value = option.value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

# Where to click, in this frame's own coordinates, whether the click would really land on the
# element (not on a banner or dialog covering it), and whether the element is still moving (a
# dialog sliding in, a menu unfolding): a click aimed at something in motion lands beside it.
HIT_TEST = """async function(el) {
  if (!el.isConnected) return {state: 'gone'};
  const box = () => { const b = el.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(Math.round).join(); };
  const before = box();
  await new Promise((done) => setTimeout(done, 60));
  if (!el.isConnected) return {state: 'gone'};
  const moving = before !== box();
  const rects = Array.from(el.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
  if (!rects.length) return {state: 'hidden'};
  const r = rects[0];
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const root = el.getRootNode();
  let hit = (root && root.elementFromPoint ? root : document).elementFromPoint(x, y);
  const inside = (node) => {
    for (let n = node; n; n = n.parentNode || n.host) if (n === el) return true;
    return false;
  };
  let ok = !!hit && inside(hit);
  if (!ok && hit) {
    const label = hit.closest && hit.closest('label');
    ok = !!label && label.control === el;
  }
  const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';
  return {state: ok ? 'ok' : 'covered', x, y, disabled, moving};
}"""

CENTER = """function(el) { try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {} }"""
FOCUS = """function(el) { el.focus(); }"""
JS_CLICK = """function(el) { el.click(); }"""
CLOSEST = """function(el, selector) { try { return el.closest(selector); } catch (e) { return null; } }"""
INNER_TEXT = """function(el) { return (el.innerText || el.textContent || '').trim(); }"""
ATTRIBUTE = """function(el, name) { return el.getAttribute(name); }"""
LOCAL_STORAGE = """function() {
  try {
    const items = [];
    for (let i = 0; i < localStorage.length; i++) {
      const name = localStorage.key(i);
      items.push({name, value: localStorage.getItem(name)});
    }
    return {origin: location.origin, items};
  } catch (e) { return null; }
}"""
PAGE_SIZE = """function() {
  const d = document.documentElement, b = document.body || d;
  return {width: Math.max(d.scrollWidth, b.scrollWidth, d.clientWidth),
          height: Math.max(d.scrollHeight, b.scrollHeight, d.clientHeight)};
}"""


def restore_local_storage(origins: dict[str, list[list[str]]]) -> str:
    """A script for every new document: put saved localStorage back, once per origin per tab."""
    import json

    return ("(() => { try { const data = " + json.dumps(origins) + "; const items = data[location.origin]; "
            "if (!items || sessionStorage.getItem('__nw_restored')) return; "
            "sessionStorage.setItem('__nw_restored', '1'); "
            "for (const [k, v] of items) if (localStorage.getItem(k) === null) localStorage.setItem(k, v); "
            "} catch (e) { /* storage is blocked on this page */ } })();")
