"""Self-verification unittest suite for the S-UI Nexus E2E harness & probes (--check).

Runnable via:
    python3 e2e/run_e2e.py --check
    python3 -m e2e.run_e2e --check
    python3 -m unittest discover -s e2e -p 'test_*.py' -v

Verifies:
  1. All module imports, zero forbidden third-party deps (no pytest / numpy)
  2. CLI argument parsing and clean error exits
  3. Strict quantitative tier & feature-area thresholds (N=14, T1>=70, T2>=70, T3>=14, T4>=7)
  4. AST non-vacuity audit confirming zero `assert True` / empty stub E2E tests
  5. Pure-Python WCAG, color, purple-on-dark, shadow, and port allocation math
  6. Live headless Chromium probes against ephemeral in-process HTTP server serving BOTH
     `/pass.html` (all probes PASS) and `/fail.html` (every probe genuinely FAILS on its defect)
"""

from __future__ import annotations

import ast
import contextlib
import http.server
import io
import socketserver
import sys
import threading
import unittest
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from e2e import (  # noqa: E402
    FEATURE_AREAS,
    MIN_TIER1_PER_AREA,
    MIN_TIER1_TOTAL,
    MIN_TIER2_PER_AREA,
    MIN_TIER2_TOTAL,
    MIN_TIER3_TOTAL,
    MIN_TIER4_TOTAL,
    N_AREAS,
)
from e2e.harness import (  # noqa: E402
    E2EEnv,
    REGISTRY,
    RuntimeGuard,
    SuiApiClient,
    TestRegistry,
    allocate_distinct_free_ports,
    launch_chromium,
)
from e2e.probes import (  # noqa: E402
    alpha_composite,
    close_active_dialog,
    contrast_ratio,
    is_legacy_heavy_shadow,
    is_purple_violation,
    parse_box_shadow_layers,
    parse_css_color,
    probe_accessible_actions,
    probe_canvas_charts,
    probe_card_and_shell_hygiene,
    probe_i18n_hygiene,
    probe_offline_typography,
    probe_qr_quiet_zone,
    probe_rtl_fidelity,
    probe_surface_separation,
    probe_svg_gauges,
    probe_viewport_overflow_and_grid,
    probe_wcag_contrast,
    probe_zero_purple_on_dark,
    relative_luminance,
)
from e2e.run_e2e import load_all_tiers, main as run_e2e_main, parse_args  # noqa: E402

# ---------------------------------------------------------------------------
# Self-Contained Live Chromium PASS & FAIL Fixtures (used ONLY by --check)
# ---------------------------------------------------------------------------

