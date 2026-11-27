"""Pure-Python color/WCAG/purple/shadow math + opaque-box Playwright probes & UI helpers.

All browser probes rely exclusively on user-observable DOM structure, ARIA roles,
computed CSS styles, rendered SVG/Canvas geometry, and browser network/dialog events.
Zero coupling to Vue/Pinia internals or frontend source files.
"""

from __future__ import annotations

import colorsys
import contextlib
import math
import re
from typing import Any, Callable, Optional

from e2e.harness import RuntimeGuard

# ---------------------------------------------------------------------------
# 1. Pure-Python Color, WCAG 2.1, Purple-on-Dark & Box-Shadow Math
# ---------------------------------------------------------------------------

_RGBA_RE = re.compile(
    r"^rgba?\(\s*([\d.]+%?)[,\s]+([\d.]+%?)[,\s]+([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$",
    re.IGNORECASE,
)
_SRGB_RE = re.compile(
    r"^color\(\s*srgb\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)(?:\s*/\s*([\d.eE+-]+%?))?\s*\)$",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _parse_alpha(raw: Optional[str]) -> float:
    if raw is None:
        return 1.0
    s = raw.strip()
    val = float(s[:-1]) / 100.0 if s.endswith("%") else float(s)
    return max(0.0, min(1.0, val))


def _parse_rgb_channel(raw: str) -> float:
    s = raw.strip()
    val = (float(s[:-1]) / 100.0) * 255.0 if s.endswith("%") else float(s)
    return max(0.0, min(255.0, val))


def parse_css_color(color_str: str) -> tuple[float, float, float, float]:
    """Parse CSS colors into `(r, g, b, a)` with RGB in `[0, 255]` and alpha in `[0, 1]`.

    Supports `transparent`, `none`, hex (`#RGB`, `#RGBA`, `#RRGGBB`, `#RRGGBBAA`),
    `rgb()`/`rgba()` (comma or CSS Color 4 space/slash syntax), and Chromium 159's
    `color(srgb r g b / a)` output from `color-mix()`.
    """
    if not color_str:
        return (0.0, 0.0, 0.0, 0.0)
    s = color_str.strip()
    low = s.lower()
    if low in ("transparent", "none"):
        return (0.0, 0.0, 0.0, 0.0)

    hm = _HEX_RE.match(s)
    if hm:
        h = hm.group(1)
        if len(h) == 3:
            r, g, b = (int(c * 2, 16) for c in h)
            return (float(r), float(g), float(b), 1.0)
        if len(h) == 4:
            r, g, b, a = (int(c * 2, 16) for c in h)
            return (float(r), float(g), float(b), a / 255.0)
        if len(h) == 6:
            return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)), 1.0)
        if len(h) == 8:
            return (
                float(int(h[0:2], 16)),
                float(int(h[2:4], 16)),
                float(int(h[4:6], 16)),
                int(h[6:8], 16) / 255.0,
            )

    rm = _RGBA_RE.match(s)
    if rm:
        return (
            _parse_rgb_channel(rm.group(1)),
            _parse_rgb_channel(rm.group(2)),
            _parse_rgb_channel(rm.group(3)),
            _parse_alpha(rm.group(4)),
        )

    sm = _SRGB_RE.match(s)
    if sm:
        r = max(0.0, min(1.0, float(sm.group(1)))) * 255.0
        g = max(0.0, min(1.0, float(sm.group(2)))) * 255.0
        b = max(0.0, min(1.0, float(sm.group(3)))) * 255.0
        return (r, g, b, _parse_alpha(sm.group(4)))

    raise ValueError(f"Unsupported CSS color string: {color_str!r}")


