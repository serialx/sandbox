// Collects all interactive elements on the page with index, tag, text, bounding box, etc.
(function () {
  const selectors = [
    "a[href]",
    "button",
    'input:not([type="hidden"])',
    "select",
    "textarea",
    '[role="button"]',
    '[role="link"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="tab"]',
    '[role="menuitem"]',
    "[onclick]",
    "[tabindex]",
  ];

  const seen = new Set();
  const results = [];
  let idx = 0;

  for (const sel of selectors) {
    for (const el of document.querySelectorAll(sel)) {
      if (seen.has(el)) continue;
      seen.add(el);

      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;

      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;

      const text =
        el.innerText?.trim()?.substring(0, 200) ||
        el.getAttribute("aria-label") ||
        el.getAttribute("title") ||
        el.getAttribute("placeholder") ||
        el.getAttribute("alt") ||
        "";

      results.push({
        index: idx++,
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute("role"),
        text: text,
        placeholder: el.getAttribute("placeholder"),
        href: el.getAttribute("href"),
        type: el.getAttribute("type"),
        bounding_box: {
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
        },
        is_visible: true,
        is_enabled: !el.disabled,
      });
    }
  }

  return results;
})();