_PASS_HTML = """<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8" />
  <title>S-UI Nexus Pass Fixture</title>
  <style>
    :root {
      --bg: #F4F6F9;
      --surface: #FFFFFF;
      --fg: #0F172A;
      --primary: #0F766E;
      --on-primary: #FFFFFF;
      --border: rgba(15, 23, 42, 0.12);
      --track: #CBD5E1;
    }
    html.dark {
      --bg: #0E141F;
      --surface: #151E2E;
      --fg: #E2E8F0;
      --primary: #2DD4BF;
      --on-primary: #042F2E;
      --border: rgba(203, 213, 225, 0.14);
      --track: #243044;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--fg);
    }
    .v-application { background: var(--bg); min-height: 100vh; }
    header.v-app-bar {
      height: 56px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 16px;
      box-shadow: none;
    }
    nav.v-navigation-drawer {
      position: fixed;
      top: 56px;
      left: 0;
      width: 240px;
      bottom: 0;
      background: var(--surface);
      border-right: 1px solid var(--border);
      padding: 12px;
    }
    html[dir="rtl"] nav.v-navigation-drawer {
      left: auto;
      right: 0;
      border-right: none;
      border-left: 1px solid var(--border);
    }
    main.v-main {
      margin: 0;
      padding: 16px 16px 16px 256px;
    }
    html[dir="rtl"] main.v-main {
      padding: 16px 256px 16px 16px;
    }
    .v-row {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
    }
    .v-col { flex: 1 1 260px; }
    .v-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .pill-mix {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      background: color-mix(in srgb, var(--primary) 14%, transparent);
      color: var(--fg);
    }
    .sui-num { font-variant-numeric: tabular-nums; font-weight: 600; }
    .sui-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    button {
      min-width: 32px;
      min-height: 32px;
      padding: 6px 12px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--primary);
      color: var(--on-primary);
      cursor: pointer;
    }
    .v-overlay {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.5);
      z-index: 1000;
    }
    .v-overlay.v-overlay--active { display: flex; }
    .sui-qr-frame {
      background: #FFFFFF;
      padding: 14px;
      border-radius: 12px;
      display: inline-flex;
    }
  </style>
</head>
<body>
  <div class="v-application">
    <header class="v-app-bar">
      <strong>S-UI Nexus</strong>
      <button id="toggle-theme" type="button">Theme</button>
      <button id="toggle-rtl" type="button">RTL</button>
      <button id="open-qr" type="button">QR</button>
    </header>
    <nav class="v-navigation-drawer">
      <a href="#dash" style="color: var(--fg);">Dashboard</a>
    </nav>
    <main class="v-main">
      <div class="v-row">
        <div class="v-col">
          <div class="v-card">
            <div class="pill-mix">Running</div>
            <p class="sui-num">42.5%</p>
            <svg width="72" height="72" viewBox="0 0 72 72">
              <circle cx="36" cy="36" r="28" fill="none" stroke="var(--track)" stroke-width="6"></circle>
              <circle cx="36" cy="36" r="28" fill="none" stroke="var(--primary)" stroke-width="6" stroke-dasharray="90 180"></circle>
            </svg>
          </div>
        </div>
        <div class="v-col">
          <div class="v-card">
            <canvas id="hist-chart" width="260" height="100"></canvas>
            <pre class="sui-mono" dir="ltr">127.0.0.1:2095 INFO sing-box started</pre>
          </div>
        </div>
      </div>
    </main>
    <div id="qr-overlay" class="v-overlay">
      <div class="v-overlay__content">
        <div class="v-card" role="dialog">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <strong>QR Code</strong>
            <button id="close-qr" type="button">Close</button>
          </div>
          <div class="sui-qr-frame">
            <canvas id="qr-canvas" width="120" height="120"></canvas>
          </div>
        </div>
      </div>
    </div>
  </div>
  <script>
    function drawChart() {
      const c = document.getElementById('hist-chart');
      const ctx = c.getContext('2d');
      const isDark = document.documentElement.classList.contains('dark');
      ctx.clearRect(0, 0, c.width, c.height);
      ctx.fillStyle = isDark ? '#151E2E' : '#FFFFFF';
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.strokeStyle = isDark ? '#2DD4BF' : '#0F766E';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(10, 80); ctx.lineTo(90, 35); ctx.lineTo(180, 50); ctx.lineTo(250, 20);
      ctx.stroke();
    }
    function drawQr() {
      const q = document.getElementById('qr-canvas');
      const ctx = q.getContext('2d');
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(0, 0, 120, 120);
      ctx.fillStyle = '#000000';
      for (let i = 10; i < 110; i += 20) {
        ctx.fillRect(i, 10, 12, 12);
        ctx.fillRect(i, 50, 12, 12);
        ctx.fillRect(10, i, 12, 12);
      }
    }
    drawChart();
    drawQr();
    document.getElementById('toggle-theme').addEventListener('click', () => {
      document.documentElement.classList.toggle('dark');
      drawChart();
    });
    document.getElementById('toggle-rtl').addEventListener('click', () => {
      const next = document.documentElement.getAttribute('dir') === 'rtl' ? 'ltr' : 'rtl';
      document.documentElement.setAttribute('dir', next);
    });
    document.getElementById('open-qr').addEventListener('click', () => {
      document.getElementById('qr-overlay').classList.add('v-overlay--active');
    });
    document.getElementById('close-qr').addEventListener('click', () => {
      document.getElementById('qr-overlay').classList.remove('v-overlay--active');
    });
  </script>
</body>
</html>
"""

