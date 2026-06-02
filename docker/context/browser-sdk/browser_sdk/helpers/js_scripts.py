"""JS script loader with file caching."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=32)
def _load_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class JSScriptLoader:
    """Loads and caches JavaScript files bundled with the SDK."""

    def load(self, relative_path: str) -> str:
        """Load a JS file relative to the browser_sdk package root."""
        full = _PACKAGE_ROOT / relative_path
        return _load_file(str(full))

    def get_init_script(self) -> str:
        return self.load("stealth/js/init_script.js")

    def get_captcha_detection_script(self) -> str:
        return self.load("captcha/js/captcha_detection.js")

    def get_interactive_elements_script(self) -> str:
        return self.load("helpers/js/interactive_elements.js")

    def get_page_text_script(self) -> str:
        return self.load("helpers/js/page_text.js")

    def get_page_markdown_script(self) -> str:
        """Return a self-contained script (Turndown + extraction logic)."""
        turndown = self.load("helpers/js/vendor/turndown.browser.umd.js")
        main = self.load("helpers/js/page_markdown.js")
        return f"{turndown}\n{main}"


# Singleton
js_loader = JSScriptLoader()
