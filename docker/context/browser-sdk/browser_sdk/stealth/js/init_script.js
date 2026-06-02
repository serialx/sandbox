// BrowserFlare stealth init script — injected at document_start via add_init_script
(function () {
  // 1. Remove navigator.webdriver — prevents Selenium/Playwright detection
  try {
    delete Object.getPrototypeOf(navigator).webdriver;
  } catch (e) {
    Object.defineProperty(navigator, "webdriver", {
      get: () => undefined,
    });
  }

  // 2. Spoof navigator.languages
  Object.defineProperty(navigator, "languages", {
    get: () => ["en-US"],
    configurable: true,
  });

  // 3. Override permissions.query for notifications
  const originalQuery = window.navigator.permissions.query.bind(
    window.navigator.permissions
  );
  window.navigator.permissions.query = (params) => {
    if (params.name === "notifications") {
      return Promise.resolve({
        state: Notification.permission,
        onchange: null,
      });
    }
    return originalQuery(params);
  };

  // 4. Force Shadow DOM to open mode — prevents anti-bot closed shadow root checks
  const originalAttachShadow = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (options) {
    return originalAttachShadow.call(this, { ...options, mode: "open" });
  };

  // 5. Protect postMessage for extension communication
  window.__browserflareOriginalPostMessage = window.postMessage;
})();
