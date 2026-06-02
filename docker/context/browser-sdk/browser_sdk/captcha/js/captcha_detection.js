// BrowserFlare CAPTCHA detection script
// Detects BLOCKING captchas only (overlays covering 80%+ of viewport)
(function () {
  const result = {
    detected: false,
    type: "",
    confidence: 0,
    indicators: [],
    url: window.location.href,
    timestamp: new Date().toISOString(),
  };

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 10 && rect.height > 10;
  }

  function coversViewport(el) {
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    return rect.width >= vw * 0.8 && rect.height >= vh * 0.8;
  }

  // --- Cloudflare ---
  const bodyText = document.body ? document.body.innerText || "" : "";
  if (
    bodyText.includes("Just a moment") &&
    (bodyText.includes("Checking") || bodyText.includes("Verify"))
  ) {
    result.detected = true;
    result.type = "cloudflare";
    result.confidence = 90;
    result.indicators.push("cloudflare_challenge_text");
  }
  const turnstile = document.querySelector(
    'iframe[src*="challenges.cloudflare.com"]'
  );
  if (turnstile && isVisible(turnstile)) {
    result.detected = true;
    result.type = "cloudflare";
    result.confidence = Math.max(result.confidence, 95);
    result.indicators.push("cloudflare_turnstile_iframe");
  }

  // --- reCAPTCHA v2 ---
  const recaptchaFrames = document.querySelectorAll(
    'iframe[src*="google.com/recaptcha"]'
  );
  for (const frame of recaptchaFrames) {
    if (!isVisible(frame)) continue;
    const rect = frame.getBoundingClientRect();
    // v2 anchor checkbox: ~304x78
    if (rect.width > 250 && rect.width < 350 && rect.height > 60) {
      result.detected = true;
      result.type = "recaptcha_v2";
      result.confidence = Math.max(result.confidence, 85);
      result.indicators.push("recaptcha_v2_anchor");
    }
    // v2 bframe challenge: ~400x580
    if (rect.width > 350 && rect.height > 400) {
      result.detected = true;
      result.type = "recaptcha_v2";
      result.confidence = Math.max(result.confidence, 90);
      result.indicators.push("recaptcha_v2_bframe");
    }
  }
  // v3 badge — NOT blocking, skip
  const v3Badge = document.querySelector(".grecaptcha-badge");
  if (v3Badge && isVisible(v3Badge)) {
    result.indicators.push("recaptcha_v3_badge_non_blocking");
  }

  // --- hCaptcha ---
  const hcaptcha = document.querySelector('[data-sitekey][data-hcaptcha]');
  const hcaptchaFrame = document.querySelector(
    'iframe[src*="hcaptcha.com"], iframe[src*="assets.hcaptcha.com"]'
  );
  if ((hcaptcha && isVisible(hcaptcha)) || (hcaptchaFrame && isVisible(hcaptchaFrame))) {
    result.detected = true;
    result.type = "hcaptcha";
    result.confidence = Math.max(result.confidence, 85);
    result.indicators.push("hcaptcha_element");
  }

  // --- FunCaptcha (ArkoseLabs) ---
  const funcaptcha = document.querySelector(
    'iframe[src*="arkoselabs.com"], iframe[src*="funcaptcha.com"]'
  );
  if (funcaptcha && isVisible(funcaptcha)) {
    result.detected = true;
    result.type = "funcaptcha";
    result.confidence = Math.max(result.confidence, 85);
    result.indicators.push("funcaptcha_iframe");
  }

  // --- AWS WAF ---
  if (
    bodyText.includes("To continue, please verify") &&
    bodyText.includes("human")
  ) {
    result.detected = true;
    result.type = "aws_waf";
    result.confidence = Math.max(result.confidence, 80);
    result.indicators.push("aws_waf_text");
  }

  // --- Geetest ---
  const geetest = document.querySelector(
    ".geetest_holder, .geetest_panel, .geetest_radar"
  );
  if (geetest && isVisible(geetest)) {
    result.detected = true;
    result.type = "geetest";
    result.confidence = Math.max(result.confidence, 85);
    result.indicators.push("geetest_element");
  }

  // --- DataDome ---
  const datadome = document.querySelector(
    ".dd-captcha, iframe[src*='datadome']"
  );
  if (datadome && isVisible(datadome)) {
    result.detected = true;
    result.type = "datadome";
    result.confidence = Math.max(result.confidence, 85);
    result.indicators.push("datadome_element");
  }

  // --- Sucuri Firewall ---
  if (
    bodyText.includes("Sucuri WebSite Firewall") ||
    bodyText.includes("Access Denied - Sucuri")
  ) {
    result.detected = true;
    result.type = "sucuri";
    result.confidence = Math.max(result.confidence, 80);
    result.indicators.push("sucuri_block_text");
  }

  // --- Reddit Security ---
  if (
    window.location.hostname.includes("reddit.com") &&
    bodyText.includes("Reddit Security")
  ) {
    result.detected = true;
    result.type = "reddit";
    result.confidence = Math.max(result.confidence, 80);
    result.indicators.push("reddit_security_block");
  }

  // --- Generic access block ---
  if (!result.detected) {
    const title = document.title.toLowerCase();
    const lowerBody = bodyText.toLowerCase().substring(0, 2000);
    const blockSignals = [
      "access denied",
      "403 forbidden",
      "please complete the captcha",
      "verify you are not a robot",
      "verify you are human",
      "blocked",
      "unusual traffic",
    ];
    for (const signal of blockSignals) {
      if (title.includes(signal) || lowerBody.includes(signal)) {
        result.detected = true;
        result.type = "generic_block";
        result.confidence = Math.max(result.confidence, 60);
        result.indicators.push("generic_block_text:" + signal);
        break;
      }
    }
  }

  // --- High z-index overlay detection ---
  if (!result.detected) {
    const allElements = document.querySelectorAll("*");
    for (const el of allElements) {
      const style = window.getComputedStyle(el);
      const zIndex = parseInt(style.zIndex, 10);
      if (zIndex > 9999 && isVisible(el) && coversViewport(el)) {
        result.detected = true;
        result.type = "generic_block";
        result.confidence = Math.max(result.confidence, 50);
        result.indicators.push("high_zindex_overlay");
        break;
      }
    }
  }

  return result;
})();
