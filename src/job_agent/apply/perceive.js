// Semantic page perception. Runs inside a frame; returns a JSON-able page model.
// No site-specific selectors: everything is derived from roles, labels, states and structure.
// Usage: page.evaluate(PERCEIVE_JS, {prefix: "f0-"})
(opts) => {
  const prefix = (opts && opts.prefix) || "";
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

  const visible = (el) => {
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || el.getAttribute("aria-hidden") === "true") return false;
    return el.getClientRects().length > 0;
  };
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
    const pt = precedingText(el);
    if (pt) return pt;
    return clean(el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name") || "");
  };

  const helpOf = (el) => textOfIds(el.getAttribute("aria-describedby"));
  const errorOf = (el) => {
    const em = textOfIds(el.getAttribute("aria-errormessage"));
    if (em) return em;
    const holder = el.closest("[class*=field],[class*=form-group],[class*=question]") || el.parentElement;
    const err = holder && holder.querySelector("[role=alert],[class*=error],[class*=invalid]");
    return err && err !== el ? clip(err.innerText, 160) : "";
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

  // ---- collect fields ------------------------------------------------------------------------
  const fields = [];
  const actions = [];
  const seenGroups = new Map();

  const addField = (f) => { f.label = clean(f.label).replace(/\s*[*✱]+\s*/g, " ").trim(); f.index = fields.length; fields.push(f); return f; };

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

  const PLACEHOLDER = /^(select|choose|please select|search|type to search|pick)\b[^a-z0-9]*$|^[-—–.]*$/i;
  const comboValue = (el, label) => {
    if (el.value) return clean(el.value);
    let text = fieldText(el);
    for (const drop of [label, helpOf(el), errorOf(el), el.getAttribute("placeholder")]) if (drop) text = clean(text.split(drop).join(" "));
    text = clean(text.replace(/\*+/g, " "));
    return PLACEHOLDER.test(text) ? "" : clip(text, 80);
  };

  for (const el of all) {
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
        id: stamp(el), kind: tag === "a" || role === "link" ? "link" : "button", label: clip(label, 80),
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
    if (type !== "file" && !visible(el)) continue;
    if (el.disabled || el.readOnly) continue;

    const label = labelOf(el);

    if (type === "radio" || role === "radio") {
      const name = el.getAttribute("name") || "";
      const grp = el.closest("fieldset,[role=radiogroup]");
      const key = "radio|" + (name || "") + "|" + (grp ? stamp(grp) : "");
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
    addField({
      id: stamp(el), kind, label: fieldLabel, help: helpOf(el), required: isRequired(el, label),
      value: kind === "file" ? (el.files && el.files.length ? el.files[0].name : "") : clean(el.value !== undefined && el.value !== "" ? el.value : el.getAttribute("contenteditable") === "true" ? el.innerText : ""),
      options: null, placeholder: clip(el.getAttribute("placeholder"), 60), maxlength: el.maxLength > 0 ? el.maxLength : null,
      pattern: el.getAttribute("pattern") || null, accept: el.getAttribute("accept") || null,
      invalid: el.getAttribute("aria-invalid") === "true" || (el.willValidate && !el.checkValidity() && el.value !== ""),
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

  const headings = Array.from(document.querySelectorAll("h1,h2")).filter(visible).slice(0, 6).map((h) => clip(h.innerText, 100));
  const alerts = Array.from(document.querySelectorAll("[role=alert],[aria-live=assertive]")).filter(visible).map((a) => clip(a.innerText, 160)).filter(Boolean).slice(0, 5);

  return {
    url: location.href, title: document.title, headings, alerts,
    fields: fields.map(({ index, ...f }) => f), actions,
    text_digest: clip((document.body && document.body.innerText) || "", 3000),
  };
}
