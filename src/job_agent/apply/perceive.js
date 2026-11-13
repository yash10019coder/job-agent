// Semantic page perception. Runs inside a frame; returns a JSON-able page model.
// No site-specific selectors: everything is derived from roles, labels, states and structure.
// Usage: page.evaluate(PERCEIVE_JS, {prefix: "f0-"})
(opts) => {
  const prefix = (opts && opts.prefix) || "";
  const frameLabel = (opts && opts.frameLabel) || "";
  document.querySelectorAll("[data-agent-id]").forEach((e) => e.removeAttribute("data-agent-id"));

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clip = (s, n) => { s = clean(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  let counter = 0;
  const stamp = (el) => {
    let id = el.getAttribute("data-agent-id");
    if (!id) { id = prefix + "n" + (++counter); el.setAttribute("data-agent-id", id); }
    return id;
  };

  // ---- traversal (DOM + open shadow roots) -------------------------------------------------
  const all = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      all.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);

  // "Visible to a human": rendered, non-zero size, on the page (not parked off-screen), not transparent.
  // Honeypot inputs (bot traps) are hidden exactly this way, and filling one flags the application as spam.
  const visible = (el) => {
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || el.getAttribute("aria-hidden") === "true") return false;
    const rects = el.getClientRects();
    if (rects.length === 0) return false;
    const r = rects[0];
    if (r.right < 0 || r.bottom + window.scrollY < 0 || r.left > document.documentElement.scrollWidth + 50) return false;
    // Widget internals (react-select's 1-2px or transparent input behind the visible control) are real controls:
    // the size/opacity trap checks only apply to plain inputs with no ARIA role or popup.
    const widget = el.hasAttribute("role") || el.hasAttribute("aria-haspopup") || el.hasAttribute("aria-controls") || el.hasAttribute("aria-autocomplete");
    if (!widget && /^(INPUT|TEXTAREA)$/.test(el.tagName) && (r.width < 1 || r.height < 1)) return false;
    if (!widget && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) for (let n = el; n && n !== document.documentElement; n = n.parentElement) if (parseFloat(getComputedStyle(n).opacity) === 0) return false;
    return true;
  };
  // Where on the page a control lives: a "Login" link in the header is not a gate on the form in main.
  const regionOf = (el) => {
    if (el.closest("[role=dialog],dialog,[aria-modal=true]")) return "dialog";
    if (el.closest("header,[role=banner]")) return "header";
    if (el.closest("nav,[role=navigation]")) return "nav";
    if (el.closest("footer,[role=contentinfo]")) return "footer";
    return "main";
  };
  let curEl = null;
  const byId = (id) => document.getElementById(id);
  const textOfIds = (ids) =>
    clean((ids || "").split(/\s+/).map((i) => (byId(i) ? byId(i).innerText || byId(i).textContent : "")).join(" "));

  // ---- naming ------------------------------------------------------------------------------
  const ownText = (el) => clean(el.innerText || el.textContent);
  const containsControl = (el) => !!el.querySelector("input,select,textarea,[role=combobox],[role=listbox],button");

  // Closest preceding "question" text for controls that have no explicit label.
  const precedingText = (el) => {
    let node = el;
    for (let depth = 0; depth < 5 && node && node !== document.body; depth++) {
      let sib = node.previousElementSibling;
      while (sib) {
        const t = ownText(sib);
        if (t && t.length <= 240 && !containsControl(sib)) return t;
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return "";
  };

  const groupLabel = (el) => {
    const group = el.closest("fieldset,[role=radiogroup],[role=group],[role=checkboxgroup]");
    if (group) {
      const legend = group.querySelector(":scope > legend");
      if (legend) return clean(legend.innerText);
      const al = group.getAttribute("aria-label") || textOfIds(group.getAttribute("aria-labelledby"));
      if (al) return clean(al);
      const first = Array.from(group.children).find((c) => !containsControl(c) && ownText(c));
      if (first) return ownText(first);
    }
    return "";
  };

  const labelOf = (el) => {
    const lb = textOfIds(el.getAttribute("aria-labelledby"));
    if (lb) return lb;
    const al = el.getAttribute("aria-label");
    if (al) return clean(al);
    if (el.labels && el.labels.length) return clean(Array.from(el.labels).map((l) => l.innerText).join(" "));
    const wrap = el.closest("label");
    if (wrap) return clean(wrap.innerText);
    const AC = { "given-name": "First name", "family-name": "Last name", name: "Full name", email: "Email", tel: "Phone", "street-address": "Address",
      "address-line1": "Address", "address-level2": "City", "address-level1": "State", "postal-code": "Postal code", country: "Country", "country-name": "Country",
      organization: "Company", "organization-title": "Job title" };
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase().split(/\s+/).pop();
    if (AC[ac]) return AC[ac];
    // Inputs inside web components (custom-element form kits): the label lives on the host element or in autocomplete.
    const host = el.getRootNode && el.getRootNode().host;
    if (host) {
      const t = host.getAttribute("label") || host.getAttribute("aria-label") || host.getAttribute("placeholder") || host.getAttribute("title") || host.getAttribute("name");
      if (t) return clean(t);
    }
    const pt = precedingText(el);
    if (pt) return pt;
    return clean(el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name") || "");
  };

  const helpOf = (el) => textOfIds(el.getAttribute("aria-describedby"));
  const errorOf = (el) => {
    const em = textOfIds(el.getAttribute("aria-errormessage"));
    if (em) return em;
    // Only a VISIBLE error inside this control's own wrapper counts (forms often keep hidden error templates for every field).
    let box = el;
    const controls = "input:not([type=hidden]),select,textarea,[role=combobox]";
    while (box.parentElement && box.parentElement !== document.body && box.parentElement.querySelectorAll(controls).length <= 1) box = box.parentElement;
    const err = Array.from(box.querySelectorAll("[role=alert],[class*=error],[class*=invalid]")).find((n) => n !== el && !n.contains(el) && visible(n) && clean(n.innerText));
    return err ? clip(err.innerText, 160) : "";
  };

  const sectionOf = (el) => {
    let node = el;
    while (node && node !== document.body) {
      let sib = node.previousElementSibling;
      while (sib) {
        const h = /^H[1-4]$/.test(sib.tagName) ? sib : sib.querySelector(":scope > h1,:scope > h2,:scope > h3,:scope > h4");
        if (h && ownText(h)) return clip(ownText(h), 80);
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return "";
  };

  const isRequired = (el, label) =>
    el.required === true || el.getAttribute("aria-required") === "true" || /[*✱]\s*$|\(required\)/i.test(label);

  window.__agentLabelOf = labelOf;
  // Native radios/checkboxes are often display:none behind a styled label: the label is the visible control.
  const toggleVisible = (el) => {
    const lab = (el.labels && el.labels[0]) || el.closest("label");
    return !!lab && visible(lab);
  };
  // Read-only inputs that open a calendar: date-looking placeholder/type/label.
  const dateLike = (el, label) =>
    /^(date|datetime-local|month)$/.test((el.getAttribute("type") || "").toLowerCase()) ||
    /\b(dd|mm|yyyy|yy)\b|[dmy]{2,4}[\/.-][dmy]{2,4}/i.test(el.getAttribute("placeholder") || "") ||
    /\bdate\b|birth|\bdob\b/i.test(label);
  // Pill/card "selected" state: ARIA first, then a selected-looking class token. Never a guess beyond that.
  const cardSelected = (el) =>
    el.getAttribute("aria-pressed") === "true" || el.getAttribute("aria-selected") === "true" || el.getAttribute("aria-checked") === "true" ||
    Array.from(el.classList).some((c) => /(^|[-_])(selected|active|checked|chosen|current|on)($|[-_])/i.test(c));
  window.__agentCardSelected = cardSelected;

  // ---- collect fields ------------------------------------------------------------------------
  const fields = [];
  const actions = [];
  const seenGroups = new Map();

  const addField = (f) => { f.region = curEl ? regionOf(curEl) : "main"; f.label = clean(f.label).replace(/\s*[*✱]+\s*/g, " ").trim(); f.index = fields.length; fields.push(f); return f; };

  const comboOptions = (el) => {
    const ctl = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
    const list = ctl ? byId(ctl) : null;
    if (list && visible(list)) {
      return Array.from(list.querySelectorAll("[role=option]")).filter(visible).map((o) => ({ id: stamp(o), label: clip(o.innerText, 120) }));
    }
    return null;
  };

  // Text of a control's own wrapper (label + widget + selected value): the smallest ancestor holding only
  // this control, minus live regions, popups, links and buttons. No class-name guessing.
  const fieldText = (el) => {
    let box = el;
    const controls = "input:not([type=hidden]),select,textarea,[role=combobox]";
    while (box.parentElement && box.parentElement !== document.body && box.parentElement.querySelectorAll(controls).length <= 1) box = box.parentElement;
    const clone = box.cloneNode(true);
    clone.querySelectorAll("[aria-live],[role=status],[role=alert],[role=listbox],a,button,[role=button],script,style").forEach((n) => n.remove());
    return clean(clone.textContent);
  };
  window.__agentFieldText = fieldText;

  const PLACEHOLDER = /^(select|choose|please select|search|type to search|pick)\b[^a-z0-9]*$|^[-—–.]*$|make (a )?selection|select an? (option|value)|choose an? (option|value)/i;
  const comboValue = (el, label) => {
    if (el.value) return clean(el.value);
    let text = fieldText(el);
    for (const drop of [label, helpOf(el), errorOf(el), el.getAttribute("placeholder")]) if (drop) text = clean(text.split(drop).join(" "));
    text = clean(text.replace(/\*+/g, " "));
    return PLACEHOLDER.test(text) ? "" : clip(text, 80);
  };

  for (const el of all) {
    curEl = el;
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();

    // Buttons and links -> actions
    if (tag === "button" || role === "button" || (tag === "a" && el.hasAttribute("href")) || role === "link" || (tag === "input" && /^(submit|button)$/.test(type))) {
      if (!visible(el)) continue;
      if (el.closest("[role=listbox],[role=option]")) continue;
      const label = clean(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title"));
      if (!label) continue;
      actions.push({
        region: regionOf(el), id: stamp(el), kind: tag === "a" || role === "link" ? "link" : "button", label: clip(label, 80),
        primary: type === "submit" || /^(next|continue|submit|apply|save and continue|review)/i.test(label),
        disabled: el.disabled === true || el.getAttribute("aria-disabled") === "true",
      });
      continue;
    }

    const isInput = tag === "input" && !/^(hidden|submit|button|image|reset)$/.test(type);
    const isField = isInput || tag === "select" || tag === "textarea" ||
      /^(combobox|textbox|switch|checkbox|radio|spinbutton)$/.test(role) || el.getAttribute("contenteditable") === "true";
    if (!isField) continue;
    if (el.closest("[role=listbox]") && role !== "combobox") continue;
    const isToggle = type === "radio" || type === "checkbox" || role === "radio" || role === "checkbox";
    if (type !== "file" && !visible(el) && !(isToggle && toggleVisible(el))) continue;
    if (el.disabled) continue;

    const label = (el === document.body || el === document.documentElement) && frameLabel ? frameLabel : labelOf(el);
    if (el.readOnly && !dateLike(el, label)) continue;

    if (type === "radio" || role === "radio") {
      const name = el.getAttribute("name") || "";
      const grp = el.closest("fieldset,[role=radiogroup]");
      // No name and no fieldset: separate questions are told apart by the text above them.
      const anchor = name || grp ? "" : precedingText(el.closest("label") || el);
      const key = "radio|" + (name || "") + "|" + (grp ? stamp(grp) : "") + "|" + anchor;
      const optLabel = clean((el.labels && el.labels[0] ? el.labels[0].innerText : "") || el.getAttribute("aria-label") || (el.closest("label") ? el.closest("label").innerText : "") || el.value);
      let f = seenGroups.get(key);
      if (!f) {
        const q = groupLabel(el) || precedingText(el.closest("label") || el) || label;
        f = addField({ id: stamp(el), kind: "radio_group", label: q, help: helpOf(el), required: isRequired(el, q), value: "", options: [], invalid: el.getAttribute("aria-invalid") === "true", error: errorOf(el), section: sectionOf(el) });
        seenGroups.set(key, f);
      }
      const checked = el.checked === true || el.getAttribute("aria-checked") === "true";
      f.options.push({ id: stamp(el), label: optLabel || el.value });
      if (checked) f.value = optLabel || el.value;
      continue;
    }

    if (type === "checkbox" || role === "checkbox" || role === "switch") {
      const name = el.getAttribute("name") || "";
      const peers = name ? all.filter((o) => o.tagName === "INPUT" && o.type === "checkbox" && o.getAttribute("name") === name && visible(o)) : [];
      const own = clean((el.labels && el.labels[0] ? el.labels[0].innerText : "") || el.getAttribute("aria-label") || (el.closest("label") ? el.closest("label").innerText : ""));
      const checked = el.checked === true || el.getAttribute("aria-checked") === "true";
      if (peers.length > 1) {
        const key = "cb|" + name;
        let f = seenGroups.get(key);
        if (!f) {
          const q = groupLabel(el) || label;
          f = addField({ id: stamp(el), kind: "checkbox_group", label: q, help: helpOf(el), required: isRequired(el, q), value: "", options: [], invalid: false, error: errorOf(el), section: sectionOf(el) });
          seenGroups.set(key, f);
        }
        f.options.push({ id: stamp(el), label: own || el.value });
        if (checked) f.value = (f.value ? f.value + "; " : "") + (own || el.value);
      } else {
        addField({ id: stamp(el), kind: "checkbox", label: own || label, help: helpOf(el), required: isRequired(el, label), value: checked ? "checked" : "", options: null, invalid: el.getAttribute("aria-invalid") === "true", error: errorOf(el), section: sectionOf(el) });
      }
      continue;
    }

    if (tag === "select") {
      const opts = Array.from(el.options).filter((o) => !o.disabled).map((o) => ({ id: "", label: clean(o.text), value: o.value }));
      const sel = el.selectedOptions[0];
      addField({ id: stamp(el), kind: "select", label, help: helpOf(el), required: isRequired(el, label), value: sel && sel.value ? clean(sel.text) : "", options: opts.filter((o) => o.label && o.value !== ""), invalid: el.getAttribute("aria-invalid") === "true", error: errorOf(el), section: sectionOf(el) });
      continue;
    }

    if (role === "combobox" || el.getAttribute("aria-haspopup") === "listbox" || el.getAttribute("aria-autocomplete") === "list") {
      const opts = comboOptions(el);
      addField({ id: stamp(el), kind: "combobox", label, help: helpOf(el), required: isRequired(el, label), value: comboValue(el, label), options: opts, async_options: opts === null, invalid: el.getAttribute("aria-invalid") === "true", error: errorOf(el), section: sectionOf(el) });
      continue;
    }

    let kind = "text";
    let fieldLabel = label;
    if (type === "file") {
      kind = "file";
      // Upload widgets are usually labelled just "Attach"/"Upload"; the input's id/name says what it is.
      if (/^(attach|upload|browse|choose( a)? file|select( a)? file)/i.test(label) || !label) {
        const hint = clean((el.id || el.getAttribute("name") || "").replace(/[_\-\[\]]+/g, " "));
        if (hint) fieldLabel = hint.charAt(0).toUpperCase() + hint.slice(1);
      }
    }
    else if (tag === "textarea" || el.getAttribute("contenteditable") === "true") kind = "textarea";
    else if (["email", "tel", "number", "date", "url", "password"].includes(type)) kind = type;
    else if (el.readOnly || /pick (a )?date|select (a )?date|choose (a )?date|mm\/dd|dd\/mm|yyyy/i.test(el.getAttribute("placeholder") || "")) kind = "date";
    addField({
      id: stamp(el), kind, label: fieldLabel, help: helpOf(el), required: isRequired(el, label),
      value: kind === "file" ? (el.files && el.files.length ? el.files[0].name : "") : clean(el.value !== undefined && el.value !== "" ? el.value : el.getAttribute("contenteditable") === "true" ? el.innerText : ""),
      options: null, placeholder: clip(el.getAttribute("placeholder"), 60), maxlength: el.maxLength > 0 ? el.maxLength : null,
      pattern: el.getAttribute("pattern") || null, accept: el.getAttribute("accept") || null, readonly: el.readOnly === true,
      invalid: el.getAttribute("aria-invalid") === "true" || !!(el.willValidate && !el.checkValidity() && el.value !== ""),
      error: errorOf(el), section: sectionOf(el),
    });
  }

  // Groups without a fieldset/legend end up labelled with their first option; use the text above the group.
  const byAgentId = (id) => document.querySelector('[data-agent-id="' + id + '"]');
  for (const f of fields) {
    if (!f.options || !/^(radio_group|checkbox_group)$/.test(f.kind)) continue;
    const norm = (t) => clean(t).toLowerCase();
    if (!f.options.some((o) => norm(o.label) === norm(f.label))) continue;
    const els = f.options.map((o) => byAgentId(o.id)).filter(Boolean);
    let box = els[0];
    while (box && box.parentElement && !els.every((e) => box.contains(e))) box = box.parentElement;
    while (box && box.parentElement && box.parentElement !== document.body && box.parentElement.querySelectorAll("input,select,textarea").length === els.length) box = box.parentElement;
    const first = els[0] && (els[0].closest("label") || els[0]);
    const above = (first && precedingText(first)) || (box ? precedingText(box) : "");
    if (above) f.label = clean(above).replace(/\s*[*✱]+\s*/g, " ").trim();
  }

  // ---- clickable cards / pills with no input and no role (cursor:pointer is the only signal) -----------
  const unparsed = [];
  const isPointer = (el) => getComputedStyle(el).cursor === "pointer";
  const candidates = all.filter((el) =>
    /^(DIV|SPAN|LI|P|SECTION)$/.test(el.tagName) && !el.hasAttribute("data-agent-id") && !el.closest("[data-agent-id],a,button,label,select,nav,header,footer,[role=listbox],[role=menu]") &&
    isPointer(el) && !(el.parentElement && isPointer(el.parentElement)) && visible(el) &&
    !el.querySelector("input,select,textarea,button,a[href],[role=button],[role=option],[role=combobox]") && ownText(el) && ownText(el).length <= 80);
  const byParent = new Map();
  for (const c of candidates) { const k = c.parentElement; if (!byParent.has(k)) byParent.set(k, []); byParent.get(k).push(c); }
  for (const [parent, cards] of byParent) {
    if (cards.length >= 2 && cards.every((c) => c.tagName === cards[0].tagName)) {
      curEl = cards[0];
      const q = precedingText(cards[0]) || (parent && precedingText(parent)) || "";
      const chosen = cards.filter(cardSelected);
      addField({ id: stamp(cards[0]), kind: "radio_group", cards: true, label: q, help: "", required: /[*✱]/.test(q), value: chosen.length ? ownText(chosen[0]) : "",
        options: cards.map((c) => ({ id: stamp(c), label: ownText(c) })), invalid: false, error: "", section: sectionOf(cards[0]) });
    } else {
      for (const c of cards) unparsed.push({ text: ownText(c), required: /[*✱]/.test(precedingText(c) + ownText(c)) });
    }
  }
  // Visible text boxes we did not turn into fields (e.g. sandboxed/closed widgets) still need to be reported.
  for (const el of all) {
    if (el.getAttribute("contenteditable") === "true" && !el.hasAttribute("data-agent-id") && visible(el) && el !== document.body)
      unparsed.push({ text: labelOf(el) || "editable area", required: false });
  }

  const headings = Array.from(document.querySelectorAll("h1,h2")).filter(visible).slice(0, 6).map((h) => clip(h.innerText, 100));
  const alerts = Array.from(document.querySelectorAll("[role=alert],[aria-live=assertive]")).filter(visible).map((a) => clip(a.innerText, 160)).filter(Boolean).slice(0, 5);

  return {
    url: location.href, title: document.title, headings, alerts,
    fields: fields.map(({ index, ...f }) => f), actions, unparsed: unparsed.slice(0, 10),
    text_digest: clip((document.body && document.body.innerText) || "", 3000),
  };
}