_FAIL_HTML = """<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8" />
  <title>Defective Fixture</title>
  <style>
    body {
      margin: 0;
      font-family: 'Lexend', sans-serif;
      background: #1A102B;
      color: #94A3B8;
    }
    .v-application { background: #1A102B; }
    header.v-app-bar {
      height: 56px;
      background: #1A102B;
      box-shadow: 0px 3px 5px -1px rgba(0,0,0,0.2), 0px 5px 8px 0px rgba(0,0,0,0.14), 0px 1px 14px 0px rgba(0,0,0,0.12);
    }
    nav.v-navigation-drawer {
      position: fixed;
      top: 56px;
      left: 0;
      width: 220px;
      bottom: 0;
      background: #1A102B;
    }
    main.v-main { margin: 10px; }
    .v-card {
      background: #1A102B;
      border-radius: 24px;
      padding: 12px;
      box-shadow: 0px 3px 5px -1px rgba(0,0,0,0.2), 0px 5px 8px 0px rgba(0,0,0,0.14), 0px 1px 14px 0px rgba(0,0,0,0.12);
    }
    .v-card-subtitle { border-bottom: 1px solid rgb(128, 128, 128); }
    .low-contrast { background: #CBD5E1; color: #94A3B8; padding: 4px; }
    .purple-btn { background: #7C3AED; color: #FFFFFF; width: 14px; height: 14px; padding: 0; border: 0; }
    .wide-overflow { width: 2000px; height: 10px; }
    .v-row { position: relative; height: 120px; }
    .overlap-col-a { position: absolute; left: 0; top: 0; width: 200px; }
    .overlap-col-b { position: absolute; left: 50px; top: 10px; width: 200px; }
    .v-overlay--active { position: fixed; inset: 0; display: none; }
    .v-overlay--active.open { display: block; background: #1A102B; }
  </style>
</head>
<body>
  <div class="v-application">
    <header class="v-app-bar">
      <i class="mdi mdi-menu" style="cursor:pointer; display:inline-block; width:20px; height:20px;"></i>
      <button id="tiny-btn" class="purple-btn" type="button">x</button>
      <button id="native-confirm" type="button" style="width:40px;height:32px;" onclick="window.confirm('native?')">C</button>
      <button id="log-err" type="button" style="width:40px;height:32px;" onclick="console.error('injected error')">E</button>
    </header>
    <nav class="v-navigation-drawer">Nav</nav>
    <main class="v-main">
      <div class="wide-overflow"></div>
      <div class="low-contrast">Low contrast leaked key: pages.clients.title</div>
      <div class="v-card">
        <div class="v-card-subtitle">Legacy Gray Border</div>
        <div class="v-card"><div class="v-card">3-deep nested card (forbidden)</div></div>
        <svg width="64" height="64" viewBox="0 0 64 64">
          <circle cx="32" cy="32" r="24" fill="none" stroke="#1A102B" stroke-width="6"></circle>
        </svg>
        <canvas id="blank-canvas" width="200" height="80"></canvas>
      </div>
      <div class="v-row">
        <div class="overlap-col-a"><div class="v-card" style="height:80px;">Card A</div></div>
        <div class="overlap-col-b"><div class="v-card" style="height:80px;">Card B</div></div>
      </div>
    </main>
    <div id="bad-qr-overlay" class="v-overlay--active open">
      <div class="v-overlay__content">
        <div class="v-card" role="dialog" style="background:#1A102B; padding:0;">
          <canvas id="bad-qr" width="120" height="120"></canvas>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
"""


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/pass.html"):
            body = _PASS_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/fail.html"):
            body = _FAIL_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/status-401"):
            body = b'{"success":false,"msg":"Invalid login","obj":null}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A002, ANN002
        return


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# 1. Module Inventory, Quantitative Thresholds & AST Non-Vacuity Tests
# ---------------------------------------------------------------------------


class TestSuiteIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_all_tiers()

    def test_feature_areas_and_thresholds(self) -> None:
        self.assertEqual(N_AREAS, 14)
        self.assertEqual(len(FEATURE_AREAS), 14)
        counts = REGISTRY.counts_by_tier_and_area()

        # Tier 1: >= 5 per area across all 14 areas (>= 70)
        t1_total = sum(counts[1].values())
        self.assertGreaterEqual(t1_total, MIN_TIER1_TOTAL)
        for area in FEATURE_AREAS:
            self.assertGreaterEqual(
                counts[1][area],
                MIN_TIER1_PER_AREA,
                f"Tier 1 area {area} has {counts[1][area]} < {MIN_TIER1_PER_AREA}",
            )

        # Tier 2: >= 5 per area across all 14 areas (>= 70)
        t2_total = sum(counts[2].values())
        self.assertGreaterEqual(t2_total, MIN_TIER2_TOTAL)
        for area in FEATURE_AREAS:
            self.assertGreaterEqual(
                counts[2][area],
                MIN_TIER2_PER_AREA,
                f"Tier 2 area {area} has {counts[2][area]} < {MIN_TIER2_PER_AREA}",
            )

        # Tier 3: >= N (14)
        t3_total = sum(counts[3].values())
        self.assertGreaterEqual(t3_total, MIN_TIER3_TOTAL)

        # Tier 4: >= max(5, N // 2) (7)
        t4_total = sum(counts[4].values())
        self.assertGreaterEqual(t4_total, MIN_TIER4_TOTAL)

        # Unique test IDs across all tiers
        all_tests = REGISTRY.all_tests()
        ids = [t.test_id for t in all_tests]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate test_ids detected")
        self.assertGreaterEqual(len(all_tests), 161)

    def test_registry_rejects_invalid_metadata(self) -> None:
        reg = TestRegistry()
        with self.assertRaises(ValueError):
            reg.register(tier=9, area="E2E-01", test_id="X1", title="bad tier")(lambda env: None)
        with self.assertRaises(ValueError):
            reg.register(tier=1, area="E2E-99", test_id="X2", title="bad area")(lambda env: None)
        reg.register(tier=1, area="E2E-01", test_id="DUP", title="first")(lambda env: None)
        with self.assertRaises(ValueError):
            reg.register(tier=1, area="E2E-01", test_id="DUP", title="second")(lambda env: None)

    def test_ast_non_vacuity_and_zero_forbidden_imports(self) -> None:
        e2e_files = sorted(_THIS_DIR.glob("*.py"))
        self.assertGreaterEqual(len(e2e_files), 9)

        for py_file in e2e_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], {"pytest", "numpy"}, f"Forbidden import in {py_file}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], {"pytest", "numpy"}, f"Forbidden import in {py_file}")
                elif isinstance(node, ast.keyword) and node.arg == "wait_until":
                    if isinstance(node.value, ast.Constant) and node.value.value == "networkidle":
                        self.fail(f"Forbidden wait_until='networkidle' found in {py_file}")

        tier_files = [
            _THIS_DIR / "tier1_features.py",
            _THIS_DIR / "tier2_boundaries.py",
            _THIS_DIR / "tier3_pairwise.py",
            _THIS_DIR / "tier4_workflows.py",
        ]
        registered_fn_count = 0
        for tf in tier_files:
            tree = ast.parse(tf.read_text(encoding="utf-8"), filename=str(tf))
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                is_reg = any(
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "register_test"
                    for dec in node.decorator_list
                )
                if not is_reg:
                    continue
                registered_fn_count += 1
                # Must not be empty pass / ellipsis stub
                real_stmts = [
                    s
                    for s in node.body
                    if not isinstance(s, ast.Pass)
                    and not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                ]
                self.assertGreater(len(real_stmts), 0, f"Empty stub test {node.name} in {tf.name}")

                # Must not contain vacuous `assert True`
                has_real_check = False
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assert):
                        if isinstance(sub.test, ast.Constant) and sub.test.value is True:
                            self.fail(f"Vacuous `assert True` found in {tf.name}:{node.name}")
                        has_real_check = True
                    elif isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name) and (
                            sub.func.id.startswith("probe_")
                            or sub.func.id.startswith("assert_")
                            or sub.func.id.startswith("check_")
                            or sub.func.id in ("login_via_ui", "open_and_cancel_dialog", "toggle_sidebar_rail")
                        ):
                            has_real_check = True
                        elif isinstance(sub.func, ast.Attribute) and sub.func.attr in (
                            "assert_clean",
                            "wait_for",
                            "wait_for_function",
                        ):
                            has_real_check = True
                self.assertTrue(has_real_check, f"Test {node.name} in {tf.name} has no real assertions or probes")

        self.assertEqual(registered_fn_count, len(REGISTRY.all_tests()))


