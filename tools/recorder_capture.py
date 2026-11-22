"""Full-capture recorder: everything about a person's application that is needed to teach the engine the same flow.

Per user action it saves the interacted element in full (HTML, attributes, ancestors, shadow-piercing selector path, role/name, the raw hit
target and the control around it, and the engine's agent-id stamp), the raw event sequence, the DOM mutations the action caused, the
network calls, and, once the page settles, a standalone reloadable copy of the whole DOM (open shadow roots as declarative shadow DOM,
same-origin iframes inlined) together with what the engine's own reader makes of that page. Nothing is acted on.

Layout of data/recordings/<time>-<name>/:  events.jsonl (interactions, states, tabs, dialogs), states/S<n>.html (+ frames, png), meta.json
Secrets (password / one-time-code fields) are blanked in HTML, values and logs. Screenshots are not redacted (use screenshots=False).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

PAGE_JS = r"""(() => {
  if (window.__recInstalled) return; window.__recInstalled = true;
  const orig = Element.prototype.attachShadow, roots = [];
  const muts = [], evts = []; let quiet = null, pendingMuts = 0, modalSeen = false;
  const clean = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n || 90);
  const rootOf = (el) => (el.getRootNode ? el.getRootNode() : document);
  const up = (n) => (n.parentElement || (n.getRootNode && n.getRootNode().host) || null);
  const textOfIds = (el, ids) => (ids || '').split(/\s+/).filter(Boolean).map((i) => { const r = rootOf(el);
    const n = (r && r.getElementById ? r.getElementById(i) : null) || document.getElementById(i); return n ? clean(n.innerText || n.textContent) : ''; }).filter(Boolean).join(' | ');
  const nameOf = (el) => clean(el.getAttribute('aria-label')) || textOfIds(el, el.getAttribute('aria-labelledby'))
    || clean(el.labels && el.labels[0] && el.labels[0].innerText) || clean(el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title'));
  const implicitRole = (el) => { const t = el.localName, ty = (el.getAttribute('type') || '').toLowerCase();
    if (t === 'a') return el.hasAttribute('href') ? 'link' : ''; if (t === 'button') return 'button'; if (t === 'select') return 'combobox'; if (t === 'textarea') return 'textbox';
    if (t === 'input') return ({checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button', range: 'slider', file: 'file'})[ty] || 'textbox';
    if (/^h[1-6]$/.test(t)) return 'heading'; if (t === 'dialog') return 'dialog'; if (t === 'li') return 'listitem'; return ''; };
  const roleOf = (el) => el.getAttribute('role') || implicitRole(el);
  const INTERACTIVE = /^(button|option|radio|checkbox|combobox|tab|menuitem|link|switch|listbox|textbox|slider)$/;
  const meaningful = (el) => { let n = el; for (let i = 0; i < 8 && n; i++, n = up(n)) { const tag = (n.localName || '');
    if (/^(button|a|input|select|textarea|label)$/.test(tag) || INTERACTIVE.test(n.getAttribute('role') || '')) return n; } return el; };
  const groupOf = (el) => { let n = el; for (let i = 0; i < 10 && n; i++, n = up(n)) { if (/^(radiogroup|group|listbox)$/.test(n.getAttribute('role') || '') || n.localName === 'fieldset') return nameOf(n); } return ''; };
  const attrs = (el) => { const o = {}; for (const a of Array.from(el.attributes || []).slice(0, 30)) o[a.name] = a.value.slice(0, 200); return o; };
  const selPath = (el) => { const parts = []; let n = el;
    for (let i = 0; i < 40 && n; i++) { let s = n.localName; if (n.id) s += '#' + CSS.escape(n.id); else { const sibs = Array.from((n.parentNode && n.parentNode.children) || []).filter((x) => x.localName === n.localName);
        if (sibs.length > 1) s += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')'; }
      parts.unshift(s); if (n.parentElement) n = n.parentElement; else { const r = n.getRootNode(); if (r instanceof ShadowRoot) { parts.unshift('>>>'); n = r.host; } else break; } }
    return parts.join(' > ').replace(/ > >>> > /g, ' >>> '); };
  const agentOf = (el) => { let n = el; for (let i = 0; i < 12 && n; i++, n = up(n)) { const id = n.getAttribute && n.getAttribute('data-agent-id'); if (id) return {id, tag: n.localName, hops: i}; } return null; };
  const cs = (el) => { const c = getComputedStyle(el); return {display: c.display, visibility: c.visibility, pointerEvents: c.pointerEvents, position: c.position, zIndex: c.zIndex, opacity: c.opacity, cursor: c.cursor}; };
  const rect = (el) => { const b = el.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
  const secret = (el) => el.type === 'password' || /pass ?code|one[- ]time|otp|verification code|security code|\bpin\b/i.test(nameOf(el));
  const describe = (raw) => { const el = meaningful(raw);
    return {hit: {tag: raw.localName, html: clean(raw.outerHTML, 400)},
      tag: el.localName, role: roleOf(el), type: el.getAttribute('type') || '', name: nameOf(el), text: clean(el.innerText), group: groupOf(el),
      html: (secret(el) ? '<blanked secret control>' : (el.outerHTML || '').replace(/\s+/g, ' ').slice(0, 4000)), attrs: secret(el) ? {} : attrs(el),
      selector: selPath(el), rect: rect(el), style: cs(el), expanded: el.getAttribute('aria-expanded'), labelledby: el.getAttribute('aria-labelledby') || '',
      inShadow: rootOf(el) instanceof ShadowRoot, inDialog: !!(el.closest && el.closest('[role=dialog],[aria-modal=true],dialog')), agent: agentOf(raw) || agentOf(el),
      chain: (() => { const o = []; let n = el; for (let i = 0; i < 10 && n; i++, n = up(n)) o.push({tag: n.localName, role: n.getAttribute('role') || '', attrs: attrs(n)}); return o; })()}; };
  const domNow = (raw) => { try { return window.__recSerialize({hit: raw, control: meaningful(raw)}).html; } catch (_) { return ''; } };   // the page exactly as it is when the person acts
  const send = (e) => { try { e.epoch = window.__recEpoch || null; window.__rec(JSON.stringify(e)); } catch (_) {} };   // the page read whose agent-id stamps are live right now
  const target = (ev) => (ev.composedPath ? ev.composedPath()[0] : ev.target);
  const keyShown = (k) => (/^(Enter|Escape|Tab|Arrow\w+|Backspace|Delete|Space| )$/.test(k) ? k : '<char>');   // never log what is typed, only that a key was pressed
  ['pointerdown', 'mousedown', 'mouseup', 'click', 'focusin', 'focusout', 'input', 'beforeinput', 'keydown', 'keyup', 'submit'].forEach((type) =>
    addEventListener(type, (ev) => { const el = target(ev); if (!el || !el.localName) return;
      evts.push({type, tag: el.localName, role: roleOf(el), trusted: ev.isTrusted, at: Math.round(performance.now()), key: /^key/.test(type) ? keyShown(ev.key) : undefined}); if (evts.length > 120) evts.shift(); }, true));
  // Typed values: focus events do not reliably cross shadow roots, `input` does. Save a value once typing pauses, or when the person
  // presses Enter/Tab or clicks elsewhere (these listeners are registered first, so the value is logged before the click/key that left the box).
  const lastSent = new WeakMap(), timers = new Map();
  const flush = (el) => { if (!el.isConnected || lastSent.get(el) === el.value) return; lastSent.set(el, el.value);
    send({t: 'change', el: describe(el), value: secret(el) ? '<blanked>' : String(el.value).slice(0, 300)}); };
  const flushAll = () => { for (const [el, tm] of Array.from(timers)) { clearTimeout(tm); flush(el); } timers.clear(); };
  addEventListener('input', (ev) => { const el = target(ev); if (!el || !/^(input|textarea)$/.test(el.localName) || /^(checkbox|radio|file)$/.test(el.type)) return;
    clearTimeout(timers.get(el)); timers.set(el, setTimeout(() => { timers.delete(el); flush(el); }, 700)); }, true);
  addEventListener('keydown', (ev) => { if (/^(Enter|Tab)$/.test(ev.key)) flushAll(); }, true);
  addEventListener('click', () => flushAll(), true);
  addEventListener('click', (ev) => { const el = target(ev); if (el && el.localName) send({t: 'click', el: describe(el), dom: domNow(el), x: ev.clientX, y: ev.clientY, trusted: ev.isTrusted}); }, true);
  addEventListener('keydown', (ev) => { if (/^(Enter|Escape|Tab)$/.test(ev.key)) { const el = target(ev); send({t: 'key', key: ev.key, el: el && el.localName ? describe(el) : null, dom: el && el.localName ? domNow(el) : ''}); } }, true);
  addEventListener('change', (ev) => { const el = target(ev); if (el && (el.localName === 'select' || /^(checkbox|radio|file)$/.test(el.type))) {
    const v = el.localName === 'select' ? el.value : el.type === 'file' ? (el.files[0] ? el.files[0].name : '') : String(el.checked);
    send({t: 'change', el: describe(el), value: String(v).slice(0, 300)}); } }, true);
  // ---- what the DOM did: a compact mutation log, drained with each snapshot -------------------------------------------------
  const brief = (n) => ({tag: n.localName, role: roleOf(n), name: clean(nameOf(n) || n.getAttribute('class'), 50)});
  const isModal = (n) => n.matches && (n.matches('[role=dialog],[aria-modal=true],dialog') || !!n.querySelector('[role=dialog],[aria-modal=true],dialog'));
  const note = () => { pendingMuts++; clearTimeout(quiet); quiet = setTimeout(() => { if (pendingMuts >= 5 || modalSeen) send({t: 'auto', muts: pendingMuts, modal: modalSeen}); pendingMuts = 0; modalSeen = false; }, 900); };
  const ATTRS = ['aria-expanded', 'aria-checked', 'aria-selected', 'aria-disabled', 'aria-hidden', 'aria-invalid', 'disabled', 'checked', 'open', 'hidden', 'value', 'class', 'style', 'role', 'aria-busy'];
  const mo = new MutationObserver((list) => { for (const m of list) {
      if (m.type === 'childList') { m.addedNodes.forEach((n) => { if (n.nodeType === 1) { const modal = isModal(n); if (modal) modalSeen = true; if (muts.length < 400) muts.push({op: 'add', ...brief(n), modal}); } });
        m.removedNodes.forEach((n) => { if (n.nodeType === 1 && muts.length < 400) muts.push({op: 'remove', ...brief(n)}); }); }
      else if (m.type === 'attributes' && muts.length < 400) { const el = m.target, r = roleOf(el); if (r || el.hasAttribute('aria-expanded') || m.attributeName.startsWith('aria-') || /^(disabled|checked|open|hidden|value)$/.test(m.attributeName))
        muts.push({op: 'attr', ...brief(el), attr: m.attributeName, old: clean(m.oldValue, 40), now: clean(el.getAttribute(m.attributeName), 40)}); }
      note(); } });
  const watch = (root) => { try { mo.observe(root, {subtree: true, childList: true, attributes: true, attributeOldValue: true, attributeFilter: ATTRS}); } catch (_) {} };
  Element.prototype.attachShadow = function (init) { const sr = orig.call(this, Object.assign({}, init, {mode: 'open'})); watch(sr); return sr; };
  watch(document);
  (function walkRoots(root) { root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) { watch(e.shadowRoot); walkRoots(e.shadowRoot); } }); })(document);
  window.__recDrain = () => { const out = {muts: muts.splice(0), evts: evts.splice(0)}; return out; };
  // ---- a standalone copy of the DOM: open shadow roots as declarative shadow DOM, same-origin iframes inlined ----------------
  const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
  const ea = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'), et = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const css = (sheets) => Array.from(sheets || []).map((ss) => { try { return Array.from(ss.cssRules).map((r) => r.cssText).join('\n'); } catch (_) { return ''; } }).join('\n');
  const KEEP = /^(input|select|textarea|button|a|label|dialog|option|summary|details|li|svg)$/;
  let MARKS = null;   // {hit, control}: the elements the person used, tagged in the copy as data-rec-target
  const ser = (node) => {
    if (node.nodeType === 3) return et(node.textContent);
    if (node.nodeType !== 1) return '';
    const t = node.localName; if (/^(script|noscript|template)$/.test(t)) return '';
    if (t === 'link') { const r = (node.getAttribute('rel') || '').toLowerCase(); if (r.includes('stylesheet') && node.sheet) { const c = css([node.sheet]); return c ? '<style data-rec-from="link">' + c.replace(/<\/style/gi, '<\\/style') + '</style>' : ''; } return ''; }
    let a = '';
    for (const at of Array.from(node.attributes)) { if (/^on/i.test(at.name) || (t === 'input' && at.name === 'value') || (t === 'iframe' && at.name === 'src')) continue; a += ' ' + at.name + '="' + ea(at.value) + '"'; }
    if (t === 'input') { const ty = (node.type || 'text').toLowerCase(); if (ty === 'checkbox' || ty === 'radio') { if (node.checked) a += ' checked'; } else if (ty !== 'file') { const v = secret(node) ? '' : node.value; a += ' value="' + ea(v) + '"'; if (v) a += ' data-rec-value="' + ea(v) + '"'; } }
    if (t === 'option' && node.selected) a += ' selected';
    if (MARKS && (node === MARKS.hit || node === MARKS.control)) a += ' data-rec-target="' + (node === MARKS.hit && node === MARKS.control ? 'both' : node === MARKS.hit ? 'hit' : 'control') + '"';
    if (KEEP.test(t) || t.includes('-') || node.hasAttribute('role')) { const s = getComputedStyle(node), b = node.getBoundingClientRect();
      if (s.display === 'none' || s.visibility === 'hidden' || (b.width === 0 && b.height === 0)) a += ' data-rec-hidden'; else a += ' data-rec-rect="' + [b.x, b.y, b.width, b.height].map(Math.round).join(',') + '"'; }
    if (t === 'iframe') { let inner = ''; try { if (node.contentDocument && node.contentDocument.documentElement) inner = node.contentDocument.documentElement.outerHTML; } catch (_) {}
      return '<iframe' + a + (inner ? ' srcdoc="' + ea(inner) + '"' : ' data-rec-frame-src="' + ea(node.getAttribute('src') || '') + '"') + '></iframe>'; }
    if (VOID.has(t)) return '<' + t + a + '>';
    let kids = '';
    if (node.shadowRoot) { const sc = css(node.shadowRoot.adoptedStyleSheets);
      kids += '<template shadowrootmode="open">' + (sc ? '<style data-rec-from="adopted">' + sc + '</style>' : '') + Array.from(node.shadowRoot.childNodes).map(ser).join('') + '</template>'; }
    if (t === 'textarea') kids += et(secret(node) ? '' : node.value); else kids += Array.from(node.childNodes).map(ser).join('');
    return '<' + t + a + '>' + kids + '</' + t + '>';
  };
  window.__recSerialize = (marks) => { MARKS = marks || null; try { return serializeDoc(); } finally { MARKS = null; } };
  const serializeDoc = () => { const d = document, sc = css(d.styleSheets) + '\n' + css(d.adoptedStyleSheets);
    const head = '<meta charset="utf-8"><title>' + et(d.title) + '</title><style data-rec-from="page">' + sc.replace(/<\/style/gi, '<\\/style') + '\n[data-rec-hidden]{display:none!important}</style>';
    const body = d.body ? ser(d.body) : '';
    return {html: '<!doctype html><html' + (d.documentElement.lang ? ' lang="' + ea(d.documentElement.lang) + '"' : '') + '><head>' + head + '</head>' + body + '</html>', title: d.title, url: location.href}; };
})();"""

_SECRET_LABEL = re.compile(r"pass ?code|password|otp|verification|security code|\bpin\b", re.I)


def bare(url: str) -> str:
    p = urlsplit(url)
    if not p.scheme:
        return ""
    return f"{p.scheme}:" if p.scheme in {"data", "about", "blob"} else f"{p.scheme}://{p.netloc}{p.path}"


class Recorder:
    """Attach to any Playwright browser context and record until stop(). Needs no engine run: the engine's reader is only *consulted*."""

    def __init__(self, context, out_dir: Path, *, facts: dict | None = None, screenshots: bool = True, settle: float = 0.9) -> None:
        self.ctx, self.out, self.facts, self.screenshots, self.settle = context, Path(out_dir), facts, screenshots, settle
        (self.out / "states").mkdir(parents=True, exist_ok=True)
        self.log = (self.out / "events.jsonl").open("a", encoding="utf-8")
        self.step = 0
        self.nstate = 0
        self.nepoch = 0
        self.pages: dict = {}
        self.net: list[dict] = []

    def write(self, rec: dict) -> None:
        if self.log.closed:
            return                                      # a tab closing after stop() must not crash the recorder
        rec["at"] = datetime.now().isoformat(timespec="milliseconds")
        self.log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.log.flush()

    async def start(self) -> None:
        await self.ctx.expose_binding("__rec", self._on_event)
        await self.ctx.add_init_script(PAGE_JS)
        self.ctx.on("page", self._new_tab)
        for page in self.ctx.pages:
            self._wire(page)
            for frame in page.frames:
                try:
                    await frame.evaluate(PAGE_JS)       # tabs that are already open do not run init scripts until they navigate
                except Exception:
                    pass
            self._schedule(page, "start")
        (self.out / "meta.json").write_text(json.dumps({"started": datetime.now().isoformat(timespec="seconds"), "screenshots": self.screenshots}), encoding="utf-8")

    def _new_tab(self, page) -> None:
        """A tab opened by the page (Apply with target=_blank, window.open) or by the person: record it, which tab opened it, and read it
        once it has loaded, so the first action in it already has a 'before' state."""
        self.write({"t": "new_tab", "tab": id(page) % 100000, "url": bare(page.url)})
        self._wire(page)
        self._schedule(page, "new_tab")
        asyncio.create_task(self._note_opener(page))

    async def _note_opener(self, page) -> None:
        try:
            opener = await page.opener()
        except Exception:
            opener = None
        self.write({"t": "tab_opener", "tab": id(page) % 100000, "opener": id(opener) % 100000 if opener else None, "opener_url": bare(opener.url) if opener else ""})

    def _wire(self, page) -> None:
        from job_agent.apply.controller import _Status
        st = {"status": _Status(page), "task": None, "last": None, "unsnapped": [], "hash": None, "epoch": None}
        self.pages[page] = st
        page.on("framenavigated", lambda fr: (self.write({"t": "navigate", "url": bare(fr.url)}), self._schedule(page, "navigate")) if fr == page.main_frame else None)
        page.on("dialog", lambda d: self.write({"t": "browser_dialog", "kind": d.type, "message": d.message[:120]}))
        page.on("close", lambda: self.write({"t": "tab_closed"}))
        page.on("response", lambda r: self.net.append({"m": r.request.method, "u": bare(r.url), "rt": r.request.resource_type, "s": r.status})
                if r.request.resource_type in {"document", "xhr", "fetch"} else None)

    async def _on_event(self, source, payload: str) -> None:
        page, ev = source["page"], json.loads(payload)
        if page not in self.pages:
            return
        st = self.pages[page]
        if ev["t"] == "auto":
            self.write({"t": "auto_change", "url": bare(page.url), "mutations": ev["muts"], "modal": ev["modal"]})
            self._schedule(page, "auto")
            return
        self.step += 1
        st["unsnapped"].append(self.step)
        dom = ev.pop("dom", "")
        if dom:
            (self.out / "states" / f"A{self.step:03d}.html").write_text(dom, encoding="utf-8")
            ev["dom_file"] = f"A{self.step:03d}.html"                    # the page as it was at this action, the exact control marked data-rec-target
        if ev.get("el") and ev["el"].get("tag") == "input" and ev["el"].get("type") == "password":
            ev["el"]["html"] = "<blanked secret control>"
        self.write({"step": self.step, "page": id(page) % 100000, "url": bare(page.url), "frame": bare(source["frame"].url) if source.get("frame") else "",
                    "before": st["last"], "before_epoch": ev.pop("epoch", None), **ev})
        self._schedule(page, f"{ev['t']}")

    async def _tag(self, page, epoch: str) -> None:
        for fr in page.frames:
            try:
                await fr.evaluate("(e) => { window.__recEpoch = e; }", epoch)
            except Exception:
                pass

    def _schedule(self, page, why: str) -> None:
        st = self.pages.get(page)
        if st is None:
            return
        if st["task"] and not st["task"].done():
            st["task"].cancel()                         # a newer action replaces the wait: one snapshot per burst
        st["task"] = asyncio.create_task(self._snapshot(page, why))

    async def _snapshot(self, page, why: str) -> None:
        try:
            await asyncio.sleep(self.settle)            # let the page react before reading it
        except asyncio.CancelledError:
            return
        st = self.pages.get(page)
        if st is None or page.is_closed():
            return
        try:
            from job_agent.apply.classify import classify
            from job_agent.apply.controller import observe
            from job_agent.apply.reveal import pick_apply
            obs, perception = await observe(page, st["status"], None)      # reads and stamps; changes nothing else
            model = perception.model
            self.nepoch += 1
            epoch = f"E{self.nepoch}"                                     # the stamps on the page now belong to this read: tag the page so every click says which
            await self._tag(page, epoch)
            main = await page.main_frame.evaluate("window.__recSerialize && window.__recSerialize()")
            if not main:
                return
            digest = hashlib.sha1(main["html"].encode()).hexdigest()
            if digest == st["hash"] and not st["unsnapped"] and why in {"auto", "navigate", "start"}:
                await self._tag(page, st["epoch"])                        # identical page, identical stamps: the earlier read still describes it
                return
            st["hash"], st["epoch"], self.nstate = digest, epoch, self.nstate + 1
            sid = f"S{self.nstate:03d}"
            (self.out / "states" / f"{sid}.html").write_text(main["html"], encoding="utf-8")
            frames = []
            for k, fr in enumerate(page.frames[1:], 1):
                if fr.url.startswith("http"):
                    try:
                        sub = await fr.evaluate("window.__recSerialize && window.__recSerialize()")
                        if sub:
                            (self.out / "states" / f"{sid}.frame{k}.html").write_text(sub["html"], encoding="utf-8")
                            frames.append({"url": bare(fr.url), "file": f"{sid}.frame{k}.html"})
                    except Exception:
                        pass
            if self.screenshots:
                await page.screenshot(path=str(self.out / "states" / f"{sid}.png"))
            try:
                delta = await page.main_frame.evaluate("window.__recDrain && window.__recDrain()") or {}
            except Exception:
                delta = {}
            dump = model.model_dump()
            for f in dump["fields"]:
                if _SECRET_LABEL.search(f.get("label", "")):
                    f["value"] = "<blanked>"
            would = []
            if self.facts:
                from job_agent.apply.prefill import prefill
                for f in model.fields:
                    pf = prefill(f, self.facts)
                    if pf is not None:
                        would.append({"id": f.id, "label": f.label, "kind": f.kind, "value": pf.value, "basis": pf.basis, "blank": pf.blank})
            c = classify(obs)
            apply_id = pick_apply(model)
            self.write({"t": "state", "id": sid, "epoch": epoch, "why": why, "url": bare(page.url), "title": main["title"], "after_steps": st["unsnapped"], "model": dump,
                        "engine": {"state": c.state.value, "confidence": c.confidence, "reasons": c.reasons, "advance_id": c.advance_id,
                                   "pick_apply": apply_id, "would_answer": would},
                        "frames": frames, "delta": delta, "network": self.net[-40:], "viewport": page.viewport_size})
            st["unsnapped"], st["last"], self.net = [], sid, []
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.write({"t": "state_failed", "why": why, "error": f"{type(error).__name__}: {str(error)[:200]}"})

    async def stop(self) -> None:
        tasks = [s["task"] for s in self.pages.values() if s["task"] and not s["task"].done()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.settle + 6)    # let the last snapshot finish
        self.log.close()
