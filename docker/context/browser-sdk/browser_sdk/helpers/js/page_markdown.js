// Extract full page content as Markdown using Turndown.
// Vendor lib (turndown.browser.umd.js) must be prepended by the script loader.
(function () {
  var title = document.title || "";

  var td = new TurndownService({ headingStyle: "atx", codeBlockStyle: "fenced" });
  td.remove(["script", "style", "noscript"]);

  var markdown = "";
  if (document.body) {
    try {
      // 1) Prefer innerHTML string — preserves original markup most faithfully.
      markdown = td.turndown(document.body.innerHTML);
    } catch (_) {
      try {
        // 2) Fallback: pass DOM node directly (bypasses DOMParser / Trusted Types).
        markdown = td.turndown(document.body);
      } catch (e) {
        // 3) All attempts failed.
        markdown = "[markdown extraction failed: " + e.message + "]";
      }
    }
  }

  return { title: title, markdown: markdown };
})();
