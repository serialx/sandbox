"""
AIO Sandbox: matplotlib fontconfig bridge.

matplotlib has its own font_manager that does NOT use fontconfig.
When a font is not in its cache (e.g. SimHei), it silently falls back to
the "best scoring" available font (e.g. Liberation Sans) — no exception,
just wrong glyphs.

This module patches FontManager.findfont() with two layers of fallback:

1. fc-match resolution: if requested fonts (e.g. SimHei) are not in
   matplotlib's cache, resolve them via fontconfig (which respects our
   alias config in 99-font-aliases.conf).

2. CJK fallback injection: if the resolved font list contains NO CJK-capable
   fonts (e.g. after plt.style.use() overwrites user's CJK settings with
   Western-only fonts), inject a CJK fallback so Chinese/Japanese/Korean
   text renders correctly instead of showing tofu blocks.

Installed at Python startup via a .pth file.
"""

import os
import importlib.abc
import importlib.machinery
import subprocess
import sys

_fc_cache = {}

# CJK fallback fonts to inject when no CJK font is in the resolved list.
# Keep this aligned with fonts baked into the image rather than user-installed
# fonts that may or may not exist in a given sandbox.
_CJK_FALLBACK_FAMILIES = ["Noto Sans CJK SC", "Noto Sans CJK JP"]

# Substrings that indicate a font family name is CJK-capable.
_CJK_INDICATORS = frozenset({
    "cjk", "wenquanyi", "wqy", "ar pl",
    "simhei", "simsun", "microsoft yahei", "kaiti", "fangsong",
    "dengxian", "stheiti", "stsong", "stkaiti", "stfangsong",
    "pingfang", "hiragino", "meiryo", "yu gothic", "yu mincho",
    "malgun", "gulim", "dotum", "batang", "nsimsun",
})

_GENERIC_FAMILIES = frozenset({
    "sans-serif", "serif", "monospace", "cursive", "fantasy",
})


def _fc_match_details(family):
    """Resolve a font family name via fontconfig."""
    if family in _fc_cache:
        return _fc_cache[family]
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{family[0]}\n%{file}", family],
            capture_output=True, text=True, timeout=5,
        )
        lines = proc.stdout.splitlines()
        matched_family = lines[0].strip() if lines else ""
        path = lines[1].strip() if len(lines) > 1 else ""
        if path and os.path.isfile(path):
            _fc_cache[family] = (matched_family, path)
            return _fc_cache[family]
    except Exception:
        pass
    _fc_cache[family] = (None, None)
    return _fc_cache[family]


def _fc_match(family):
    """Resolve a font family name to a file path via fontconfig."""
    return _fc_match_details(family)[1]


def _fc_match_family(family):
    """Return the matched font family name from fontconfig."""
    return _fc_match_details(family)[0]


def _is_cjk_family(family):
    """Check whether a font family name is likely CJK-capable."""
    family_lower = family.lower()
    return any(ind in family_lower for ind in _CJK_INDICATORS)


def _has_cjk_font(families):
    """Check if any family name in the list is likely CJK-capable."""
    return any(_is_cjk_family(family) for family in families)


def _resolve_families(prop):
    """Extract concrete font names, resolving aliases like 'sans-serif'."""
    import matplotlib

    if isinstance(prop, str):
        raw = [prop]
    elif hasattr(prop, "get_family"):
        raw = prop.get_family()
    else:
        return []

    resolved = []
    for family in raw:
        if family.lower() in _GENERIC_FAMILIES:
            resolved.extend(matplotlib.rcParams.get(f"font.{family}", []))
        else:
            resolved.append(family)
    return resolved