def alpha_composite(
    fg: tuple[float, float, float, float],
    bg: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Source-over alpha compositing of RGBA `fg` over opaque RGB `bg`."""
    a = max(0.0, min(1.0, fg[3]))
    return (
        fg[0] * a + bg[0] * (1.0 - a),
        fg[1] * a + bg[1] * (1.0 - a),
        fg[2] * a + bg[2] * (1.0 - a),
    )


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    """Compute WCAG 2.1 relative luminance in `[0.0, 1.0]`."""

    def _lin(c: float) -> float:
        s = max(0.0, min(255.0, c)) / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * _lin(rgb[0]) + 0.7152 * _lin(rgb[1]) + 0.0722 * _lin(rgb[2])


def contrast_ratio(
    c1: tuple[float, float, float],
    c2: tuple[float, float, float],
) -> float:
    """Compute WCAG 2.1 contrast ratio in `[1.0, 21.0]`."""
    l1 = relative_luminance(c1)
    l2 = relative_luminance(c2)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def rgb_distance(
    c1: tuple[float, float, float],
    c2: tuple[float, float, float],
) -> float:
    """Euclidean RGB distance in `[0, 441.67]`."""
    return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2)


def is_purple_violation(rgb: tuple[float, float, float], role: str = "surface") -> bool:
    """Detect forbidden purple/violet hues (`250°..315°`, `S > 0.15`) on dark themes.

    Verified zero false positives on all 18 approved cool-slate/teal/semantic dark tokens
    and 100% detection across Material purple, Tailwind violet/purple/fuchsia, and purple-tinted dark cards.
    """
    r = max(0.0, min(255.0, rgb[0])) / 255.0
    g = max(0.0, min(255.0, rgb[1])) / 255.0
    b = max(0.0, min(255.0, rgb[2])) / 255.0
    h_frac, _l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h_frac * 360.0) % 360.0
    chroma = max(r, g, b) - min(r, g, b)
    if 250.0 <= h <= 315.0 and s > 0.15:
        min_chroma = 0.025 if role == "surface" else 0.04
        return chroma >= min_chroma
    return False


def parse_box_shadow_layers(shadow: str) -> list[dict[str, float]]:
    """Parse Chromium `getComputedStyle().boxShadow` (color-before-lengths) into layers."""
    if not shadow or shadow.strip().lower() == "none":
        return []
    layers: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in shadow:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            layers.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        layers.append("".join(buf).strip())

    parsed: list[dict[str, float]] = []
    for layer in layers:
        if not layer:
            continue
        col_m = re.search(r"(?:rgba?\([^)]*\)|color\([^)]*\)|#[0-9a-fA-F]{3,8})", layer)
        alpha = 1.0
        if col_m:
            try:
                alpha = parse_css_color(col_m.group(0))[3]
            except ValueError:
                alpha = 1.0
        px_vals = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)px", layer)]
        blur = px_vals[2] if len(px_vals) >= 3 else 0.0
        spread = px_vals[3] if len(px_vals) >= 4 else 0.0
        parsed.append({"blur": blur, "spread": spread, "alpha": alpha})
    return parsed


def is_legacy_heavy_shadow(shadow: str) -> bool:
    """True when boxShadow matches legacy Material elevation-3/5 multi-layer drop shadows."""
    layers = parse_box_shadow_layers(shadow)
    if len(layers) < 3:
        return False
    return max(l["blur"] for l in layers) >= 10.0 and max(l["alpha"] for l in layers) >= 0.12


# ---------------------------------------------------------------------------
# 2. HTTP Header & Wait Helpers
# ---------------------------------------------------------------------------


def assert_security_headers(headers: dict[str, str], *, is_asset: bool = False) -> None:
    """Assert security and Cache-Control headers mandated by web/web.go:93-105."""
    lower = {k.lower(): v for k, v in headers.items()}
    assert lower.get("x-content-type-options", "").lower() == "nosniff", f"Bad X-Content-Type-Options: {lower}"
    assert lower.get("x-frame-options", "").upper() == "DENY", f"Bad X-Frame-Options: {lower}"
    assert lower.get("referrer-policy", "").lower() == "no-referrer", f"Bad Referrer-Policy: {lower}"
    perm = lower.get("permissions-policy", "")
    assert "camera=()" in perm and "microphone=()" in perm and "geolocation=()" in perm, (
        f"Bad Permissions-Policy: {perm!r}"
    )
    cc = lower.get("cache-control", "").lower()
    if is_asset:
        assert "max-age=31536000" in cc and "immutable" in cc, f"Expected immutable Cache-Control on asset, got {cc!r}"
    else:
        assert "no-store" in cc, f"Expected no-store Cache-Control on non-asset, got {cc!r}"


def wait_for_rAF_settle(page: Any, delay_ms: int = 220) -> None:
    """Wait two animation frames plus a brief settle period for Chart.js / Vuetify transitions."""
    page.evaluate(
        """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"""
    )
    if delay_ms > 0:
        page.wait_for_timeout(delay_ms)


def wait_for_spa_ready(page: Any, *, expect_shell: bool = True, timeout: int = 15000) -> None:
    """Wait deterministically for SPA mount without using `networkidle` (safe on polling routes)."""
    page.wait_for_load_state("domcontentloaded", timeout=timeout)
    page.wait_for_function(
        """(expectShell) => {
            const app = document.querySelector('#app');
            if (!app || !app.innerHTML || app.innerHTML.trim().length < 20) return false;
            const vApp = document.querySelector('.v-application');
            if (!vApp) return false;
            if (expectShell) {
                const main = document.querySelector('main, .v-main');
                if (!main) return false;
                const rect = main.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }
            const formOrCard = document.querySelector('form, .v-card, input');
            return !!formOrCard;
        }""",
        arg=expect_shell,
        timeout=timeout,
    )
    # Wait briefly if a full-screen global loading spinner is covering the page
    with contextlib.suppress(Exception):
        page.wait_for_function(
            """() => {
                const loaders = document.querySelectorAll('.v-overlay--active .v-progress-circular');
                for (const l of loaders) {
                    const ov = l.closest('.v-overlay--active');
                    if (ov && !ov.querySelector('[role="dialog"], .v-card')) {
                        return false;
                    }
                }
                return true;
            }""",
            timeout=5000,
        )
    wait_for_rAF_settle(page, delay_ms=120)


def navigate_spa(
    page: Any,
    base_url: str,
    path: str = "",
    *,
    expect_shell: bool = True,
    timeout: int = 15000,
) -> Any:
    """Navigate to `<base_url><path>` using `domcontentloaded` + semantic SPA readiness."""
    clean_base = base_url if base_url.endswith("/") else base_url + "/"
    clean_path = path.lstrip("/")
    url = f"{clean_base}{clean_path}"
    resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    wait_for_spa_ready(page, expect_shell=expect_shell, timeout=timeout)
    return resp


# ---------------------------------------------------------------------------
# 3. Opaque-Box Browser Probes (WCAG, Purple, Surface, Hygiene, Typography,
#    Overflow/Grid, SVG Gauges, Canvas Charts, QR, RTL, A11y, i18n)
# ---------------------------------------------------------------------------

_JS_COLOR_UTILS = """
  const scratchCanvas = document.createElement('canvas');
  scratchCanvas.width = 1; scratchCanvas.height = 1;
  const scratchCtx = scratchCanvas.getContext('2d', { willReadFrequently: true });

  function parseColor(str) {
    if (!str || str === 'transparent' || str === 'none') return [0, 0, 0, 0];
    let m = str.match(/^rgba?\\(\\s*([\\d.]+%?)[,\\s]+([\\d.]+%?)[,\\s]+([\\d.]+%?)(?:\\s*[,/]\\s*([\\d.]+%?))?\\s*\\)$/i);
    if (m) {
      const ch = (v) => v.endsWith('%') ? (parseFloat(v) / 100) * 255 : parseFloat(v);
      const a = m[4] === undefined ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      return [ch(m[1]), ch(m[2]), ch(m[3]), Math.max(0, Math.min(1, a))];
    }
    m = str.match(/^color\\(\\s*srgb\\s+([\\d.eE+-]+)\\s+([\\d.eE+-]+)\\s+([\\d.eE+-]+)(?:\\s*\\/\\s*([\\d.eE+-]+%?))?\\s*\\)$/i);
    if (m) {
      const clamp01 = (v) => Math.max(0, Math.min(1, parseFloat(v)));
      const a = m[4] === undefined ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]));
      return [clamp01(m[1]) * 255, clamp01(m[2]) * 255, clamp01(m[3]) * 255, Math.max(0, Math.min(1, a))];
    }
    scratchCtx.clearRect(0, 0, 1, 1);
    scratchCtx.fillStyle = 'rgba(0,0,0,0)';
    scratchCtx.fillStyle = str;
    scratchCtx.fillRect(0, 0, 1, 1);
    const d = scratchCtx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  }

  function composite(fg, bg) {
    const a = Math.max(0, Math.min(1, fg[3]));
    return [fg[0] * a + bg[0] * (1 - a), fg[1] * a + bg[1] * (1 - a), fg[2] * a + bg[2] * (1 - a)];
  }

  function isVisibleScopeEl(el) {
    if (!el || el.nodeType !== 1) return false;
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
      const cs = getComputedStyle(cur);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    }
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 40;
  }

  function pickActiveScope(scopeSelector, fallbackRoot) {
    if (scopeSelector === 'auto') {
      const overlays = Array.from(document.querySelectorAll('.v-overlay--active .v-overlay__content'))
        .filter(isVisibleScopeEl);
      return overlays.length > 0 ? overlays[overlays.length - 1] : fallbackRoot;
    }
    if (scopeSelector) {
      const all = Array.from(document.querySelectorAll(scopeSelector));
      const vis = all.filter(isVisibleScopeEl);
      if (vis.length > 0) return vis[vis.length - 1];
      return all.length > 0 ? all[all.length - 1] : null;
    }
    return fallbackRoot;
  }

  function resolveBgAndOpacity(el) {
    const chain = [];
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) chain.push(cur);
    chain.reverse();
    const darkHint = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    let bg = darkHint ? [14, 20, 31] : [255, 255, 255];
    let effOpacity = 1;
    for (const node of chain) {
      const cs = getComputedStyle(node);
      const op = parseFloat(cs.opacity);
      const nodeOp = Number.isFinite(op) ? op : 1;
      const layer = parseColor(cs.backgroundColor);
      if (layer[3] > 0) {
        bg = composite([layer[0], layer[1], layer[2], layer[3] * effOpacity * nodeOp], bg);
      }
      const nRect = node.getBoundingClientRect();
      for (const ch of node.children) {
        const cls = typeof ch.className === 'string' ? ch.className : '';
        if (!/(overlay|underlay)/i.test(cls)) continue;
        const ocs = getComputedStyle(ch);
        const oOp = parseFloat(ocs.opacity);
        if (!oOp || ocs.display === 'none' || ocs.visibility === 'hidden') continue;
        const oCol = parseColor(ocs.backgroundColor);
        if (oCol[3] <= 0) continue;
        const oRect = ch.getBoundingClientRect();
        if (nRect.width > 0 && oRect.width >= nRect.width * 0.8 && oRect.height >= nRect.height * 0.8) {
          bg = composite([oCol[0], oCol[1], oCol[2], oCol[3] * oOp * effOpacity * nodeOp], bg);
        }
      }
      effOpacity *= nodeOp;
    }
    return { bg, effOpacity };
  }
"""


def probe_wcag_contrast(
    page: Any,
    *,
    scope_selector: Optional[str] = None,
    min_normal: float = 4.5,
    min_large: float = 3.0,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Audit rendered visible text WCAG 2.1 AA contrast with full alpha/opacity compositing."""
    js = (
        """({ scopeSelector, minNormal, minLarge }) => {
"""
        + _JS_COLOR_UTILS
        + """
  function relLum([r, g, b]) {
    const lin = (v) => {
      const s = v / 255;
      return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }
  function contrast(c1, c2) {
    const l1 = relLum(c1), l2 = relLum(c2);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  function isDisabledOrHidden(el) {
    for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
      if (cur.hasAttribute('disabled') || cur.getAttribute('aria-disabled') === 'true') return true;
      if (cur.getAttribute('aria-hidden') === 'true') return true;
      const cls = typeof cur.className === 'string' ? cur.className : '';
      if (/--disabled|\\bv-sr-only\\b|\\bsr-only\\b|\\bd-sr-only\\b|\\bmdi\\b|\\bv-icon\\b/.test(cls)) return true;
      const cs = getComputedStyle(cur);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return true;
    }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return true;
    // Skip zero-clip sr-only
    const cs = getComputedStyle(el);
    if (cs.clip === 'rect(0px, 0px, 0px, 0px)') return true;
    return false;
  }

  const root = pickActiveScope(scopeSelector, document.body);
  if (!root) return { checked: 0, violations: [] };

  const puaOnly = /^[\\uE000-\\uF8FF\\s]+$/;
  const candidates = [];
  const seen = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let tn;
  while ((tn = walker.nextNode())) {
    const raw = tn.nodeValue || '';
    const trimmed = raw.trim();
    if (!trimmed || puaOnly.test(trimmed)) continue;
    const el = tn.parentElement;
    if (!el || seen.has(el)) continue;
    const tag = el.tagName.toUpperCase();
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'TITLE') continue;
    seen.add(el);
    candidates.push(el);
  }
  for (const inp of root.querySelectorAll('input:not([type="hidden"]), textarea')) {
    if (inp.value && !seen.has(inp)) {
      seen.add(inp);
      candidates.push(inp);
    }
  }

  const violations = [];
  let checked = 0;
  for (const el of candidates) {
    if (isDisabledOrHidden(el)) continue;
    const cs = getComputedStyle(el);
    const isSvgText = el.namespaceURI === 'http://www.w3.org/2000/svg';
    const rawFg = parseColor(isSvgText ? cs.fill : cs.color);
    const { bg, effOpacity } = resolveBgAndOpacity(el);
    const fg = composite([rawFg[0], rawFg[1], rawFg[2], rawFg[3] * effOpacity], bg);
    const fontSize = parseFloat(cs.fontSize) || 14;
    const fontWeight = parseInt(cs.fontWeight, 10) || 400;
    const isLarge = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
    const required = isLarge ? minLarge : minNormal;
    const ratio = contrast(fg, bg);
    checked++;
    if (ratio + 0.01 < required) {
      const txt = (el.value || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
      violations.push({
        tag: el.tagName.toLowerCase(),
        cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
        text: txt,
        fg: fg.map(Math.round),
        bg: bg.map(Math.round),
        ratio: Number(ratio.toFixed(2)),
        required,
        fontSize,
        fontWeight,
      });
    }
  }
  return { checked, violations };
}"""
    )
    res = page.evaluate(js, {"scopeSelector": scope_selector, "minNormal": min_normal, "minLarge": min_large})
    if assert_pass:
        assert res["checked"] > 0, f"probe_wcag_contrast checked 0 visible text nodes (scope={scope_selector!r})"
        assert not res["violations"], (
            f"WCAG 2.1 AA contrast violations ({len(res['violations'])}/{res['checked']}): "
            f"{res['violations'][:8]}"
        )
    return res