# ---------------------------------------------------------------------------
# 2. CLI Argument Parsing & Error Exit Tests
# ---------------------------------------------------------------------------


class TestCliRunner(unittest.TestCase):
    def test_cli_defaults_and_custom_flags(self) -> None:
        ns_def = parse_args([])
        self.assertEqual(ns_def.binary, "./sui")
        self.assertEqual(ns_def.tier, "all")
        self.assertFalse(ns_def.headed)
        self.assertFalse(ns_def.check)
        self.assertIsNone(ns_def.screenshots)
        self.assertIsNone(ns_def.filter)

        ns = parse_args(
            [
                "--binary",
                "/tmp/custom-sui",
                "--tier",
                "3",
                "--headed",
                "--screenshots",
                "/tmp/shots",
                "--filter",
                "PAIR_01",
            ]
        )
        self.assertEqual(ns.binary, "/tmp/custom-sui")
        self.assertEqual(ns.tier, "3")
        self.assertTrue(ns.headed)
        self.assertEqual(ns.screenshots, "/tmp/shots")
        self.assertEqual(ns.filter, "PAIR_01")

    def test_cli_rejects_invalid_tier(self) -> None:
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                parse_args(["--tier", "9"])
        self.assertEqual(ctx.exception.code, 2)

    def test_missing_binary_exits_2(self) -> None:
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            rc = run_e2e_main(["--binary", "/nonexistent/path/to/sui", "--tier", "1"])
        self.assertEqual(rc, 2)
        self.assertIn("not exist or is not executable", stderr_buf.getvalue())


# ---------------------------------------------------------------------------
# 3. Pure-Python Probe Math & Port Allocation Tests
# ---------------------------------------------------------------------------


