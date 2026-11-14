// Summarizes the DOM into a compact, LLM-friendly structure (no raw HTML). Used by the crawler.
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const txt = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const labelFor = (el) => {
    if (el.labels && el.labels.length) return txt(el.labels[0]);
    const al = el.getAttribute("aria-label");
    if (al) return al;
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const n = document.getElementById(lb);
      if (n) return txt(n);
    }
    return "";
  };
  const fieldType = (el) =>
    el.getAttribute("type") || (el.tagName === "TEXTAREA" ? "textarea" : el.tagName === "SELECT" ? "select" : "text");
  const field = (el) => ({
    tag: el.tagName.toLowerCase(),
    type: fieldType(el),
    name: el.getAttribute("name") || "",
    id: el.id || "",
    label: labelFor(el),
    placeholder: el.getAttribute("placeholder") || "",
    required: el.required || false,
    testid: el.getAttribute("data-testid") || "",
  });
  const buttons = [...document.querySelectorAll("button, [role=button], input[type=submit], input[type=button]")]
    .filter(vis)
    .slice(0, 40)
    .map((b) => ({
      text: txt(b) || b.value || "",
      aria_label: b.getAttribute("aria-label") || "",
      type: b.getAttribute("type") || "",
      id: b.id || "",
      testid: b.getAttribute("data-testid") || "",
    }));
  const links = [...document.querySelectorAll("a[href]")].slice(0, 200).map((a) => ({ href: a.href, text: txt(a) }));
  const forms = [...document.querySelectorAll("form")].slice(0, 10).map((f) => {
    const s = f.querySelector("button[type=submit], button:not([type]), input[type=submit]");
    return {
      action: f.getAttribute("action") || "",
      method: (f.getAttribute("method") || "get").toLowerCase(),
      id: f.id || "",
      fields: [...f.querySelectorAll("input, textarea, select")]
        .filter((i) => i.type !== "hidden")
        .slice(0, 20)
        .map(field),
      submit: s ? txt(s) || s.value || "" : "",
    };
  });
  const loose = [...document.querySelectorAll("input, textarea, select")]
    .filter((i) => !i.form && i.type !== "hidden" && vis(i))
    .slice(0, 20)
    .map(field);
  const headings = [...document.querySelectorAll("h1, h2, h3")]
    .slice(0, 20)
    .map((h) => h.tagName.toLowerCase() + ": " + txt(h));
  const landmarkSel = "header, nav, main, footer, aside, [role=navigation], [role=main], [role=search]";
  const landmarks = [...document.querySelectorAll(landmarkSel)].slice(0, 12).map((l) => {
    const al = l.getAttribute("aria-label");
    return l.tagName.toLowerCase() + (al ? "[" + al + "]" : "");
  });
  const testids = [...document.querySelectorAll("[data-testid]")].slice(0, 30).map((e) => e.getAttribute("data-testid"));
  const alerts = [...document.querySelectorAll("[role=alert], [role=status]")].map(txt).filter(Boolean).slice(0, 5);
  const main = document.querySelector("main") || document.body;
  return {
    headings,
    landmarks,
    buttons,
    links,
    forms,
    loose_fields: loose,
    testids,
    alerts,
    text: main ? main.innerText.replace(/\s+/g, " ").trim().slice(0, 600) : "",
    images: document.images.length,
    lang: document.documentElement.lang || "",
  };
}