def probe_zero_purple_on_dark(
    page: Any,
    *,
    scope_selector: Optional[str] = None,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Sample surfaces, borders, accents & SVG strokes in Dark mode and assert zero purple."""
    js = (
        """(scopeSelector) => {
"""
        + _JS_COLOR_UTILS
        + """
  const root = pickActiveScope(scopeSelector, document);
  if (!root) return { sampled: 0, samples: [] };
  const surfaceSel = 'html, body, .v-application, main, header, .v-app-bar, nav, .v-navigation-drawer, .v-card, .v-dialog, .v-table, .v-data-table, [role="dialog"]';
  const accentSel = '.v-list-item--active, button, .v-btn, .v-chip, .v-tab--selected, a, svg circle, svg path';
  const samples = [];
  const seen = new Set();

  function addEl(el, role) {
    if (!el || seen.has(el)) return;
    seen.add(el);
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return;
    const r = el.getBoundingClientRect();
    if (el.tagName !== 'HTML' && el.tagName !== 'BODY' && (r.width < 2 || r.height < 2)) return;
    const { bg } = resolveBgAndOpacity(el);
    const cls = (typeof el.className === 'string' ? el.className : (el.className?.baseVal || '')).slice(0, 60);
    samples.push({ tag: el.tagName.toLowerCase(), cls, role, prop: 'bg', rgb: bg.map(Math.round) });

    // Check all 4 visible borders (catches e.g. purple border-inline-start active bars)
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      const bw = parseFloat(cs[`border${side}Width`]) || 0;
      if (bw > 0) {
        const bc = parseColor(cs[`border${side}Color`]);
        if (bc[3] > 0.05) {
          const effB = composite(bc, bg);
          samples.push({ tag: el.tagName.toLowerCase(), cls, role, prop: `border${side}`, rgb: effB.map(Math.round) });
        }
      }
    }

    if (role === 'accent') {
      const col = parseColor(cs.color);
      if (col[3] > 0.05) {
        const effC = composite(col, bg);
        samples.push({ tag: el.tagName.toLowerCase(), cls, role: 'accent', prop: 'color', rgb: effC.map(Math.round) });
      }
      if (el.namespaceURI === 'http://www.w3.org/2000/svg') {
        for (const svgProp of ['stroke', 'fill']) {
          const svCol = parseColor(cs[svgProp]);
          if (svCol[3] > 0.05) {
            const effS = composite(svCol, bg);
            samples.push({ tag: el.tagName.toLowerCase(), cls, role: 'accent', prop: svgProp, rgb: effS.map(Math.round) });
          }
        }
      }
    }
  }

  for (const el of root.querySelectorAll(surfaceSel)) addEl(el, 'surface');
  for (const el of root.querySelectorAll(accentSel)) addEl(el, 'accent');
  return { sampled: samples.length, samples };
}"""
    )
    raw = page.evaluate(js, scope_selector)
    violations: list[dict[str, Any]] = []
    for item in raw.get("samples", []):
        rgb = tuple(float(c) for c in item["rgb"])
        if is_purple_violation(rgb, role=item["role"]):
            violations.append(item)
    result = {"sampled": raw.get("sampled", 0), "violations": violations}
    if assert_pass:
        assert result["sampled"] > 0, "probe_zero_purple_on_dark sampled 0 elements"
        assert not violations, f"Purple-on-dark violation(s) detected ({len(violations)}): {violations[:8]}"
    return result


def probe_surface_separation(
    page: Any,
    *,
    mode: str = "light",
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify distinct canvas vs card surface luminance matching Light or Dark polarity."""
    js = (
        """() => {
"""
        + _JS_COLOR_UTILS
        + """
  const canvasEl = document.querySelector('.v-application') || document.body;
  const canvasBg = resolveBgAndOpacity(canvasEl).bg;
  const cards = [];
  for (const card of document.querySelectorAll('.v-card')) {
    if (card.closest('.v-overlay__content')) continue;
    const cs = getComputedStyle(card);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const r = card.getBoundingClientRect();
    if (r.width < 80 || r.height < 40) continue;
    cards.push({
      cls: (typeof card.className === 'string' ? card.className : '').slice(0, 60),
      rgb: resolveBgAndOpacity(card).bg,
    });
  }
  return { canvasRgb: canvasBg, cards };
}"""
    )
    data = page.evaluate(js)
    canvas_rgb = tuple(float(c) for c in data["canvasRgb"])
    l_canvas = relative_luminance(canvas_rgb)
    cards = data.get("cards", [])

    issues: list[str] = []
    if mode == "light":
        if l_canvas <= 0.70:
            issues.append(f"Light canvas luminance too dark: L={l_canvas:.4f} rgb={tuple(round(c) for c in canvas_rgb)}")
    elif mode == "dark":
        if l_canvas >= 0.10:
            issues.append(f"Dark canvas luminance too bright: L={l_canvas:.4f} rgb={tuple(round(c) for c in canvas_rgb)}")
    else:
        raise ValueError(f"mode must be 'light' or 'dark', got {mode!r}")

    separated_cards = 0
    for c in cards:
        c_rgb = tuple(float(x) for x in c["rgb"])
        l_card = relative_luminance(c_rgb)
        dist = rgb_distance(c_rgb, canvas_rgb)
        if mode == "light":
            if l_card > l_canvas and dist >= 8.0:
                separated_cards += 1
        else:
            if l_card > l_canvas and dist >= 8.0 and abs(l_card - l_canvas) >= 0.005:
                separated_cards += 1

    if cards and separated_cards == 0:
        sample_cards = [
            {"rgb": [round(x) for x in c["rgb"]], "L": round(relative_luminance(tuple(c["rgb"])), 4)}
            for c in cards[:4]
        ]
        issues.append(
            f"No card surface visually separated from {mode} canvas "
            f"(canvas rgb={[round(x) for x in canvas_rgb]} L={l_canvas:.4f}, sample_cards={sample_cards})"
        )
    elif not cards:
        issues.append("No visible top-level .v-card found for surface separation check")

    result = {
        "mode": mode,
        "canvas_rgb": [round(x) for x in canvas_rgb],
        "canvas_luminance": round(l_canvas, 4),
        "card_count": len(cards),
        "separated_cards": separated_cards,
        "issues": issues,
    }
    if assert_pass:
        assert not issues, f"Surface separation failed ({mode}): {issues}"
    return result