class TestPurePythonProbes(unittest.TestCase):
    def test_allocate_distinct_free_ports(self) -> None:
        ports = allocate_distinct_free_ports(4)
        self.assertEqual(len(ports), 4)
        self.assertEqual(len(set(ports)), 4)
        for p in ports:
            self.assertTrue(1 <= p <= 65535)
        with self.assertRaises(ValueError):
            allocate_distinct_free_ports(0)

    def test_parse_css_color_formats(self) -> None:
        self.assertEqual(parse_css_color("transparent"), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(parse_css_color("#0F766E"), (15.0, 118.0, 110.0, 1.0))
        self.assertEqual(parse_css_color("rgb(15, 118, 110)"), (15.0, 118.0, 110.0, 1.0))
        self.assertAlmostEqual(parse_css_color("rgba(15, 118, 110, 0.5)")[3], 0.5, places=3)
        self.assertAlmostEqual(parse_css_color("rgb(15 118 110 / 25%)")[3], 0.25, places=3)
        # Chromium 159 color-mix() serialization
        r, g, b, a = parse_css_color("color(srgb 0.0588235 0.462745 0.431373 / 0.12)")
        self.assertAlmostEqual(r, 15.0, delta=0.2)
        self.assertAlmostEqual(g, 118.0, delta=0.2)
        self.assertAlmostEqual(b, 110.0, delta=0.2)
        self.assertAlmostEqual(a, 0.12, places=4)
        with self.assertRaises(ValueError):
            parse_css_color("not-a-color")

    def test_wcag_luminance_alpha_and_contrast_ratios(self) -> None:
        self.assertAlmostEqual(relative_luminance((0, 0, 0)), 0.0, places=5)
        self.assertAlmostEqual(relative_luminance((255, 255, 255)), 1.0, places=5)
        self.assertAlmostEqual(contrast_ratio((0, 0, 0), (255, 255, 255)), 21.0, places=2)
        # Approved Light primary #0F766E on #FFFFFF (~5.47 >= 4.5 AA)
        cr_light = contrast_ratio((15, 118, 110), (255, 255, 255))
        self.assertGreaterEqual(cr_light, 5.3)
        # Approved Dark primary #2DD4BF on #151E2E (~8.97 >= 4.5 AA)
        cr_dark = contrast_ratio((45, 212, 191), (21, 30, 46))
        self.assertGreaterEqual(cr_dark, 8.5)
        # Known AA failure: orange #FF9800 on white (~2.16 < 4.5)
        cr_fail = contrast_ratio((255, 152, 0), (255, 255, 255))
        self.assertLess(cr_fail, 2.5)
        # Source-over alpha compositing 50% black over white = mid gray (127.5)
        comp = alpha_composite((0, 0, 0, 0.5), (255, 255, 255))
        self.assertAlmostEqual(comp[0], 127.5, places=1)

    def test_zero_purple_on_dark_detector(self) -> None:
        approved_dark_tokens = [
            "#0E141F",
            "#151E2E",
            "#1C273B",
            "#1B2639",
            "#243044",
            "#334155",
            "#E2E8F0",
            "#2DD4BF",
            "#14B8A6",
            "#042F2E",
            "#94A3B8",
            "#0F172A",
            "#34D399",
            "#052E16",
            "#FBBF24",
            "#F87171",
            "#38BDF8",
            "#FB923C",
        ]
        for hex_col in approved_dark_tokens:
            rgb = parse_css_color(hex_col)[:3]
            self.assertFalse(is_purple_violation(rgb, role="surface"), f"False positive on {hex_col}")
            self.assertFalse(is_purple_violation(rgb, role="accent"), f"False positive accent on {hex_col}")

        purple_traps = [
            "#6200EE",
            "#BB86FC",
            "#7C3AED",
            "#A855F7",
            "#C026D3",
            "#1A102B",
            "#161224",
            "#241C35",
            "#1D1829",
            "#29223B",
        ]
        for hex_col in purple_traps:
            rgb = parse_css_color(hex_col)[:3]
            self.assertTrue(is_purple_violation(rgb, role="surface"), f"Missed purple trap {hex_col}")

    def test_chromium159_box_shadow_parsing(self) -> None:
        elev5 = (
            "rgba(0, 0, 0, 0.2) 0px 3px 5px -1px, "
            "rgba(0, 0, 0, 0.14) 0px 5px 8px 0px, "
            "rgba(0, 0, 0, 0.12) 0px 1px 14px 0px"
        )
        layers = parse_box_shadow_layers(elev5)
        self.assertEqual(len(layers), 3)
        self.assertAlmostEqual(layers[2]["blur"], 14.0)
        self.assertAlmostEqual(layers[0]["alpha"], 0.2)
        self.assertTrue(is_legacy_heavy_shadow(elev5))

        modern = "rgba(15, 23, 42, 0.04) 0px 1px 2px 0px"
        self.assertFalse(is_legacy_heavy_shadow(modern))
        self.assertFalse(is_legacy_heavy_shadow("none"))

    def test_shared_server_safety_guard(self) -> None:
        guarded = SuiApiClient("http://127.0.0.1:1", "/app/", shared_server_guard=True)
        for forbidden_action in (
            "restartApp",
            "changePass",
            "totp-enable",
            "installAcme",
            "issueCert",
            "renewCert",
            "syncNginxProxy",
            "applyCert",
        ):
            with self.assertRaises(RuntimeError, msg=f"Guard failed to block {forbidden_action}"):
                guarded.post_form(forbidden_action, {})
        with self.assertRaises(RuntimeError):
            guarded.save("outbounds", "del", "direct")
        with self.assertRaises(RuntimeError):
            guarded.save("settings", "set", {"webPort": "9999"})
        with self.assertRaises(RuntimeError):
            guarded.save("settings", "set", {"trafficAge": "0"})
        with self.assertRaises(RuntimeError):
            guarded.post_multipart(
                "importdb",
                file_field="db",
                filename="valid.db",
                content=b"SQLite format 3\x00dummy",
            )


# ---------------------------------------------------------------------------
# 4. Live Headless Chromium PASS & FAIL Fixture Probe Verification
# ---------------------------------------------------------------------------


class TestLiveChromiumFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright

        cls.server = _ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.port = int(cls.server.server_address[1])
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

        cls.pw = sync_playwright().start()
        cls.browser = launch_chromium(cls.pw, headed=False)

    @classmethod
    def tearDownClass(cls) -> None:
        with contextlib.suppress(Exception):
            cls.browser.close()
        with contextlib.suppress(Exception):
            cls.pw.stop()
        with contextlib.suppress(Exception):
            cls.server.shutdown()
            cls.server.server_close()

    def test_all_probes_pass_on_compliant_fixture(self) -> None:
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            page = ctx.new_page()
            guard = RuntimeGuard(page)
            page.goto(f"{self.base}/pass.html", wait_until="domcontentloaded")

            # Light mode probes
            probe_surface_separation(page, mode="light")
            probe_wcag_contrast(page)
            probe_card_and_shell_hygiene(page, max_card_depth=2)
            probe_offline_typography(page, guard, require_tabular=True, require_mono=True)
            probe_viewport_overflow_and_grid(page, check_grid=True, min_card_width=180.0)
            probe_svg_gauges(page)
            a11y = probe_accessible_actions(page)
            self.assertGreaterEqual(a11y["buttonCount"], 3)
            i18n_ok = probe_i18n_hygiene(page)
            self.assertGreater(i18n_ok["sample_len"], 10)

            # In Light mode, require_dark_backdrop=True on QR dialog MUST fail
            page.click("#open-qr")
            with self.assertRaises(AssertionError):
                probe_qr_quiet_zone(page, require_dark_backdrop=True)
            close_active_dialog(page)

            # Canvas chart Light -> Dark visual redraw diff
            res = probe_canvas_charts(
                page,
                verify_theme_diff=True,
                toggle_theme_fn=lambda: page.click("#toggle-theme"),
            )
            self.assertTrue(res["theme_changed"])

            # Dark mode probes (including zero purple & QR white quiet zone with dark backdrop)
            probe_surface_separation(page, mode="dark")
            probe_zero_purple_on_dark(page)
            probe_wcag_contrast(page)

            page.click("#open-qr")
            probe_qr_quiet_zone(page, require_dark_backdrop=True)
            close_active_dialog(page)

            # RTL probe
            page.click("#toggle-rtl")
            probe_rtl_fidelity(page, is_desktop=True, require_ltr_tech=True)

            guard.assert_clean()
        finally:
            ctx.close()

    def test_all_probes_fail_on_defective_fixture(self) -> None:
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            page = ctx.new_page()
            guard = RuntimeGuard(page)
            page.goto(f"{self.base}/fail.html", wait_until="domcontentloaded")

            # 1. QR quiet zone probe MUST fail on dark QR overlay (before hiding overlay)
            with self.assertRaises(AssertionError):
                probe_qr_quiet_zone(page, require_dark_backdrop=True)
            # Hide overlay (intentionally keep .v-overlay--active with display:none to verify visibility filtering!)
            page.evaluate("() => document.getElementById('bad-qr-overlay').classList.remove('open')")

            # 2. WCAG contrast MUST fail on #94A3B8 on #CBD5E1
            with self.assertRaises(AssertionError):
                probe_wcag_contrast(page)

            # 3. Purple-on-dark MUST fail on #1A102B & #7C3AED
            with self.assertRaises(AssertionError):
                probe_zero_purple_on_dark(page)

            # 4. Surface separation MUST fail when card == canvas bg
            with self.assertRaises(AssertionError):
                probe_surface_separation(page, mode="dark")

            # 5. Card & shell hygiene MUST fail on elevation-5, 24px radius, gray subtitle, 10px margin, 3-deep nesting
            hyg = probe_card_and_shell_hygiene(page, max_card_depth=2, assert_pass=False)
            self.assertGreaterEqual(len(hyg["violations"]), 5, f"Expected >=5 hygiene violations: {hyg}")
            self.assertTrue(
                any("nesting depth 3 > 2" in v for v in hyg["violations"]),
                f"Expected 3-deep card nesting violation: {hyg['violations']}",
            )

            # 6. Offline typography MUST fail on 'Lexend'
            with self.assertRaises(AssertionError):
                probe_offline_typography(page, guard)

            # 7. Viewport overflow & grid MUST fail on 2000px overflow + overlapping cards
            ov = probe_viewport_overflow_and_grid(page, check_grid=True, assert_pass=False)
            self.assertGreaterEqual(len(ov["violations"]), 2, f"Expected overflow + overlap violations: {ov}")

            # 8. SVG gauge MUST fail on invisible track stroke (ΔRGB == 0) & missing %
            with self.assertRaises(AssertionError):
                probe_svg_gauges(page)

            # 9. Canvas chart MUST fail on blank canvas
            with self.assertRaises(AssertionError):
                probe_canvas_charts(page)

            # 10. RTL fidelity MUST fail on LTR page
            with self.assertRaises(AssertionError):
                probe_rtl_fidelity(page, is_desktop=True)

            # 11. Accessible actions MUST fail on raw clickable <i class="mdi"> & 14x14px button
            a11y = probe_accessible_actions(page, assert_pass=False)
            self.assertGreaterEqual(len(a11y["violations"]), 2, f"Expected raw-icon + tiny-btn violations: {a11y}")

            # 12. i18n hygiene MUST fail on leaked `pages.clients.title`
            i18n_bad = probe_i18n_hygiene(page, assert_pass=False)
            self.assertIn("pages.clients.title", i18n_bad["leaks"])
            with self.assertRaises(AssertionError):
                probe_i18n_hygiene(page)

            # 13. RuntimeGuard MUST catch native window.confirm() and console.error()
            page.click("#native-confirm")
            page.click("#log-err")
            self.assertEqual(len(guard.native_dialogs), 1)
            self.assertEqual(guard.native_dialogs[0][0], "confirm")
            self.assertGreaterEqual(len(guard.console_errors), 1)
            with self.assertRaises(AssertionError):
                guard.assert_clean()
        finally:
            ctx.close()

    def test_storage_state_local_storage_persists_mutations_across_reload(self) -> None:
        state = E2EEnv.build_storage_state(None, self.base, {"theme": "light", "locale": "en"})
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800}, storage_state=state)
        try:
            page = ctx.new_page()
            page.goto(f"{self.base}/pass.html", wait_until="domcontentloaded")
            self.assertEqual(page.evaluate("() => localStorage.getItem('theme')"), "light")
            self.assertEqual(page.evaluate("() => localStorage.getItem('locale')"), "en")

            # Mutate localStorage like the UI switcher does, then reload AND navigate again
            page.evaluate("() => { localStorage.setItem('theme', 'dark'); localStorage.setItem('locale', 'fa'); }")
            page.reload(wait_until="domcontentloaded")
            self.assertEqual(page.evaluate("() => localStorage.getItem('theme')"), "dark")
            self.assertEqual(page.evaluate("() => localStorage.getItem('locale')"), "fa")

            page.goto(f"{self.base}/pass.html?nav=2", wait_until="domcontentloaded")
            self.assertEqual(page.evaluate("() => localStorage.getItem('theme')"), "dark")
            self.assertEqual(page.evaluate("() => localStorage.getItem('locale')"), "fa")
        finally:
            ctx.close()

    def test_runtime_guard_allow_http_status_suppresses_matching_401_console_noise(self) -> None:
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            page = ctx.new_page()
            guard = RuntimeGuard(page)
            page.goto(f"{self.base}/pass.html", wait_until="domcontentloaded")

            with guard.allow_http_status(401, "/status-401"):
                st = page.evaluate("async () => (await fetch('/status-401')).status")
                self.assertEqual(st, 401)
            # Allowlisted 401 must NOT poison http_errors or console_errors
            guard.assert_clean()

            # Unallowed 404 MUST still fail RuntimeGuard
            st404 = page.evaluate("async () => (await fetch('/unallowed-404')).status")
            self.assertEqual(st404, 404)
            with self.assertRaises(AssertionError):
                guard.assert_clean()
        finally:
            ctx.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