def _patch_findfont(fm_module):
    """Patch FontManager to resolve missing fonts via fontconfig."""
    FontManager = fm_module.FontManager

    if getattr(FontManager.findfont, "_aio_mpl_fontconfig_patched", False):
        return

    _original = FontManager.findfont

    # Build a set of known font family names from matplotlib's cache
    _known_cache = {}

    def _get_known_families(fm_instance):
        """Lazily build set of font families known to matplotlib."""
        fid = id(fm_instance)
        if fid not in _known_cache:
            _known_cache[fid] = {
                entry.name.lower()
                for entry in getattr(fm_instance, "ttflist", [])
            }
        return _known_cache[fid]

    def _resolve_concrete_family(self, family, known, *,
                                 fontext, directory,
                                 fallback_to_default, rebuild_if_missing,
                                 require_cjk_match=False):
        """Resolve one concrete family name to a font path."""
        if family.lower() in known:
            from matplotlib.font_manager import FontProperties
            return _original(
                self, FontProperties(family=[family]),
                fontext=fontext,
                directory=directory,
                fallback_to_default=fallback_to_default,
                rebuild_if_missing=rebuild_if_missing,
            )

        matched_path = _fc_match(family)
        if not matched_path:
            return None

        if require_cjk_match:
            matched_family = _fc_match_family(family) or ""
            if not _is_cjk_family(matched_family):
                return None

        return matched_path

    def _patched(self, prop, fontext="ttf", directory=None,
                 fallback_to_default=True, rebuild_if_missing=True):
        resolved = _resolve_families(prop)
        known = _get_known_families(self)

        # If the user explicitly configured CJK fonts in a generic family list
        # (e.g. font.family=sans-serif + font.sans-serif=[WenQuanYi, Noto, DejaVu]),
        # matplotlib's scoring can still pick DejaVu Sans. Prefer the first
        # available CJK family directly so Chinese text actually renders.
        missing_requested_cjk = False
        for family in resolved:
            if not _is_cjk_family(family):
                continue

            if family.lower() not in known:
                missing_requested_cjk = True

            concrete = _resolve_concrete_family(
                self,
                family,
                known,
                fontext=fontext,
                directory=directory,
                fallback_to_default=fallback_to_default,
                rebuild_if_missing=rebuild_if_missing,
                require_cjk_match=True,
            )
            if concrete:
                return concrete

        # Phase 1: CJK fallback injection (must run BEFORE fc-match).
        # Handles: plt.style.use('seaborn-v0_8') overwrites font.sans-serif
        # to ['Arial', 'Liberation Sans', ...] — all Western, no CJK.
        # If we let fc-match run first, it resolves 'Arial' → Liberation Sans
        # and returns immediately, never reaching this check.
        if missing_requested_cjk or not _has_cjk_font(resolved):
            for cjk in _CJK_FALLBACK_FAMILIES:
                if cjk.lower() in known:
                    from matplotlib.font_manager import FontProperties
                    return _original(
                        self, FontProperties(family=[cjk]),
                        fontext=fontext,
                        directory=directory,
                        fallback_to_default=fallback_to_default,
                        rebuild_if_missing=rebuild_if_missing,
                    )

        # Phase 2: fc-match for fonts not in matplotlib's cache.
        # Handles: rcParams['font.sans-serif'] = ['SimHei'] where SimHei
        # is not installed but fontconfig can alias it to Noto Sans CJK SC.
        for family in resolved:
            if family.lower() not in known:
                concrete = _resolve_concrete_family(
                    self,
                    family,
                    known,
                    fontext=fontext,
                    directory=directory,
                    fallback_to_default=fallback_to_default,
                    rebuild_if_missing=rebuild_if_missing,
                )
                if concrete:
                    return concrete

        # All requested fonts are in cache — use original scoring.
        return _original(
            self, prop,
            fontext=fontext,
            directory=directory,
            fallback_to_default=fallback_to_default,
            rebuild_if_missing=rebuild_if_missing,
        )

    _patched._aio_mpl_fontconfig_patched = True
    FontManager.findfont = _patched

    # Critical: also update the module-level shortcut.
    # font_manager.py line 1644 does `findfont = fontManager.findfont` which
    # captures a bound method to the ORIGINAL findfont. Replacing the class
    # attribute above does NOT update that reference. Rendering code (e.g.
    # _mathtext.py) imports this module-level `findfont` and would bypass
    # our patch without this line.
    if hasattr(fm_module, "fontManager"):
        fm_module.findfont = fm_module.fontManager.findfont
    fm_module._aio_mpl_fontconfig_patched = True


def _install():
    """Register a one-shot import hook for matplotlib.font_manager."""

    existing = sys.modules.get("matplotlib.font_manager")
    if existing is not None:
        _patch_findfont(existing)
        return

    class _Hook(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        _active = True
        _original_loader = None

        def find_spec(self, fullname, path=None, target=None):
            if fullname != "matplotlib.font_manager" or not self._active:
                return None

            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None or spec.loader is None:
                return None

            self._original_loader = spec.loader
            spec.loader = self
            return spec

        def create_module(self, spec):
            if (
                self._original_loader is not None
                and hasattr(self._original_loader, "create_module")
            ):
                return self._original_loader.create_module(spec)
            return None

        def exec_module(self, module):
            self.__class__._active = False
            sys.meta_path[:] = [h for h in sys.meta_path if h is not self]

            if self._original_loader is None:
                raise ImportError("Original loader missing for matplotlib.font_manager")

            if hasattr(self._original_loader, "exec_module"):
                self._original_loader.exec_module(module)
            else:
                loaded = self._original_loader.load_module(module.__name__)
                module.__dict__.update(loaded.__dict__)

            _patch_findfont(module)

    sys.meta_path.insert(0, _Hook())


_install()