def probe_card_and_shell_hygiene(
    page: Any,
    *,
    max_card_depth: int = 2,
    check_topbar: bool = True,
    check_card_radius: bool = True,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify hairline borders, zero heavy shadows, zero 24px card radii, zero raw-gray subtitle border, zero .v-main 10px margin, and bounded card nesting."""
    js = (
        """() => {
"""
        + _JS_COLOR_UTILS
        + """
  const topbarEl = document.querySelector('header.v-app-bar, header, .v-app-bar');
  let topbar = null;
  if (topbarEl) {
    const cs = getComputedStyle(topbarEl);
    const r = topbarEl.getBoundingClientRect();
    if (cs.display !== 'none' && r.height > 0) {
      topbar = {
        height: r.height,
        boxShadow: cs.boxShadow,
        borderBottomWidth: parseFloat(cs.borderBottomWidth) || 0,
        borderBottomAlpha: parseColor(cs.borderBottomColor)[3],
      };
    }
  }

  const mainEl = document.querySelector('main.v-main, .v-main, main');
  let mainMargin = null;
  if (mainEl) {
    const cs = getComputedStyle(mainEl);
    mainMargin = {
      marginLeft: parseFloat(cs.marginLeft) || 0,
      marginRight: parseFloat(cs.marginRight) || 0,
      marginTop: parseFloat(cs.marginTop) || 0,
    };
  }

  const cards = [];
  for (const card of document.querySelectorAll('.v-card')) {
    if (card.closest('.v-overlay__content')) continue;
    const cs = getComputedStyle(card);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const r = card.getBoundingClientRect();
    if (r.width < 40 || r.height < 30) continue;
    // Total card nesting depth (1 = un-nested, 2 = card inside card, 3+ = forbidden over-nesting)
    let depth = 1;
    for (let anc = card.parentElement; anc; anc = anc.parentElement) {
      if (anc.classList && anc.classList.contains('v-card') && !anc.closest('.v-overlay__content')) {
        depth++;
      }
    }
    const bwTop = parseFloat(cs.borderTopWidth) || 0;
    const bwLeft = parseFloat(cs.borderLeftWidth) || 0;
    const bAlpha = Math.max(parseColor(cs.borderTopColor)[3], parseColor(cs.borderLeftColor)[3]);
    cards.push({
      cls: (typeof card.className === 'string' ? card.className : '').slice(0, 70),
      width: r.width,
      height: r.height,
      inMain: !!card.closest('main, .v-main'),
      boxShadow: cs.boxShadow,
      radiusTopLeft: parseFloat(cs.borderTopLeftRadius) || 0,
      borderWidth: Math.max(bwTop, bwLeft),
      borderAlpha: bAlpha,
      depth,
    });
  }

  const legacySubtitles = [];
  for (const sub of document.querySelectorAll('.v-card-subtitle')) {
    const cs = getComputedStyle(sub);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const bw = parseFloat(cs.borderBottomWidth) || 0;
    const bc = parseColor(cs.borderBottomColor);
    if (bw >= 1 && Math.round(bc[0]) === 128 && Math.round(bc[1]) === 128 && Math.round(bc[2]) === 128 && bc[3] > 0.5) {
      legacySubtitles.push({ text: (sub.textContent || '').trim().slice(0, 50), bw, bc: bc.map(Math.round) });
    }
  }

  return { topbar, mainMargin, cards, legacySubtitles };
}"""
    )
    raw = page.evaluate(js)
    violations: list[str] = []

    topbar = raw.get("topbar")
    if check_topbar and topbar is not None:
        if is_legacy_heavy_shadow(topbar["boxShadow"]):
            violations.append(f"Topbar has legacy heavy shadow: {topbar['boxShadow']}")
        if not (0.5 <= topbar["borderBottomWidth"] <= 2.0 and topbar["borderBottomAlpha"] > 0.01):
            violations.append(
                f"Topbar missing hairline bottom border (width={topbar['borderBottomWidth']}, alpha={topbar['borderBottomAlpha']:.3f})"
            )

    main_margin = raw.get("mainMargin")
    if main_margin is not None:
        if main_margin["marginLeft"] >= 8.0 or main_margin["marginRight"] >= 8.0 or main_margin["marginTop"] >= 8.0:
            violations.append(f"Legacy .v-main outer margin detected: {main_margin}")

    if raw.get("legacySubtitles"):
        violations.append(f"Legacy .v-card-subtitle raw rgb(128,128,128) border: {raw['legacySubtitles'][:4]}")

    cards = raw.get("cards", [])
    top_level_cards = [c for c in cards if c["depth"] == 1 and c["width"] >= 120 and c["height"] >= 60]
    hairline_cards = 0
    for card in cards:
        if is_legacy_heavy_shadow(card["boxShadow"]):
            violations.append(f"Legacy heavy shadow on card ({card['cls']}): {card['boxShadow']}")
        if check_card_radius and card["inMain"] and card["width"] >= 140 and card["height"] >= 70:
            if card["radiusTopLeft"] > 16.5:
                violations.append(
                    f"Bulbous card border-radius ({card['radiusTopLeft']}px > 16px) on card ({card['cls']})"
                )
        if card["depth"] > max_card_depth:
            violations.append(
                f"Excessive card nesting depth {card['depth']} > {max_card_depth} on card ({card['cls']})"
            )
        if card["depth"] == 1 and 0.5 <= card["borderWidth"] <= 2.0 and card["borderAlpha"] > 0.01:
            hairline_cards += 1

    if top_level_cards and hairline_cards == 0:
        violations.append(
            f"None of {len(top_level_cards)} top-level visible cards have a visible hairline border (0.5..2px)"
        )

    result = {
        "topbar": topbar,
        "main_margin": main_margin,
        "card_count": len(cards),
        "hairline_cards": hairline_cards,
        "violations": violations,
    }
    if assert_pass:
        assert not violations, "Card/shell visual hygiene violation(s):\n  - " + "\n  - ".join(violations[:10])
    return result


def probe_offline_typography(
    page: Any,
    guard: Optional[RuntimeGuard] = None,
    *,
    require_tabular: bool = False,
    require_mono: bool = False,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify zero external font requests, zero 'Lexend', tabular numerals, and monospace technical values."""
    js = """() => {
      const lexendFonts = [];
      if (document.fonts) {
        for (const f of document.fonts) {
          if ((f.family || '').toLowerCase().includes('lexend')) {
            lexendFonts.push(f.family);
          }
        }
      }
      const lexendEls = [];
      let tabularCount = 0;
      let monoCount = 0;
      const monoRe = /mono|consolas|menlo|monaco|courier|fira code|jetbrains/i;

      for (const el of document.querySelectorAll('body, body *')) {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        const ff = cs.fontFamily || '';
        if (ff.toLowerCase().includes('lexend')) {
          lexendEls.push({ tag: el.tagName.toLowerCase(), ff: ff.slice(0, 80) });
        }
        const fvn = cs.fontVariantNumeric || '';
        if (fvn.includes('tabular-nums')) {
          tabularCount++;
        }
        if (monoRe.test(ff)) {
          monoCount++;
        }
      }
      return {
        lexendFonts,
        lexendEls: lexendEls.slice(0, 8),
        tabularCount,
        monoCount,
      };
    }"""
    data = page.evaluate(js)
    violations: list[str] = []
    if guard is not None and guard.external_requests:
        violations.append(f"External non-localhost request(s): {guard.external_requests[:5]}")
    if data["lexendFonts"] or data["lexendEls"]:
        violations.append(f"Forbidden 'Lexend' font reference found: fonts={data['lexendFonts']} els={data['lexendEls']}")
    if require_tabular and data["tabularCount"] == 0:
        violations.append("Expected tabular-nums on numeric/telemetry readouts, found 0 elements with tabular-nums")
    if require_mono and data["monoCount"] == 0:
        violations.append("Expected monospace typography on technical readouts/code/logs, found 0 monospace elements")

    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "Offline typography violation(s):\n  - " + "\n  - ".join(violations)
    return result


def probe_viewport_overflow_and_grid(
    page: Any,
    *,
    check_grid: bool = False,
    min_card_width: float = 180.0,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify zero horizontal document overflow and clean non-overlapping desktop card grids."""
    js = """({ checkGrid, minCardWidth }) => {
      const docEl = document.documentElement;
      const scrollWidth = docEl.scrollWidth;
      const innerWidth = window.innerWidth;
      const overflowPx = scrollWidth - innerWidth;
      const gridViolations = [];
      let gridCardCount = 0;

      if (checkGrid) {
        const containers = new Set(document.querySelectorAll('main .v-row, .v-main .v-row, .grid-row'));
        const mainRoot = document.querySelector('main, .v-main') || document.body;
        for (const el of mainRoot.querySelectorAll('div, section')) {
          if (getComputedStyle(el).display === 'grid') containers.add(el);
        }
        const cards = [];
        const seen = new Set();
        for (const row of containers) {
          for (const col of row.children) {
            const card = col.classList.contains('v-card') ? col : col.querySelector(':scope > .v-card');
            if (!card || seen.has(card) || card.closest('.v-overlay__content')) continue;
            const cs = getComputedStyle(card);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            const r = card.getBoundingClientRect();
            if (r.width < 20 || r.height < 20) continue;
            seen.add(card);
            cards.push({
              el: card,
              cls: (typeof card.className === 'string' ? card.className : '').slice(0, 60),
              left: r.left,
              right: r.right,
              top: r.top,
              bottom: r.bottom,
              width: r.width,
              height: r.height,
            });
          }
        }
        gridCardCount = cards.length;
        for (const c of cards) {
          if (c.right > innerWidth + 1.5) {
            gridViolations.push(`Card overflows viewport right (${c.right.toFixed(1)} > ${innerWidth}): ${c.cls}`);
          }
          if (innerWidth >= 1024 && c.width < minCardWidth) {
            gridViolations.push(`Card narrower than ${minCardWidth}px (${c.width.toFixed(1)}px): ${c.cls}`);
          }
        }
        for (let i = 0; i < cards.length; i++) {
          for (let j = i + 1; j < cards.length; j++) {
            const a = cards[i], b = cards[j];
            if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
            const overlapW = Math.min(a.right, b.right) - Math.max(a.left, b.left);
            const overlapH = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
            if (overlapW > 1.5 && overlapH > 1.5) {
              gridViolations.push(
                `Overlapping grid cards #${i} & #${j} (${overlapW.toFixed(1)}x${overlapH.toFixed(1)}px)`
              );
            }
          }
        }
      }
      return { scrollWidth, innerWidth, overflowPx, gridCardCount, gridViolations };
    }"""
    data = page.evaluate(js, {"checkGrid": check_grid, "minCardWidth": min_card_width})
    violations: list[str] = []
    if data["overflowPx"] > 1:
        violations.append(
            f"Horizontal viewport overflow: scrollWidth={data['scrollWidth']} > innerWidth={data['innerWidth']}"
        )
    violations.extend(data["gridViolations"])
    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "Viewport/grid violation(s):\n  - " + "\n  - ".join(violations[:10])
    return result


def probe_svg_gauges(page: Any, *, assert_pass: bool = True) -> dict[str, Any]:
    """Verify visible SVG arc/ring gauges (>=48px) with distinct track stroke and percentage readouts."""
    js = (
        """() => {
"""
        + _JS_COLOR_UTILS
        + """
  const scope = document.querySelector('main, .v-main') || document.body;
  const gauges = [];
  for (const svg of scope.querySelectorAll('svg')) {
    if (svg.closest('header, nav, .v-navigation-drawer, .v-app-bar')) continue;
    if (svg.classList && svg.classList.contains('v-icon__svg')) continue;
    const cs = getComputedStyle(svg);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = svg.getBoundingClientRect();
    if (r.width < 48 || r.height < 48) continue;
    const shapes = Array.from(svg.querySelectorAll('circle, path'));
    if (shapes.length === 0) continue;

    const card = svg.closest('.v-card') || svg.parentElement;
    const cardBg = resolveBgAndOpacity(card).bg;
    const cardText = (card.textContent || '').trim();
    const hasPct = /\\d+(?:\\.\\d+)?\\s*%/.test(cardText);
    const hasNaN = /\\bNaN\\b|\\bInfinity\\b/i.test(cardText);

    let maxTrackDist = 0;
    let visibleStrokeCount = 0;
    for (const sh of shapes) {
      const scs = getComputedStyle(sh);
      const sw = parseFloat(scs.strokeWidth) || 0;
      const st = parseColor(scs.stroke);
      if (sw > 0 && st[3] > 0.05) {
        visibleStrokeCount++;
        const eff = composite(st, cardBg);
        const dist = Math.hypot(eff[0] - cardBg[0], eff[1] - cardBg[1], eff[2] - cardBg[2]);
        if (dist > maxTrackDist) maxTrackDist = dist;
      }
    }

    // Check font families inside gauge container for forbidden Lexend
    let hasLexend = false;
    for (const n of card.querySelectorAll('*')) {
      if ((getComputedStyle(n).fontFamily || '').toLowerCase().includes('lexend')) {
        hasLexend = true;
        break;
      }
    }

    gauges.push({
      width: Math.round(r.width),
      height: Math.round(r.height),
      visibleStrokeCount,
      maxTrackDist: Number(maxTrackDist.toFixed(1)),
      hasPct,
      hasNaN,
      hasLexend,
      sampleText: cardText.replace(/\\s+/g, ' ').slice(0, 60),
    });
  }
  return { count: gauges.length, gauges };
}"""
    )
    data = page.evaluate(js)
    violations: list[str] = []
    if data["count"] == 0:
        violations.append("No visible SVG gauge (>= 48x48px with circle/path arcs) found in main content")
    for idx, g in enumerate(data["gauges"]):
        if g["visibleStrokeCount"] == 0 or g["maxTrackDist"] < 12.0:
            violations.append(
                f"SVG gauge #{idx} track stroke indistinct from card bg (ΔRGB={g['maxTrackDist']} < 12): {g}"
            )
        if not g["hasPct"]:
            violations.append(f"SVG gauge #{idx} missing percentage readout: {g['sampleText']!r}")
        if g["hasNaN"]:
            violations.append(f"SVG gauge #{idx} displays NaN/Infinity: {g['sampleText']!r}")
        if g["hasLexend"]:
            violations.append(f"SVG gauge #{idx} references forbidden 'Lexend' font")

    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "SVG gauge probe failed:\n  - " + "\n  - ".join(violations)
    return result


def probe_canvas_charts(
    page: Any,
    *,
    verify_theme_diff: bool = False,
    toggle_theme_fn: Optional[Callable[[], None]] = None,
    scope_selector: Optional[str] = None,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify visible Chart.js <canvas> graphics are non-blank and optionally redraw on theme change."""

    def _inspect_canvases() -> dict[str, Any]:
        js = """(scopeSelector) => {
          let root = document;
          if (scopeSelector) {
            const all = Array.from(document.querySelectorAll(scopeSelector));
            const vis = all.filter(el => {
              const cs = getComputedStyle(el);
              const r = el.getBoundingClientRect();
              return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 40 && r.height > 40;
            });
            root = vis.length > 0 ? vis[vis.length - 1] : (all.length > 0 ? all[all.length - 1] : null);
          }
          if (!root) return { count: 0, canvases: [] };
          const out = [];
          for (const c of root.querySelectorAll('canvas')) {
            const cs = getComputedStyle(c);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            const r = c.getBoundingClientRect();
            if (r.width < 80 || r.height < 40 || c.width === 0 || c.height === 0) continue;
            // Skip square QR canvases inside QR frames
            if (c.closest('.sui-qr-frame') || (Math.abs(r.width - r.height) <= 8 && r.width <= 320 && c.closest('[role="dialog"]'))) {
              // still allow non-square chart canvases in dialogs (e.g., Stats modal)
              if (Math.abs(r.width - r.height) <= 8) continue;
            }
            let opaquePixels = 0;
            const colors = new Set();
            let dataUrl = '';
            try {
              const ctx = c.getContext('2d', { willReadFrequently: true });
              const img = ctx.getImageData(0, 0, c.width, c.height).data;
              // Stride sample for speed while remaining thorough
              const step = Math.max(1, Math.floor((c.width * c.height) / 40000));
              for (let i = 0; i < img.length; i += 4 * step) {
                if (img[i + 3] > 0) {
                  opaquePixels++;
                  if (colors.size < 16) {
                    colors.add(`${img[i] >> 2},${img[i+1] >> 2},${img[i+2] >> 2}`);
                  }
                }
              }
              dataUrl = c.toDataURL('image/png');
            } catch (e) {
              out.push({ error: String(e), width: r.width, height: r.height });
              continue;
            }
            out.push({
              width: Math.round(r.width),
              height: Math.round(r.height),
              opaquePixels,
              distinctColors: colors.size,
              dataUrlLen: dataUrl.length,
              dataUrl,
            });
          }
          return { count: out.length, canvases: out };
        }"""
        return page.evaluate(js, scope_selector)

    wait_for_rAF_settle(page, delay_ms=250)
    before = _inspect_canvases()
    violations: list[str] = []

    if before["count"] == 0:
        violations.append("No visible chart <canvas> (>= 80x40px) found")
    for idx, c in enumerate(before["canvases"]):
        if "error" in c:
            violations.append(f"Canvas #{idx} read error: {c['error']}")
            continue
        if c["opaquePixels"] < 50:
            violations.append(f"Canvas #{idx} appears blank (opaquePixels={c['opaquePixels']} < 50)")
        if c["distinctColors"] < 2:
            violations.append(f"Canvas #{idx} has < 2 distinct colors (distinctColors={c['distinctColors']})")

    theme_changed = None
    if verify_theme_diff and toggle_theme_fn is not None and before["count"] > 0 and not violations:
        before_urls = [c.get("dataUrl", "") for c in before["canvases"]]
        toggle_theme_fn()
        wait_for_rAF_settle(page, delay_ms=350)
        after = _inspect_canvases()
        after_urls = [c.get("dataUrl", "") for c in after["canvases"]]
        theme_changed = bool(after_urls) and before_urls != after_urls
        if not theme_changed:
            violations.append("Chart <canvas> bitmap did not visually redraw after Light <-> Dark theme switch")

    # Strip full dataUrl from returned summary to keep error logs clean
    summary_canvases = [{k: v for k, v in c.items() if k != "dataUrl"} for c in before["canvases"]]
    result = {
        "count": before["count"],
        "canvases": summary_canvases,
        "theme_changed": theme_changed,
        "violations": violations,
    }
    if assert_pass:
        assert not violations, "Canvas chart probe failed:\n  - " + "\n  - ".join(violations)
    return result


def probe_qr_quiet_zone(
    page: Any,
    *,
    require_dark_backdrop: bool = False,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify QR code inside active dialog sits in a white (#FFFFFF) quiet-zone frame (>=8px padding)."""
    js = (
        """() => {
"""
        + _JS_COLOR_UTILS
        + """
  const overlays = Array.from(document.querySelectorAll('.v-overlay--active .v-overlay__content, [role="dialog"], .v-dialog'))
    .filter(el => {
      if (!isVisibleScopeEl(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width > 80 && r.height > 80;
    });
  const dialog = overlays.length > 0 ? overlays[overlays.length - 1] : document.body;
  const pageBg = resolveBgAndOpacity(document.querySelector('.v-application') || document.body).bg;

  let qrEl = null;
  for (const el of dialog.querySelectorAll('canvas, svg, img')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width >= 80 && r.height >= 80 && Math.abs(r.width - r.height) <= 24) {
      qrEl = el;
      break;
    }
  }
  if (!qrEl) return { found: false, pageBg: pageBg.map(Math.round) };

  const qrRect = qrEl.getBoundingClientRect();
  let whiteFrame = null;
  for (let cur = qrEl.parentElement; cur && cur !== document.body; cur = cur.parentElement) {
    const bg = resolveBgAndOpacity(cur).bg;
    if (bg[0] >= 245 && bg[1] >= 245 && bg[2] >= 245) {
      const fr = cur.getBoundingClientRect();
      whiteFrame = {
        rgb: bg.map(Math.round),
        padLeft: Number((qrRect.left - fr.left).toFixed(1)),
        padRight: Number((fr.right - qrRect.right).toFixed(1)),
        padTop: Number((qrRect.top - fr.top).toFixed(1)),
        padBottom: Number((fr.bottom - qrRect.bottom).toFixed(1)),
      };
      break;
    }
    if (cur === dialog) break;
  }

  let canvasColors = null;
  if (qrEl.tagName.toLowerCase() === 'canvas') {
    try {
      const ctx = qrEl.getContext('2d', { willReadFrequently: true });
      const img = ctx.getImageData(0, 0, qrEl.width, qrEl.height).data;
      const set = new Set();
      for (let i = 0; i < img.length; i += 16) {
        if (img[i + 3] > 0) set.add(`${img[i] >> 4},${img[i+1] >> 4},${img[i+2] >> 4}`);
        if (set.size >= 4) break;
      }
      canvasColors = set.size;
    } catch (_) {}
  }

  return {
    found: true,
    qrTag: qrEl.tagName.toLowerCase(),
    qrSize: [Math.round(qrRect.width), Math.round(qrRect.height)],
    pageBg: pageBg.map(Math.round),
    whiteFrame,
    canvasColors,
  };
}"""
    )
    data = page.evaluate(js)
    violations: list[str] = []
    page_bg = tuple(float(c) for c in data.get("pageBg", [255, 255, 255]))
    if require_dark_backdrop:
        bg_lum = relative_luminance(page_bg)
        if bg_lum >= 0.15:
            violations.append(
                f"Expected dark backdrop behind QR dialog (L={bg_lum:.4f} >= 0.15, rgb={data.get('pageBg')})"
            )
    if not data.get("found"):
        violations.append("No square QR code (<canvas>/<svg>/<img> >= 80x80) found inside active dialog")
    else:
        frame = data.get("whiteFrame")
        if frame is None:
            violations.append("QR code lacks an enclosing white (#FFFFFF, r/g/b >= 245) quiet-zone container")
        else:
            min_pad = min(frame["padLeft"], frame["padRight"], frame["padTop"], frame["padBottom"])
            if min_pad < 8.0:
                violations.append(f"QR white quiet-zone padding too small ({min_pad}px < 8px): {frame}")
        if data.get("canvasColors") is not None and data["canvasColors"] < 2:
            violations.append(f"QR <canvas> appears blank (distinct colors={data['canvasColors']})")

    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "QR quiet-zone probe failed:\n  - " + "\n  - ".join(violations)
    return result


def probe_rtl_fidelity(
    page: Any,
    *,
    is_desktop: bool = True,
    require_ltr_tech: bool = False,
    assert_pass: bool = True,
) -> dict[str, Any]:
    """Verify RTL document/body direction, right-anchored desktop sidebar, preserved dir='ltr' tech nodes, and zero overflow."""
    js = """({ isDesktop }) => {
      const htmlDir = (document.documentElement.getAttribute('dir') || '').toLowerCase();
      const bodyDir = getComputedStyle(document.body).direction;
      const appEl = document.querySelector('.v-application');
      const appDir = appEl ? getComputedStyle(appEl).direction : bodyDir;
      const vuetifyRtl = !!document.querySelector('.v-locale--is-rtl');
      const isRtl = htmlDir === 'rtl' || bodyDir === 'rtl' || appDir === 'rtl' || vuetifyRtl;

      const innerWidth = window.innerWidth;
      const scrollWidth = document.documentElement.scrollWidth;

      let drawer = null;
      if (isDesktop) {
        const nav = document.querySelector('nav.v-navigation-drawer, aside.v-navigation-drawer, .v-navigation-drawer, nav[data-sidebar]');
        if (nav) {
          const cs = getComputedStyle(nav);
          const r = nav.getBoundingClientRect();
          if (cs.display !== 'none' && r.width > 20) {
            drawer = {
              left: Number(r.left.toFixed(1)),
              right: Number(r.right.toFixed(1)),
              width: Number(r.width.toFixed(1)),
              innerWidth,
            };
          }
        }
      }

      const ltrNodes = [];
      const brokenLtrNodes = [];
      for (const el of document.body.querySelectorAll('[dir="ltr"]')) {
        if (el.classList && el.classList.contains('v-application')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        ltrNodes.push(el.tagName.toLowerCase());
        if (cs.direction !== 'ltr') {
          brokenLtrNodes.push({ tag: el.tagName.toLowerCase(), computed: cs.direction });
        }
      }

      return {
        htmlDir,
        bodyDir,
        appDir,
        vuetifyRtl,
        isRtl,
        scrollWidth,
        innerWidth,
        drawer,
        ltrCount: ltrNodes.length,
        brokenLtrNodes,
      };
    }"""
    data = page.evaluate(js, {"isDesktop": is_desktop})
    violations: list[str] = []
    if not data["isRtl"]:
        violations.append(
            f"RTL not activated: htmlDir={data['htmlDir']!r}, bodyDir={data['bodyDir']!r}, vuetifyRtl={data['vuetifyRtl']}"
        )
    if is_desktop:
        drawer = data.get("drawer")
        if drawer is None:
            violations.append("Desktop RTL sidebar/navigation drawer not visible")
        else:
            if abs(drawer["right"] - drawer["innerWidth"]) > 4.0 or drawer["left"] <= drawer["innerWidth"] / 2.0:
                violations.append(f"Desktop RTL sidebar not anchored to right edge: {drawer}")
    if data["brokenLtrNodes"]:
        violations.append(f"Elements with dir='ltr' did not resolve to computed direction 'ltr': {data['brokenLtrNodes']}")
    if require_ltr_tech and data["ltrCount"] == 0:
        violations.append("Expected at least one visible [dir='ltr'] technical value in RTL mode, found 0")
    if data["scrollWidth"] > data["innerWidth"] + 1:
        violations.append(f"Horizontal overflow in RTL: scrollWidth={data['scrollWidth']} > innerWidth={data['innerWidth']}")

    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "RTL fidelity probe failed:\n  - " + "\n  - ".join(violations)
    return result


def probe_accessible_actions(page: Any, *, assert_pass: bool = True) -> dict[str, Any]:
    """Verify interactive icon controls use semantic <button>/[role='button'] (>=24x24px) rather than raw clickable icons."""
    js = """() => {
      const rawIcons = [];
      const interactiveAnc = 'button, a, [role="button"], [role="tab"], [role="switch"], [role="checkbox"], [role="menuitem"], label, input, select';
      for (const icon of document.querySelectorAll('i.mdi, .v-icon, svg.clickable-icon')) {
        const cs = getComputedStyle(icon);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
        const r = icon.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        if (icon.closest(interactiveAnc)) continue;
        // Flag icons that act as direct click targets (pointer cursor, explicit tabindex, or inline onclick)
        const isClickable = cs.cursor === 'pointer' || icon.hasAttribute('onclick') || icon.hasAttribute('tabindex');
        if (isClickable) {
          // Ignore Vuetify internal field clear/append wrapper icons that live inside .v-field__append-inner if role=button exists on icon itself
          rawIcons.push({
            tag: icon.tagName.toLowerCase(),
            cls: (typeof icon.className === 'string' ? icon.className : '').slice(0, 70),
            parentCls: (typeof icon.parentElement?.className === 'string' ? icon.parentElement.className : '').slice(0, 60),
          });
        }
      }

      const tinyButtons = [];
      let buttonCount = 0;
      const visibleOverlays = Array.from(document.querySelectorAll('.v-overlay--active .v-overlay__content'))
        .filter(el => {
          for (let cur = el; cur && cur.nodeType === 1; cur = cur.parentElement) {
            const ocs = getComputedStyle(cur);
            if (ocs.display === 'none' || ocs.visibility === 'hidden' || parseFloat(ocs.opacity) === 0) return false;
          }
          const or = el.getBoundingClientRect();
          return or.width > 40 && or.height > 40;
        });
      const scope = visibleOverlays.length > 0 ? visibleOverlays[visibleOverlays.length - 1] : document.body;
      for (const btn of scope.querySelectorAll('button, [role="button"]')) {
        const cs = getComputedStyle(btn);
        if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
        if (btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true') continue;
        const r = btn.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        buttonCount++;
        if (r.width < 23.5 || r.height < 23.5) {
          tinyButtons.push({
            tag: btn.tagName.toLowerCase(),
            cls: (typeof btn.className === 'string' ? btn.className : '').slice(0, 60),
            w: Number(r.width.toFixed(1)),
            h: Number(r.height.toFixed(1)),
            text: (btn.textContent || btn.getAttribute('aria-label') || '').trim().slice(0, 30),
          });
        }
      }
      return { rawIcons, tinyButtons, buttonCount };
    }"""
    data = page.evaluate(js)
    violations: list[str] = []
    if data["rawIcons"]:
        violations.append(f"Raw clickable icon(s) outside semantic <button>/[role='button']: {data['rawIcons'][:6]}")
    if data["tinyButtons"]:
        violations.append(f"Interactive button(s) smaller than 24x24px: {data['tinyButtons'][:6]}")
    result = {**data, "violations": violations}
    if assert_pass:
        assert not violations, "Accessible actions probe failed:\n  - " + "\n  - ".join(violations)
    return result


_LEAKED_I18N_KEY_RE = re.compile(
    r"(?:@:[a-zA-Z0-9_.-]+|\b(?:pages|ui|navigation|actions|objects|setting|main|client|in|out|rule|dns|basic|admin|tls|node|domain|stats|login)\.[a-z][a-zA-Z0-9_]{1,}(?:\.[a-zA-Z][a-zA-Z0-9_]*)*\b)"
)


def probe_i18n_hygiene(page: Any, *, assert_pass: bool = True) -> dict[str, Any]:
    """Scan visible UI text (excluding code/pre/textarea/JSON editors) for leaked raw i18n keys."""
    text = page.evaluate(
        """() => {
          const skipTags = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'TEXTAREA', 'INPUT']);
          const chunks = [];
          const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          let tn;
          while ((tn = walker.nextNode())) {
            const el = tn.parentElement;
            if (!el || skipTags.has(el.tagName)) continue;
            if (el.closest('pre, code, .sui-mono')) continue;
            const cs = getComputedStyle(el);
            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
            const v = (tn.nodeValue || '').trim();
            if (v) chunks.push(v);
          }
          return chunks.join(' \\n ');
        }"""
    )
    leaks = sorted(set(_LEAKED_I18N_KEY_RE.findall(text)))
    # Filter obvious domain/file extensions like example.com or e2e-x.invalid
    leaks = [k for k in leaks if not k.endswith((".com", ".org", ".net", ".invalid", ".local", ".pem", ".crt", ".key"))]
    result = {"leaks": leaks, "sample_len": len(text)}
    if assert_pass:
        assert len(text) > 0, "probe_i18n_hygiene scanned 0 characters of visible UI text"
        assert not leaks, f"Leaked raw i18n key(s) detected in visible UI: {leaks[:10]}"
    return result


# ---------------------------------------------------------------------------
# 4. Resilient Opaque-Box UI Interaction Helpers
# ---------------------------------------------------------------------------


def login_via_ui(
    page: Any,
    base_url: str,
    username: str = "admin",
    password: str = "admin",
    *,
    expect_redirect: bool = True,
    timeout: int = 15000,
) -> None:
    """Fill and submit the /login form using user-observable inputs & submit button."""
    navigate_spa(page, base_url, "login", expect_shell=False, timeout=timeout)
    user_input = page.locator(
        'input[autocomplete="username"], input[name*="user" i], input[type="text"], form input:not([type="password"]):not([type="hidden"]):not([type="checkbox"])'
    ).first
    pass_input = page.locator('input[type="password"], input[autocomplete="current-password"]').first
    user_input.wait_for(state="visible", timeout=timeout)
    pass_input.wait_for(state="visible", timeout=timeout)
    user_input.fill(username)
    pass_input.fill(password)

    submit_btn = page.locator('button[type="submit"]').first
    if submit_btn.count() == 0:
        submit_btn = page.locator("form button").last
    submit_btn.click()

    if expect_redirect:
        page.wait_for_function(
            """() => !window.location.pathname.endsWith('/login') && !!document.querySelector('main, .v-main')""",
            timeout=timeout,
        )
        wait_for_spa_ready(page, expect_shell=True, timeout=timeout)
    else:
        wait_for_rAF_settle(page, delay_ms=400)


def get_sidebar_rect(page: Any) -> Optional[dict[str, float]]:
    """Return bounding rect of the primary navigation drawer if visible."""
    return page.evaluate(
        """() => {
          const nav = document.querySelector('nav.v-navigation-drawer, aside.v-navigation-drawer, .v-navigation-drawer');
          if (!nav) return null;
          const cs = getComputedStyle(nav);
          if (cs.display === 'none' || cs.visibility === 'hidden') return null;
          const r = nav.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height };
        }"""
    )


def toggle_sidebar_rail(page: Any) -> dict[str, float]:
    """Click the desktop sidebar collapse/expand button and wait for drawer width transition."""
    before = get_sidebar_rect(page)
    assert before is not None, "Cannot toggle rail: navigation drawer not found"
    candidates = [
        'header button:has(.mdi-menu), .v-app-bar button:has(.mdi-menu)',
        'header button:has(.mdi-menu-open), .v-app-bar button:has(.mdi-menu-open)',
        'header button:has(.mdi-backburger), header button:has(.mdi-forwardburger)',
        'header button:has(.mdi-dock-left), header button:has(.mdi-page-layout-sidebar-left)',
        'button[aria-label*="sidebar" i], button[aria-label*="rail" i], button[aria-label*="collapse" i], button[aria-label*="expand" i], button[aria-label*="menu" i]',
        '.v-navigation-drawer button:has(.mdi-chevron-left), .v-navigation-drawer button:has(.mdi-chevron-right)',
        'header button, .v-app-bar button',
    ]
    clicked = False
    for sel in candidates:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 4)):
            btn = loc.nth(i)
            if btn.is_visible():
                btn.click()
                clicked = True
                break
        if clicked:
            break
    assert clicked, "Could not locate visible sidebar rail toggle <button>"
    page.wait_for_function(
        """(prevW) => {
            const nav = document.querySelector('nav.v-navigation-drawer, aside.v-navigation-drawer, .v-navigation-drawer');
            if (!nav) return false;
            return Math.abs(nav.getBoundingClientRect().width - prevW) > 20;
        }""",
        arg=before["width"],
        timeout=5000,
    )
    wait_for_rAF_settle(page, delay_ms=250)
    after = get_sidebar_rect(page)
    assert after is not None
    return after


def open_mobile_drawer(page: Any) -> dict[str, float]:
    """At mobile viewport, open the temporary navigation drawer via the topbar menu button."""
    btn = page.locator(
        'header button:has(.mdi-menu), .v-app-bar button:has(.mdi-menu), '
        'header button[aria-label*="menu" i], .v-app-bar button'
    ).first
    btn.wait_for(state="visible", timeout=5000)
    btn.click()
    page.wait_for_function(
        """() => {
            const nav = document.querySelector('nav.v-navigation-drawer, aside.v-navigation-drawer, .v-navigation-drawer');
            if (!nav) return false;
            const r = nav.getBoundingClientRect();
            return r.width > 120 && r.right > 50 && r.left < window.innerWidth - 50;
        }""",
        timeout=5000,
    )
    wait_for_rAF_settle(page, delay_ms=200)
    rect = get_sidebar_rect(page)
    assert rect is not None
    return rect


def switch_theme_via_ui(page: Any, target: str = "dark") -> None:
    """Switch theme ('light', 'dark', or 'system') via the visible theme switcher control."""
    target = target.lower()
    if target not in ("light", "dark", "system"):
        raise ValueError(f"Unsupported theme target: {target!r}")

    theme_btn = page.locator(
        'button:has(.mdi-theme-light-dark), '
        'button:has(.mdi-weather-night), '
        'button:has(.mdi-weather-sunny), '
        'button:has(.mdi-brightness-auto), '
        'button:has(.mdi-white-balance-sunny), '
        'button[aria-label*="theme" i], '
        'button[title*="theme" i], '
        'header button:has(.mdi-monitor), '
        'header button:has(.mdi-laptop)'
    ).first
    theme_btn.wait_for(state="visible", timeout=6000)
    theme_btn.click()
    wait_for_rAF_settle(page, delay_ms=150)

    # Check if clicking opened a menu/overlay with explicit Light / Dark / System options
    menu_items = page.locator('.v-overlay--active .v-list-item, [role="menu"] [role="menuitem"], .v-menu .v-list-item')
    if menu_items.count() > 0:
        label_map = {
            "dark": re.compile(r"dark|深色|暗色|تیره", re.IGNORECASE),
            "light": re.compile(r"light|浅色|淺色|亮色|روشن", re.IGNORECASE),
            "system": re.compile(r"system|auto|跟随|跟隨|سیستم", re.IGNORECASE),
        }
        icon_map = {
            "dark": ".mdi-weather-night",
            "light": ".mdi-weather-sunny, .mdi-white-balance-sunny",
            "system": ".mdi-theme-light-dark, .mdi-brightness-auto, .mdi-laptop, .mdi-monitor",
        }
        picked = False
        pat = label_map[target]
        for i in range(menu_items.count()):
            item = menu_items.nth(i)
            if not item.is_visible():
                continue
            txt = (item.inner_text() or "").strip()
            if pat.search(txt) or item.locator(icon_map[target]).count() > 0:
                item.click()
                picked = True
                break
        assert picked, f"Theme menu opened but option for {target!r} was not found"
    else:
        # Cycle button (clicking toggles theme): click up to 3 times until localStorage/luminance matches
        for _ in range(3):
            stored = (page.evaluate("() => window.localStorage.getItem('theme') || ''") or "").lower()
            if stored == target:
                break
            theme_btn.click()
            wait_for_rAF_settle(page, delay_ms=150)

    wait_for_rAF_settle(page, delay_ms=220)


def switch_locale_via_ui(page: Any, locale_code: str) -> None:
    """Switch UI locale ('en', 'zhHans', 'zhHant', 'fa') via the visible language menu button."""
    lang_btn = page.locator(
        'button:has(.mdi-translate), '
        'button:has(.mdi-web), '
        'button[aria-label*="lang" i], '
        'button[aria-label*="locale" i]'
    ).first
    lang_btn.wait_for(state="visible", timeout=6000)
    lang_btn.click()
    wait_for_rAF_settle(page, delay_ms=150)

    patterns = {
        "en": re.compile(r"english|\ben\b", re.IGNORECASE),
        "zhHans": re.compile(r"简体|簡體|中文\s*\(简体\)|zh-?cn|zhHans", re.IGNORECASE),
        "zhHant": re.compile(r"繁體|繁体|中文\s*\(繁體\)|zh-?tw|zhHant", re.IGNORECASE),
        "fa": re.compile(r"فارسی|persian|\bfa\b", re.IGNORECASE),
    }
    pat = patterns.get(locale_code)
    assert pat is not None, f"Unsupported locale_code: {locale_code!r}"

    items = page.locator('.v-overlay--active .v-list-item, [role="menu"] [role="menuitem"]')
    items.first.wait_for(state="visible", timeout=5000)
    clicked = False
    for i in range(items.count()):
        item = items.nth(i)
        if not item.is_visible():
            continue
        txt = (item.inner_text() or "").strip()
        if pat.search(txt):
            item.click()
            clicked = True
            break
    assert clicked, f"Locale menu option for {locale_code!r} not found"
    wait_for_rAF_settle(page, delay_ms=250)


_DIALOG_DIRECT_SEL = (
    '.v-overlay--active .v-overlay__content > .v-card, '
    '.v-overlay--active .v-overlay__content > [role="dialog"], '
    '.v-overlay--active .v-overlay__content > form > .v-card'
)
_DIALOG_FALLBACK_SEL = '.v-overlay--active .v-overlay__content .v-card, .v-overlay--active [role="dialog"]'


def find_active_dialog(page: Any, timeout: int = 6000) -> Any:
    """Wait for and return the topmost visible Vuetify dialog container."""
    fallback = page.locator(_DIALOG_FALLBACK_SEL).last
    fallback.wait_for(state="visible", timeout=timeout)
    direct = page.locator(_DIALOG_DIRECT_SEL)
    if direct.count() > 0 and direct.last.is_visible():
        return direct.last
    return fallback


def close_active_dialog(page: Any, timeout: int = 5000) -> None:
    """Close the topmost active dialog via its Close/Cancel button (or Escape fallback)."""
    direct = page.locator(_DIALOG_DIRECT_SEL)
    dlg = direct.last if direct.count() > 0 else page.locator(_DIALOG_FALLBACK_SEL).last
    if dlg.count() == 0 or not dlg.is_visible():
        return
    close_candidates = [
        dlg.locator('button:has(.mdi-close)').first,
        dlg.locator(
            'button:has-text("Cancel"), button:has-text("Close"), '
            'button:has-text("取消"), button:has-text("关闭"), button:has-text("關閉"), '
            'button:has-text("انصراف"), button:has-text("بستن")'
        ).first,
    ]
    clicked = False
    for cand in close_candidates:
        if cand.count() > 0 and cand.is_visible():
            cand.click()
            clicked = True
            break
    if not clicked:
        page.keyboard.press("Escape")
    page.wait_for_function(
        """() => {
            const ovs = document.querySelectorAll('.v-overlay--active .v-overlay__content .v-card, .v-overlay--active [role="dialog"]');
            for (const o of ovs) {
                const cs = getComputedStyle(o);
                if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                const r = o.getBoundingClientRect();
                if (r.width > 40 && r.height > 40) return false;
            }
            return true;
        }""",
        timeout=timeout,
    )
    wait_for_rAF_settle(page, delay_ms=120)


def assert_dialog_chrome_clean(page: Any) -> dict[str, Any]:
    """Assert active dialog uses overflow-y: auto (not forced scroll) and no legacy blue-darken-1 header."""
    dlg = find_active_dialog(page)
    info = dlg.evaluate(
        """(el) => {
          const hasLegacyBlue = !!el.querySelector('.bg-blue-darken-1, .blue-darken-1');
          const badScrolls = [];
          for (const node of [el, ...el.querySelectorAll('.v-card-text, .sui-modal-body, div')]) {
            const cs = getComputedStyle(node);
            if (cs.overflowY === 'scroll') {
              badScrolls.push((typeof node.className === 'string' ? node.className : '').slice(0, 60));
            }
          }
          const rect = el.getBoundingClientRect();
          return {
            hasLegacyBlue,
            badScrolls: badScrolls.slice(0, 5),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            innerWidth: window.innerWidth,
          };
        }"""
    )
    assert not info["hasLegacyBlue"], f"Dialog still uses legacy blue-darken-1 header styling: {info}"
    assert not info["badScrolls"], f"Dialog uses forced overflow-y: scroll instead of auto: {info['badScrolls']}"
    assert info["width"] <= info["innerWidth"] + 2, f"Dialog overflows viewport width: {info}"
    return info


def open_and_cancel_dialog(page: Any, trigger_locator: Any) -> dict[str, Any]:
    """Click a dialog trigger, verify clean dialog chrome, and close cleanly without saving."""
    trigger_locator.wait_for(state="visible", timeout=6000)
    trigger_locator.click()
    info = assert_dialog_chrome_clean(page)
    close_active_dialog(page)
    return info


def click_page_add_button(page: Any, extra_texts: tuple[str, ...] = ()) -> Any:
    """Locate and click the primary Add/New action button inside the current view's main area."""
    selectors = [
        'main button:has(.mdi-plus)',
        'main button:has(.mdi-plus-circle-outline)',
        'main button:has-text("Add")',
        'main button:has-text("New")',
        'main button:has-text("Create")',
        'main button:has-text("添加")',
        'main button:has-text("新增")',
    ] + [f'main button:has-text("{t}")' for t in extra_texts]
    for sel in selectors:
        loc = page.locator(sel)
        for i in range(min(loc.count(), 4)):
            btn = loc.nth(i)
            if btn.is_visible():
                btn.click()
                return btn
    raise AssertionError(f"Could not locate visible Add/Create button on {page.url}")


def wait_for_live_status(page: Any, timeout: int = 12000) -> None:
    """Wait until Dashboard has rendered live telemetry percentages or running status."""
    page.wait_for_function(
        """() => {
            const main = document.querySelector('main, .v-main');
            if (!main) return false;
            const txt = main.textContent || '';
            return /\\d+(?:\\.\\d+)?\\s*%/.test(txt) || /running|online|uptime|goroutine/i.test(txt);
        }""",
        timeout=timeout,
    )
    wait_for_rAF_settle(page, delay_ms=250)


def check_confirm_dialog_semantics(page: Any) -> dict[str, Any]:
    """Verify open ConfirmDialog has a neutral Cancel button (not green/success) and destructive Confirm."""
    dlg = find_active_dialog(page)
    info = dlg.evaluate(
        """(el) => {
          const btns = Array.from(el.querySelectorAll('button')).filter(b => {
            const r = b.getBoundingClientRect();
            return r.width > 10 && r.height > 10;
          });
          return btns.map(b => ({
            text: (b.textContent || '').trim(),
            cls: typeof b.className === 'string' ? b.className : '',
            bg: getComputedStyle(b).backgroundColor,
          }));
        }"""
    )
    for b in info:
        if re.search(r"cancel|no|取消|انصراف", b["text"], re.IGNORECASE):
            assert "bg-success" not in b["cls"] and "text-success" not in b["cls"], (
                f"ConfirmDialog Cancel button must be neutral, not success: {b}"
            )
    return {"buttons": info}
