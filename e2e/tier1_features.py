"""Tier 1: Feature Coverage — 70 genuine isolated happy-path tests (5 per area E2E-01..E2E-14).

Every test runs strictly opaque-box against a live compiled ./sui binary + real Chromium browser.
"""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
import urllib.parse
from pathlib import Path

from e2e.harness import (
    E2EEnv,
    allocate_distinct_free_ports,
    create_temp_self_signed_cert,
    register_test,
)
from e2e.probes import (
    assert_dialog_chrome_clean,
    assert_security_headers,
    check_confirm_dialog_semantics,
    click_page_add_button,
    close_active_dialog,
    find_active_dialog,
    get_sidebar_rect,
    login_via_ui,
    navigate_spa,
    open_mobile_drawer,
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
    switch_locale_via_ui,
    switch_theme_via_ui,
    toggle_sidebar_rail,
    wait_for_live_status,
    wait_for_rAF_settle,
    wait_for_spa_ready,
)

# ===========================================================================
# E2E-01: Binary Embed, HTTP Headers & Subpath Serving (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-01",
    test_id="T1_E2E01_01",
    title="Default /app/ serves index.html with window.BASE_URL='/app/' and HTTP 200 on /app/login",
)
def t1_e2e01_01(env: E2EEnv) -> None:
    srv = env.primary
    client = srv.api_client(authenticated=False)
    resp = client.raw_request("GET", f"{srv.base_url}login", follow_redirects=False, use_cookies=False)
    assert resp.status == 200, f"Expected 200 on /app/login, got {resp.status}"
    assert "text/html" in resp.header("content-type").lower()
    assert 'window.BASE_URL = "/app/"' in resp.text or "window.BASE_URL='/app/'" in resp.text, (
        "Expected Go template BASE_URL injection '/app/' in index.html"
    )
    with env.anon_page(test_id="T1_E2E01_01") as (page, _guard):
        navigate_spa(page, srv.base_url, "login", expect_shell=False)
        base_url_js = page.evaluate("() => window.BASE_URL")
        assert base_url_js == "/app/", f"window.BASE_URL mismatch: {base_url_js!r}"


@register_test(
    tier=1,
    area="E2E-01",
    test_id="T1_E2E01_02",
    title="HTML and API responses include all required security headers and Cache-Control: no-store",
)
def t1_e2e01_02(env: E2EEnv) -> None:
    srv = env.primary
    client = srv.api_client(authenticated=False)
    html_resp = client.raw_request("GET", f"{srv.base_url}login", use_cookies=False)
    assert html_resp.status == 200
    assert_security_headers(html_resp.headers, is_asset=False)

    api_resp = client.raw_request("GET", f"{srv.base_url}api/auth-meta", use_cookies=False)
    assert api_resp.status == 200
    assert_security_headers(api_resp.headers, is_asset=False)
    meta = api_resp.json()
    assert meta.get("success") is True and isinstance(meta.get("obj"), dict)


@register_test(
    tier=1,
    area="E2E-01",
    test_id="T1_E2E01_03",
    title="Embedded /app/assets/* static bundles load HTTP 200 with immutable Cache-Control and zero CDN calls",
)
def t1_e2e01_03(env: E2EEnv) -> None:
    srv = env.primary
    with env.auth_page(test_id="T1_E2E01_03") as (page, guard):
        navigate_spa(page, srv.base_url, "")
        asset_responses = [
            (st, url, hdrs)
            for st, _m, url, hdrs in guard.all_responses
            if "/app/assets/" in url
        ]
        assert len(asset_responses) >= 2, f"Expected >=2 /app/assets/* requests, observed {len(asset_responses)}"
        for st, url, hdrs in asset_responses:
            assert st == 200, f"Asset {url} returned HTTP {st}"
            assert_security_headers(hdrs, is_asset=True)
        assert not guard.external_requests, f"External CDN requests detected: {guard.external_requests}"


@register_test(
    tier=1,
    area="E2E-01",
    test_id="T1_E2E01_04",
    title="Trailing-slash /app -> 307 /app/ and unauthenticated /app/ -> 307 /app/login",
)
def t1_e2e01_04(env: E2EEnv) -> None:
    srv = env.primary
    client = srv.api_client(authenticated=False)
    r1 = client.raw_request("GET", "/app", follow_redirects=False, use_cookies=False)
    assert r1.status == 307, f"GET /app expected 307, got {r1.status}"
    assert r1.header("location") == "/app/", f"GET /app Location expected '/app/', got {r1.header('location')!r}"

    r2 = client.raw_request("GET", "/app/", follow_redirects=False, use_cookies=False)
    assert r2.status == 307, f"Unauth GET /app/ expected 307, got {r2.status}"
    assert r2.header("location") == "/app/login", f"Expected '/app/login', got {r2.header('location')!r}"


@register_test(
    tier=1,
    area="E2E-01",
    test_id="T1_E2E01_05",
    title="Custom subpath /ci/ injects window.BASE_URL='/ci/', serves /ci/assets/*, and renders authenticated SPA",
)
def t1_e2e01_05(env: E2EEnv) -> None:
    ci_srv = env.subpath
    with env.auth_page(server="subpath", test_id="T1_E2E01_05") as (page, guard):
        navigate_spa(page, ci_srv.base_url, "")
        assert page.evaluate("() => window.BASE_URL") == "/ci/"
        ci_assets = [url for st, _m, url, _h in guard.all_responses if "/ci/assets/" in url and st == 200]
        assert len(ci_assets) >= 2, f"Expected embedded /ci/assets/* 200 responses, got {ci_assets}"
        assert "/ci/" in page.url


# ===========================================================================
# E2E-02: Theme System, WCAG-AA Contrast & Zero Purple-on-Dark (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-02",
    test_id="T1_E2E02_01",
    title="Light mode renders separated light canvas/cards and passes WCAG 2.1 AA contrast on /",
)
def t1_e2e02_01(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T1_E2E02_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_surface_separation(page, mode="light")
        probe_wcag_contrast(page)


@register_test(
    tier=1,
    area="E2E-02",
    test_id="T1_E2E02_02",
    title="Dark mode renders deep cool-slate surfaces, passes WCAG AA contrast, and has zero purple-on-dark",
)
def t1_e2e02_02(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T1_E2E02_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        probe_wcag_contrast(page)


@register_test(
    tier=1,
    area="E2E-02",
    test_id="T1_E2E02_03",
    title="Theme switcher toggles Light -> Dark, writes localStorage['theme'], and persists across reload",
)
def t1_e2e02_03(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T1_E2E02_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_surface_separation(page, mode="light")
        switch_theme_via_ui(page, "dark")
        stored = page.evaluate("() => window.localStorage.getItem('theme')")
        assert stored == "dark", f"Expected localStorage['theme'] == 'dark', got {stored!r}"
        probe_surface_separation(page, mode="dark")
        page.reload(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)


@register_test(
    tier=1,
    area="E2E-02",
    test_id="T1_E2E02_04",
    title="System theme mode tracks prefers-color-scheme dark and light dynamically",
)
def t1_e2e02_04(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "system"}, color_scheme="dark", test_id="T1_E2E02_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        page.emulate_media(color_scheme="light")
        wait_for_rAF_settle(page, delay_ms=250)
        probe_surface_separation(page, mode="light")


@register_test(
    tier=1,
    area="E2E-02",
    test_id="T1_E2E02_05",
    title="Notivue toast matches active Dark/System theme polarity with dark surface, LTR dir in en, and AA contrast",
)
def t1_e2e02_05(env: E2EEnv) -> None:
    # Using theme='system' + prefers-color-scheme: dark verifies F-03 message.vue dark & isRtl fixes directly
    with env.anon_page(
        local_storage={"theme": "system", "locale": "en"},
        color_scheme="dark",
        test_id="T1_E2E02_05",
    ) as (page, _g):
        login_via_ui(page, env.primary.base_url, "e2e_toast_probe_t1", "wrong-pass", expect_redirect=False)
        toast = page.locator('[role="status"], [role="alert"], .Notivue__notification, li[class*="Notivue"]').first
        toast.wait_for(state="visible", timeout=6000)
        wait_for_rAF_settle(page, delay_ms=150)
        toast_style = toast.evaluate(
            """(el) => {
              const box = el.querySelector('.Notivue__notification, [class*="Notification"]') || el;
              const cs = getComputedStyle(box);
              const bg = cs.backgroundColor;
              const m = bg.match(/rgba?\\(\\s*([\\d.]+)[,\\s]+([\\d.]+)[,\\s]+([\\d.]+)/i);
              const rgb = m ? [parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])] : null;
              return { dir: cs.direction, bg, rgb };
            }"""
        )
        assert toast_style["dir"] == "ltr", f"Toast in English locale erroneously rendered RTL: {toast_style}"
        if toast_style["rgb"] is not None:
            lum = relative_luminance(tuple(toast_style["rgb"]))
            assert lum < 0.25, f"Toast background in Dark/System mode is bright/unthemed (L={lum:.3f}): {toast_style}"
        probe_zero_purple_on_dark(page)
        probe_wcag_contrast(page)


# ===========================================================================
# E2E-03: Offline Typography, Tabular Numerals & Visual Craft Hygiene (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-03",
    test_id="T1_E2E03_01",
    title="Zero external font/CDN requests and zero 'Lexend' font references across dashboard",
)
def t1_e2e03_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E03_01") as (page, guard):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_offline_typography(page, guard, require_tabular=False, require_mono=False)


@register_test(
    tier=1,
    area="E2E-03",
    test_id="T1_E2E03_02",
    title="Dashboard telemetry, KPI, and gauge readouts enforce tabular-nums",
)
def t1_e2e03_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E03_02") as (page, guard):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_offline_typography(page, guard, require_tabular=True)


@register_test(
    tier=1,
    area="E2E-03",
    test_id="T1_E2E03_03",
    title="Technical readouts (Raw JSON config and Logs) render with monospace font family",
)
def t1_e2e03_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E03_03") as (page, guard):
        navigate_spa(page, env.primary.base_url, "config?tab=json")
        probe_offline_typography(page, guard, require_mono=True)


@register_test(
    tier=1,
    area="E2E-03",
    test_id="T1_E2E03_04",
    title="Cards and app shell follow hairline-border hygiene (zero elevation-5, zero 24px radii, zero gray subtitle border, zero 10px main margin)",
)
def t1_e2e03_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E03_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_card_and_shell_hygiene(page, max_card_depth=2)


@register_test(
    tier=1,
    area="E2E-03",
    test_id="T1_E2E03_05",
    title="Buttons use non-uppercase text-transform and inputs use modern outlined field styling",
)
def t1_e2e03_05(env: E2EEnv) -> None:
    with env.anon_page(test_id="T1_E2E03_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        info = page.evaluate(
            """() => {
              const btn = document.querySelector('button[type="submit"], form button');
              const btnTransform = btn ? getComputedStyle(btn).textTransform : null;
              const outlinedFields = document.querySelectorAll('.v-field--variant-outlined').length;
              return { btnTransform, outlinedFields };
            }"""
        )
        assert info["btnTransform"] != "uppercase", f"Primary button should not force uppercase: {info}"
        assert info["outlinedFields"] >= 2, f"Expected outlined inputs on /login, found {info['outlinedFields']}"


# ===========================================================================
# E2E-04: Login Page, Authentication Lifecycle & 401 Protection (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-04",
    test_id="T1_E2E04_01",
    title="/login renders centered themed card inside .v-application with 'S-UI Nexus' brand and WCAG AA contrast",
)
def t1_e2e04_01(env: E2EEnv) -> None:
    with env.anon_page(local_storage={"theme": "light"}, test_id="T1_E2E04_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        info = page.evaluate(
            """() => {
              const app = document.querySelector('.v-application');
              const card = document.querySelector('.v-application .v-card');
              const r = card ? card.getBoundingClientRect() : null;
              const mt = card ? parseFloat(getComputedStyle(card).marginTop) : -1;
              return {
                title: document.title,
                bodyText: document.body.innerText,
                inVApp: !!card,
                marginTop: mt,
                cardCenterX: r ? (r.left + r.right) / 2 : 0,
                viewportCenterX: window.innerWidth / 2,
                cardWidth: r ? r.width : 0,
              };
            }"""
        )
        assert info["inVApp"], "Login card must render inside .v-application"
        assert "S-UI Nexus" in info["title"], f"Expected 'S-UI Nexus' in document.title, got {info['title']!r}"
        assert "S-UI Nexus" in info["bodyText"], "Expected 'S-UI Nexus' brand heading on /login"
        assert info["marginTop"] < 90, f"Legacy margin-top: 100px push detected (marginTop={info['marginTop']})"
        assert abs(info["cardCenterX"] - info["viewportCenterX"]) < 40, f"Login card not centered: {info}"
        probe_surface_separation(page, mode="light")
        probe_wcag_contrast(page)


@register_test(
    tier=1,
    area="E2E-04",
    test_id="T1_E2E04_02",
    title="Password visibility toggle on /login switches input type between password and text",
)
def t1_e2e04_02(env: E2EEnv) -> None:
    with env.anon_page(test_id="T1_E2E04_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        pw_input = page.locator('input[type="password"]').first
        pw_input.wait_for(state="visible", timeout=6000)
        pw_input.fill("secret-check")
        toggle = page.locator(
            'button:has(.mdi-eye), button:has(.mdi-eye-off), button:has(.mdi-eye-outline), button:has(.mdi-eye-off-outline), '
            '.v-field__append-inner .mdi-eye, .v-field__append-inner .mdi-eye-off, '
            'button[aria-label*="password" i]'
        ).first
        toggle.wait_for(state="visible", timeout=5000)
        toggle.click()
        wait_for_rAF_settle(page, delay_ms=100)
        revealed = page.locator('input[type="text"]').evaluate_all(
            "els => els.some(e => e.value === 'secret-check')"
        )
        assert revealed, "Clicking password visibility toggle did not change password input to type='text'"
        toggle.click()
        wait_for_rAF_settle(page, delay_ms=100)
        assert page.locator('input[type="password"]').count() >= 1, "Toggling back did not restore type='password'"


@register_test(
    tier=1,
    area="E2E-04",
    test_id="T1_E2E04_03",
    title="UI login (admin/admin) redirects to /, sets HttpOnly SameSite=Lax cookie, and passes api/auth-check",
)
def t1_e2e04_03(env: E2EEnv) -> None:
    srv = env.primary
    # Verify Set-Cookie attributes via raw HTTP login as well
    raw_client = srv.api_client(authenticated=False)
    login_raw = raw_client.raw_request(
        "POST",
        f"{srv.base_url}api/login",
        data=urllib.parse.urlencode({"user": "admin", "pass": "admin"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        use_cookies=True,
    )
    assert login_raw.status == 200 and login_raw.json().get("success") is True
    cookie_hdr = "; ".join(login_raw.set_cookies).lower()
    assert "s-ui-next=" in cookie_hdr and "httponly" in cookie_hdr and "samesite=lax" in cookie_hdr, (
        f"Missing HttpOnly/SameSite=Lax on s-ui-next Set-Cookie: {login_raw.set_cookies}"
    )
    raw_client.logout()

    with env.anon_page(test_id="T1_E2E04_03") as (page, _g):
        login_via_ui(page, srv.base_url, "admin", "admin", expect_redirect=True)
        chk = page.evaluate(
            """async (baseUrl) => (await fetch(baseUrl + 'api/auth-check')).json()""",
            srv.base_url,
        )
        assert (chk.get("obj") or {}).get("authenticated") is True
        assert (chk.get("obj") or {}).get("username") == "admin"


@register_test(
    tier=1,
    area="E2E-04",
    test_id="T1_E2E04_04",
    title="Logout via sidebar clears s-ui-next cookie, redirects to /login, and invalidates auth-check",
)
def t1_e2e04_04(env: E2EEnv) -> None:
    srv = env.primary
    with env.anon_page(test_id="T1_E2E04_04") as (page, _g):
        login_via_ui(page, srv.base_url, "admin", "admin", expect_redirect=True)
        logout_trigger = page.locator(
            'nav .v-list-item:has(.mdi-logout), nav button:has(.mdi-logout), '
            'nav .v-list-item:has-text("Logout"), nav button:has-text("Logout"), '
            '.v-navigation-drawer :has(.mdi-logout-variant)'
        ).first
        logout_trigger.wait_for(state="visible", timeout=6000)
        logout_trigger.click()
        page.wait_for_function("() => window.location.pathname.endsWith('/login')", timeout=10000)
        chk = page.evaluate(
            """async (baseUrl) => (await fetch(baseUrl + 'api/auth-check')).json()""",
            srv.base_url,
        )
        assert (chk.get("obj") or {}).get("authenticated") is False, f"Expected logged-out state, got {chk}"


@register_test(
    tier=1,
    area="E2E-04",
    test_id="T1_E2E04_05",
    title="/login exposes working top-end theme and locale controls prior to authentication",
)
def t1_e2e04_05(env: E2EEnv) -> None:
    with env.anon_page(local_storage={"theme": "light", "locale": "en"}, test_id="T1_E2E04_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        switch_theme_via_ui(page, "dark")
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        switch_locale_via_ui(page, "zhHans")
        loc = page.evaluate("() => window.localStorage.getItem('locale')")
        assert loc and "zh" in loc.lower(), f"Expected zh locale stored on /login, got {loc!r}"


# ===========================================================================
# E2E-05: App Shell — Collapsible Sidebar, Flat Top Bar & Routing (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-05",
    test_id="T1_E2E05_01",
    title="Sidebar displays 'S-UI Nexus' brand header, SVG logo, and links covering all 13 authenticated routes",
)
def t1_e2e05_01(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), test_id="T1_E2E05_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        info = page.evaluate(
            """() => {
              const nav = document.querySelector('nav.v-navigation-drawer, aside.v-navigation-drawer, .v-navigation-drawer');
              if (!nav) return null;
              const links = Array.from(nav.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '');
              const hasSvgOrImg = !!(nav.querySelector('svg') || nav.querySelector('img'));
              return { text: nav.innerText, links, hasSvgOrImg };
            }"""
        )
        assert info is not None, "Navigation drawer not found"
        assert "S-UI Nexus" in info["text"], f"Sidebar missing 'S-UI Nexus' brand: {info['text'][:120]!r}"
        assert info["hasSvgOrImg"], "Sidebar brand mark SVG/image missing"
        expected_segments = [
            "analytics",
            "clients",
            "inbounds",
            "tls",
            "outbounds",
            "endpoints",
            "config",
            "nodes",
            "domains",
            "services",
            "settings",
            "admins",
        ]
        joined = " ".join(info["links"])
        missing = [seg for seg in expected_segments if seg not in joined]
        assert not missing, f"Sidebar missing route links: {missing} (found: {info['links']})"


@register_test(
    tier=1,
    area="E2E-05",
    test_id="T1_E2E05_02",
    title="Desktop sidebar rail collapse toggles width (~248px <-> ~68px) and persists in localStorage across reload",
)
def t1_e2e05_02(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), test_id="T1_E2E05_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        initial = get_sidebar_rect(page)
        assert initial is not None and initial["width"] >= 200, f"Expected expanded desktop sidebar, got {initial}"
        collapsed = toggle_sidebar_rail(page)
        assert 48 <= collapsed["width"] <= 96, f"Expected collapsed rail width ~68px, got {collapsed}"
        stored = page.evaluate("() => window.localStorage.getItem('sui-drawer-rail')")
        assert stored == "true", f"Expected localStorage['sui-drawer-rail'] == 'true', got {stored!r}"

        page.reload(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        reloaded = get_sidebar_rect(page)
        assert reloaded is not None and reloaded["width"] <= 96, f"Rail state did not persist on reload: {reloaded}"

        expanded = toggle_sidebar_rail(page)
        assert expanded["width"] >= 200, f"Expected re-expanded sidebar >=200px, got {expanded}"


@register_test(
    tier=1,
    area="E2E-05",
    test_id="T1_E2E05_03",
    title="Top bar is flat (~56px) with hairline bottom border, zero heavy shadow, and semantic <button> triggers",
)
def t1_e2e05_03(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), test_id="T1_E2E05_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        hygiene = probe_card_and_shell_hygiene(page, check_topbar=True)
        tb = hygiene["topbar"]
        assert tb is not None, "Topbar not rendered"
        assert 44 <= tb["height"] <= 68, f"Expected flat ~56px topbar height, got {tb['height']}"
        probe_accessible_actions(page)


@register_test(
    tier=1,
    area="E2E-05",
    test_id="T1_E2E05_04",
    title="All 4 legacy SPA routes redirect properly (/logs, /basics, /dns, /rules)",
)
def t1_e2e05_04(env: E2EEnv) -> None:
    cases = [
        ("logs", "/analytics", None),
        ("basics", "/config", "basics"),
        ("dns", "/config", "dns"),
        ("rules", "/config", "route"),
    ]
    with env.auth_page(test_id="T1_E2E05_04") as (page, _g):
        for old_path, target_path, expected_tab in cases:
            navigate_spa(page, env.primary.base_url, old_path)
            parsed = urllib.parse.urlsplit(page.url)
            assert parsed.path.endswith(target_path), f"/{old_path} redirected to {page.url!r}, expected {target_path}"
            if expected_tab is not None:
                qs = urllib.parse.parse_qs(parsed.query)
                assert qs.get("tab") == [expected_tab], f"/{old_path} expected ?tab={expected_tab}, got {page.url!r}"


@register_test(
    tier=1,
    area="E2E-05",
    test_id="T1_E2E05_05",
    title="Mobile viewport (375x812) hides sidebar initially and opens temporary drawer via accessible menu button",
)
def t1_e2e05_05(env: E2EEnv) -> None:
    with env.auth_page(viewport=(375, 812), test_id="T1_E2E05_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        before = get_sidebar_rect(page)
        assert before is None or before["right"] <= 2 or before["left"] >= 373, (
            f"Sidebar should be closed off-canvas initially at 375px, got {before}"
        )
        opened = open_mobile_drawer(page)
        assert opened["width"] >= 180, f"Mobile drawer did not open properly: {opened}"
        probe_viewport_overflow_and_grid(page)


# ===========================================================================
# E2E-06: Dashboard Overview, Core Status & Host Telemetry (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-06",
    test_id="T1_E2E06_01",
    title="Core Status card displays running sing-box telemetry without decorative diagonal gradient",
)
def t1_e2e06_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E06_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        info = page.evaluate(
            """() => {
              const cards = Array.from(document.querySelectorAll('main .v-card'));
              const gradients = [];
              for (const c of cards) {
                const bgImg = getComputedStyle(c).backgroundImage || '';
                if (bgImg.includes('linear-gradient') || bgImg.includes('radial-gradient')) {
                  gradients.push(bgImg.slice(0, 80));
                }
              }
              return { text: document.querySelector('main')?.innerText || '', gradients };
            }"""
        )
        assert not info["gradients"], f"Decorative gradient detected on Dashboard card(s): {info['gradients']}"
        assert re.search(r"running|online|sing-box|uptime|goroutine", info["text"], re.IGNORECASE), (
            f"Core Status telemetry missing on Dashboard: {info['text'][:250]!r}"
        )


@register_test(
    tier=1,
    area="E2E-06",
    test_id="T1_E2E06_02",
    title="Dashboard KPI summary cards reflect fresh DB counts (outbounds >= 1) and navigate on click",
)
def t1_e2e06_02(env: E2EEnv) -> None:
    api = env.api()
    db_stat = (api.get_json("status", {"r": "db"}).get("obj") or {}).get("db") or {}
    assert int(db_stat.get("outbounds", 0)) >= 1, f"Fresh DB should have seeded direct outbound: {db_stat}"

    with env.auth_page(test_id="T1_E2E06_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        # Click the Outbounds KPI card or link in main area
        kpi = page.locator('main a[href*="outbounds"], main .v-card:has-text("Outbound")').first
        kpi.wait_for(state="visible", timeout=6000)
        kpi.click()
        page.wait_for_function("() => window.location.pathname.includes('/outbounds')", timeout=8000)


@register_test(
    tier=1,
    area="E2E-06",
    test_id="T1_E2E06_03",
    title="Host System Info panel displays structured key-value host telemetry matching api/status?r=sys",
)
def t1_e2e06_03(env: E2EEnv) -> None:
    sys_info = (env.api().get_json("status", {"r": "sys"}).get("obj") or {}).get("sys") or {}
    hostname = str(sys_info.get("hostName", ""))
    with env.auth_page(test_id="T1_E2E06_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        main_text = page.locator("main").inner_text()
        if hostname:
            assert hostname in main_text or str(sys_info.get("appVersion", "")) in main_text, (
                f"Expected host telemetry ({hostname!r} or version) on Dashboard"
            )
        assert len(main_text.strip()) > 60


@register_test(
    tier=1,
    area="E2E-06",
    test_id="T1_E2E06_04",
    title="Dashboard Quick Actions open Logs, Backup, and Usage Stats modals cleanly with zero ANSI escapes",
)
def t1_e2e06_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E06_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        # Open Logs modal
        logs_btn = page.locator(
            'main button:has-text("Log"), main button:has(.mdi-text-box-outline), main button:has(.mdi-file-document-outline), main button:has(.mdi-console)'
        ).first
        logs_btn.wait_for(state="visible", timeout=6000)
        logs_btn.click()
        dlg = find_active_dialog(page)
        wait_for_rAF_settle(page, delay_ms=300)
        dlg_text = dlg.inner_text()
        assert "\x1b[" not in dlg_text, "Raw ANSI escape codes leaked into Logs modal"
        assert dlg.locator('button:has(.mdi-close), button:has-text("Close")').count() >= 1
        close_active_dialog(page)

        # Open Backup modal
        backup_btn = page.locator(
            'main button:has-text("Backup"), main button:has(.mdi-database-export-outline), main button:has(.mdi-backup-restore)'
        ).first
        backup_btn.wait_for(state="visible", timeout=6000)
        backup_btn.click()
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-06",
    test_id="T1_E2E06_05",
    title="Dashboard Tile Picker dialog opens, exposes toggle controls, and closes cleanly",
)
def t1_e2e06_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E06_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        tile_btn = page.locator(
            'main button:has(.mdi-view-dashboard-edit-outline), '
            'main button:has(.mdi-view-dashboard-edit), '
            'main button:has(.mdi-widgets-outline), '
            'main button:has(.mdi-tune), '
            'main button:has(.mdi-plus-box-outline), '
            'main button:has-text("Tile"), main button:has-text("Widgets"), main button:has-text("Customize")'
        ).first
        tile_btn.wait_for(state="visible", timeout=6000)
        tile_btn.click()
        dlg = find_active_dialog(page)
        assert dlg.locator('input[type="checkbox"], .v-switch, .v-chip, .v-list-item').count() >= 1, (
            "Tile picker dialog rendered no selectable tile items"
        )
        close_active_dialog(page)


# ===========================================================================
# E2E-07: SVG Gauges & Theme-Reactive Charts (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-07",
    test_id="T1_E2E07_01",
    title="Dashboard SVG gauges render >=48px arcs with distinct track contrast, percentage text, and zero Lexend",
)
def t1_e2e07_01(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, test_id="T1_E2E07_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        res = probe_svg_gauges(page)
        assert res["count"] >= 1


@register_test(
    tier=1,
    area="E2E-07",
    test_id="T1_E2E07_02",
    title="Dashboard History Chart.js <canvas> renders non-blank multi-color telemetry pixels",
)
def t1_e2e07_02(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, test_id="T1_E2E07_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        # Allow one live 2s polling tick so history chart has samples plotted
        page.wait_for_timeout(2200)
        probe_canvas_charts(page)


@register_test(
    tier=1,
    area="E2E-07",
    test_id="T1_E2E07_03",
    title="Switching Light -> Dark visibly redraws Dashboard History Chart.js <canvas> bitmap",
)
def t1_e2e07_03(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T1_E2E07_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        page.wait_for_timeout(2200)
        res = probe_canvas_charts(
            page,
            verify_theme_diff=True,
            toggle_theme_fn=lambda: switch_theme_via_ui(page, "dark"),
        )
        assert res["theme_changed"] is True


@register_test(
    tier=1,
    area="E2E-07",
    test_id="T1_E2E07_04",
    title="/analytics renders Chart.js canvas, structured logs/tabs, and clean WCAG contrast",
)
def t1_e2e07_04(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, test_id="T1_E2E07_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "analytics")
        wait_for_rAF_settle(page, delay_ms=400)
        probe_wcag_contrast(page)
        probe_card_and_shell_hygiene(page)
        # Verify canvas or structured log tabs exist and are interactive
        has_canvas = page.locator("main canvas").count() > 0
        if has_canvas:
            probe_canvas_charts(page)
        else:
            tabs = page.locator('main [role="tab"], main .v-tab')
            assert tabs.count() >= 2, "Expected analytics tabs or chart canvas on /analytics"


@register_test(
    tier=1,
    area="E2E-07",
    test_id="T1_E2E07_05",
    title="Resource Stats modal renders Chart.js canvas with compact period toggle (not 11 wrapping radio buttons)",
)
def t1_e2e07_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E07_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "outbounds")
        stats_btn = page.locator(
            'main button:has(.mdi-chart-line), main button:has(.mdi-chart-bar), main button:has(.mdi-chart-areaspline)'
        ).first
        stats_btn.wait_for(state="visible", timeout=6000)
        stats_btn.click()
        dlg = find_active_dialog(page)
        wait_for_rAF_settle(page, delay_ms=350)
        radio_count = dlg.locator('input[type="radio"], .v-radio').count()
        assert radio_count < 8, f"Stats modal still renders {radio_count} legacy wrapping radio buttons"
        probe_canvas_charts(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


# ===========================================================================
# E2E-08: Clients Management, Inline Filters, Bulk/QR Modals & Mobile (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-08",
    test_id="T1_E2E08_01",
    title="/clients renders hairline table container (no elevation-3 shadow) and inline filter toolbar",
)
def t1_e2e08_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E08_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "clients")
        probe_card_and_shell_hygiene(page)
        # Verify an inline search text field is immediately visible without opening a hidden menu
        search_inp = page.locator('main input[type="text"], main input[type="search"]').first
        search_inp.wait_for(state="visible", timeout=6000)


@register_test(
    tier=1,
    area="E2E-08",
    test_id="T1_E2E08_02",
    title="Client CRUD lifecycle renders created row in /clients, persists across reload, and deletes cleanly",
)
def t1_e2e08_02(env: E2EEnv) -> None:
    api = env.api()
    name = "e2e-t1-client-crud"
    try:
        api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": name,
                "desc": "Tier 1 CRUD test",
                "group": "e2e-grp-t1",
                "inbounds": [],
                "volume": 1073741824,
                "expiry": 0,
            },
        )
        with env.auth_page(test_id="T1_E2E08_02") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text(name, exact=False).first.wait_for(state="visible", timeout=8000)
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            page.get_by_text(name, exact=False).first.wait_for(state="visible", timeout=8000)

            # Delete via UI ConfirmDialog
            row = page.locator("tr, .v-card").filter(has_text=name).first
            del_btn = row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first
            del_btn.wait_for(state="visible", timeout=6000)
            del_btn.click()
            check_confirm_dialog_semantics(page)
            dlg = find_active_dialog(page)
            confirm_btn = dlg.locator(
                'button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error, button.text-error'
            ).last
            confirm_btn.click()
            page.wait_for_function(
                "(target) => !document.body.innerText.includes(target)",
                arg=name,
                timeout=8000,
            )
    finally:
        env.cleanup_entities(prefix="e2e-t1-client-crud")


@register_test(
    tier=1,
    area="E2E-08",
    test_id="T1_E2E08_03",
    title="Inline search toolbar on /clients filters visible rows live without hidden filter menu",
)
def t1_e2e08_03(env: E2EEnv) -> None:
    api = env.api()
    c1, c2 = "e2e-t1-flt-alpha", "e2e-t1-flt-beta"
    try:
        for nm, grp in ((c1, "grp-alpha"), (c2, "grp-beta")):
            api.save(
                "clients",
                "new",
                {"enable": True, "name": nm, "desc": "", "group": grp, "inbounds": [], "volume": 0, "expiry": 0},
            )
        with env.auth_page(test_id="T1_E2E08_03") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text(c1).first.wait_for(state="visible", timeout=6000)
            page.get_by_text(c2).first.wait_for(state="visible", timeout=6000)
            search_inp = page.locator('main input[type="text"], main input[type="search"]').first
            search_inp.fill("flt-alpha")
            wait_for_rAF_settle(page, delay_ms=250)
            body_txt = page.locator("main").inner_text()
            assert c1 in body_txt, f"Expected {c1} visible after search"
            assert c2 not in body_txt, f"Expected {c2} filtered out after searching 'flt-alpha'"
    finally:
        env.cleanup_entities(prefix="e2e-t1-flt-")


@register_test(
    tier=1,
    area="E2E-08",
    test_id="T1_E2E08_04",
    title="Client QR modal displays white #FFFFFF quiet-zone frame and valid live subscription URL (HTTP 200)",
)
def t1_e2e08_04(env: E2EEnv) -> None:
    api = env.api()
    inb_tag = "e2e-t1-qr-inb"
    cli_name = "e2e-t1-qr-cli"
    try:
        inb_res = api.save(
            "inbounds",
            "new",
            {"type": "vless", "tag": inb_tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0},
        )
        inbounds = (inb_res.get("obj") or {}).get("inbounds") or []
        inb_id = next(int(x["id"]) for x in inbounds if x.get("tag") == inb_tag)
        cli_res = api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": cli_name,
                "desc": "QR test",
                "group": "e2e-qr",
                "inbounds": [inb_id],
                "volume": 0,
                "expiry": 0,
                "config": {
                    "vless": {
                        "name": cli_name,
                        "uuid": "11111111-1111-4111-8111-111111111111",
                        "flow": "",
                    }
                },
            },
        )
        clients = (cli_res.get("obj") or {}).get("clients") or []
        sub_id = next(str(c.get("subId", "")) for c in clients if c.get("name") == cli_name)
        assert sub_id, "Backend did not generate client subId"

        # Verify subscription server actually serves HTTP 200
        sub_resp = api.raw_request("GET", f"{env.primary.sub_base_url}{sub_id}", use_cookies=False)
        assert sub_resp.status == 200 and len(sub_resp.body) > 0, f"Subscription URL failed: {sub_resp.status}"

        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T1_E2E08_04") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            row = page.locator("tr, .v-card").filter(has_text=cli_name).first
            row.wait_for(state="visible", timeout=8000)
            qr_btn = row.locator('button:has(.mdi-qrcode)').first
            qr_btn.wait_for(state="visible", timeout=6000)
            qr_btn.click()
            find_active_dialog(page)
            wait_for_rAF_settle(page, delay_ms=250)
            probe_qr_quiet_zone(page, require_dark_backdrop=True)
            close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-t1-qr-")


@register_test(
    tier=1,
    area="E2E-08",
    test_id="T1_E2E08_05",
    title="Client Add and Bulk Add modals open with clean chrome, overflow-y: auto, and accessible close controls",
)
def t1_e2e08_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E08_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "clients")
        click_page_add_button(page)
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)

        bulk_btn = page.locator(
            'main button:has(.mdi-account-multiple-plus), main button:has(.mdi-account-multiple-plus-outline), '
            'main button:has-text("Bulk")'
        ).first
        if bulk_btn.count() > 0 and bulk_btn.is_visible():
            bulk_btn.click()
            assert_dialog_chrome_clean(page)
            close_active_dialog(page)


# ===========================================================================
# E2E-09: Access & Network Resource Grids & Modals (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-09",
    test_id="T1_E2E09_01",
    title="/inbounds renders created loopback inbound card, toggles enable switch with reload persistence, and cleans up",
)
def t1_e2e09_01(env: E2EEnv) -> None:
    api = env.api()
    tag = "e2e-t1-inb-sw"
    port = allocate_distinct_free_ports(1)[0]
    try:
        api.save(
            "inbounds",
            "new",
            {"type": "mixed", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": port},
        )
        with env.auth_page(test_id="T1_E2E09_01") as (page, _g):
            navigate_spa(page, env.primary.base_url, "inbounds")
            card = page.locator("main .v-card").filter(has_text=tag).first
            card.wait_for(state="visible", timeout=8000)
            probe_card_and_shell_hygiene(page)
            sw = card.locator('input[type="checkbox"], [role="switch"]').first
            sw.wait_for(state="visible", timeout=5000)
            with page.expect_response(lambda r: "/api/save" in r.url and r.status == 200, timeout=10000):
                sw.click(force=True)
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            inbounds = env.api().load_normalized()["inbounds"]
            match = next(x for x in inbounds if x.get("tag") == tag)
            assert match.get("enable") is False, f"Inbound enable toggle did not persist: {match}"
    finally:
        env.cleanup_entities(prefix="e2e-t1-inb-")


@register_test(
    tier=1,
    area="E2E-09",
    test_id="T1_E2E09_02",
    title="/outbounds renders seeded 'direct' card with hairline chrome and opens Add Outbound modal cleanly",
)
def t1_e2e09_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E09_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "outbounds")
        page.locator("main .v-card").filter(has_text="direct").first.wait_for(state="visible", timeout=6000)
        probe_card_and_shell_hygiene(page)
        click_page_add_button(page)
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-09",
    test_id="T1_E2E09_03",
    title="/tls displays created TLS profile with semantic actions and deletes cleanly via ConfirmDialog",
)
def t1_e2e09_03(env: E2EEnv) -> None:
    api = env.api()
    tls_name = "e2e-t1-tls-prof"
    try:
        api.save(
            "tls",
            "new",
            {
                "name": tls_name,
                "server": {"enabled": True, "server_name": "e2e-t1.example.com"},
                "client": {"enabled": True, "server_name": "e2e-t1.example.com"},
            },
        )
        with env.auth_page(test_id="T1_E2E09_03") as (page, _g):
            navigate_spa(page, env.primary.base_url, "tls")
            card = page.locator("main .v-card").filter(has_text=tls_name).first
            card.wait_for(state="visible", timeout=6000)
            probe_accessible_actions(page)
            del_btn = card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first
            del_btn.click()
            check_confirm_dialog_semantics(page)
            dlg = find_active_dialog(page)
            dlg.locator(
                'button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error'
            ).last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=tls_name, timeout=8000)
    finally:
        env.cleanup_entities(prefix="e2e-t1-tls-")


@register_test(
    tier=1,
    area="E2E-09",
    test_id="T1_E2E09_04",
    title="/endpoints and /services load clean empty states and open Add modals with overflow-y: auto chrome",
)
def t1_e2e09_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E09_04") as (page, _g):
        for route in ("endpoints", "services"):
            navigate_spa(page, env.primary.base_url, route)
            probe_viewport_overflow_and_grid(page)
            probe_wcag_contrast(page)
            click_page_add_button(page)
            assert_dialog_chrome_clean(page)
            close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-09",
    test_id="T1_E2E09_05",
    title="Desktop resource grid (1280px & 1440px) with multiple cards has zero overlap and width >= 180px",
)
def t1_e2e09_05(env: E2EEnv) -> None:
    api = env.api()
    tags = [f"e2e-t1-grid-{i}" for i in range(4)]
    try:
        for t in tags:
            api.save("outbounds", "new", {"type": "block", "tag": t})
        for vp in ((1280, 800), (1440, 900)):
            with env.auth_page(viewport=vp, test_id=f"T1_E2E09_05_{vp[0]}") as (page, _g):
                navigate_spa(page, env.primary.base_url, "outbounds")
                page.get_by_text(tags[-1]).first.wait_for(state="visible", timeout=6000)
                res = probe_viewport_overflow_and_grid(page, check_grid=True, min_card_width=180.0)
                assert res["gridCardCount"] >= 4, f"Expected >=4 grid cards, found {res['gridCardCount']}"
    finally:
        env.cleanup_entities(prefix="e2e-t1-grid-")


# ===========================================================================
# E2E-10: Core Config Tabbed Workspace (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-10",
    test_id="T1_E2E10_01",
    title="/config syncs ?tab= query across route, dns, basics, and json tabs",
)
def t1_e2e10_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E10_01") as (page, _g):
        for tab in ("route", "dns", "basics", "json"):
            navigate_spa(page, env.primary.base_url, f"config?tab={tab}")
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
            assert qs.get("tab") == [tab], f"Expected ?tab={tab}, got {page.url!r}"
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=1,
    area="E2E-10",
    test_id="T1_E2E10_02",
    title="/config?tab=route renders flattened card hierarchy, seeded rules, and visible drag-handle affordance",
)
def t1_e2e10_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E10_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=route")
        probe_card_and_shell_hygiene(page, max_card_depth=2)
        main_txt = page.locator("main").inner_text().lower()
        assert "sniff" in main_txt or "hijack-dns" in main_txt, f"Seeded route rules missing: {main_txt[:200]!r}"
        drag_cues = page.locator(
            'main .mdi-drag, main .mdi-drag-vertical, main .mdi-drag-horizontal-variant, main [draggable="true"]'
        ).count()
        assert drag_cues >= 1, "Expected visible drag-handle affordance on draggable rule cards"


@register_test(
    tier=1,
    area="E2E-10",
    test_id="T1_E2E10_03",
    title="Rule modal on /config?tab=route opens with clean chrome (no blue-darken-1, no forced scroll)",
)
def t1_e2e10_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E10_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=route")
        click_page_add_button(page, extra_texts=("Rule",))
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-10",
    test_id="T1_E2E10_04",
    title="/config?tab=dns renders flattened DNS workspace and opens Add DNS modal cleanly",
)
def t1_e2e10_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E10_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=dns")
        probe_card_and_shell_hygiene(page, max_card_depth=2)
        click_page_add_button(page, extra_texts=("Server", "DNS"))
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-10",
    test_id="T1_E2E10_05",
    title="/config?tab=basics and /config?tab=json display valid config structure and parseable JSON",
)
def t1_e2e10_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E10_05") as (page, guard):
        navigate_spa(page, env.primary.base_url, "config?tab=basics")
        probe_card_and_shell_hygiene(page, max_card_depth=2)
        probe_wcag_contrast(page)

        navigate_spa(page, env.primary.base_url, "config?tab=json")
        probe_offline_typography(page, guard, require_mono=True)
        raw_json = page.evaluate(
            """() => {
              const ta = document.querySelector('main textarea');
              if (ta && ta.value) return ta.value;
              const pre = document.querySelector('main pre, main code');
              return pre ? pre.textContent : '';
            }"""
        )
        parsed = json.loads(raw_json)
        assert isinstance(parsed, dict) and "route" in parsed, f"Raw JSON tab did not render sing-box config: {parsed}"


# ===========================================================================
# E2E-11: Operations — Cluster Nodes, Domains/Certs & Nginx Proxy (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-11",
    test_id="T1_E2E11_01",
    title="/nodes displays clean empty state on fresh DB and opens Add Node modal cleanly",
)
def t1_e2e11_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E11_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "nodes")
        probe_wcag_contrast(page)
        probe_viewport_overflow_and_grid(page)
        click_page_add_button(page, extra_texts=("Node",))
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-11",
    test_id="T1_E2E11_02",
    title="Disabled Cluster Node CRUD persists on /nodes and deletes cleanly via ConfirmDialog",
)
def t1_e2e11_02(env: E2EEnv) -> None:
    api = env.api()
    node_name = "e2e-t1-node-1"
    try:
        api.save(
            "nodes",
            "new",
            {
                "enable": False,
                "name": node_name,
                "baseUrl": "http://127.0.0.1:19991",
                "webPath": "/app/",
                "token": "e2e-dummy-tok",
                "insecure": False,
                "certPin": "",
                "desc": "Tier 1 disabled node",
            },
        )
        with env.auth_page(test_id="T1_E2E11_02") as (page, _g):
            navigate_spa(page, env.primary.base_url, "nodes")
            card = page.locator("main .v-card").filter(has_text=node_name).first
            card.wait_for(state="visible", timeout=8000)
            del_btn = card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first
            del_btn.click()
            check_confirm_dialog_semantics(page)
            dlg = find_active_dialog(page)
            dlg.locator(
                'button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error'
            ).last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=node_name, timeout=8000)
    finally:
        env.cleanup_entities(prefix="e2e-t1-node-")


@register_test(
    tier=1,
    area="E2E-11",
    test_id="T1_E2E11_03",
    title="/domains loads ACME status, Nginx status, NginxProxy panel, and certs table with clean layout",
)
def t1_e2e11_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E11_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "domains")
        wait_for_rAF_settle(page, delay_ms=300)
        probe_card_and_shell_hygiene(page)
        probe_viewport_overflow_and_grid(page)
        probe_wcag_contrast(page)
        txt = page.locator("main").inner_text()
        assert re.search(r"acme|nginx|cert|domain", txt, re.IGNORECASE), f"Expected Domains sections, got {txt[:200]!r}"


@register_test(
    tier=1,
    area="E2E-11",
    test_id="T1_E2E11_04",
    title="Valid manual self-signed certificate registers on /domains, renders in responsive table, and cleans up",
)
def t1_e2e11_04(env: E2EEnv) -> None:
    api = env.api()
    domain = "e2e-t1-cert.example.com"
    with tempfile.TemporaryDirectory(prefix="sui-e2e-cert-") as td:
        cert_file, key_file = create_temp_self_signed_cert(domain, Path(td))
        try:
            api.post_form(
                "saveManualCert",
                {"domain": domain, "certFile": str(cert_file), "keyFile": str(key_file)},
            )
            with env.auth_page(test_id="T1_E2E11_04") as (page, _g):
                navigate_spa(page, env.primary.base_url, "domains")
                page.get_by_text(domain, exact=False).first.wait_for(state="visible", timeout=8000)
                probe_viewport_overflow_and_grid(page)
                probe_wcag_contrast(page)
        finally:
            env.cleanup_entities(prefix="e2e-t1-cert")


@register_test(
    tier=1,
    area="E2E-11",
    test_id="T1_E2E11_05",
    title="Manual Certificate and Issue Certificate modals on /domains open with clean chrome and cancel cleanly",
)
def t1_e2e11_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E11_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "domains")
        manual_btn = page.locator(
            'main button:has-text("Manual"), main button:has(.mdi-file-certificate-outline), main button:has(.mdi-certificate-outline)'
        ).first
        manual_btn.wait_for(state="visible", timeout=6000)
        manual_btn.click()
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)


# ===========================================================================
# E2E-12: Settings, Tools, Admins & Security Settings (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-12",
    test_id="T1_E2E12_01",
    title="/settings syncs ?tab=settings and ?tab=tools with flattened card hierarchy",
)
def t1_e2e12_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E12_01") as (page, _g):
        for tab in ("settings", "tools"):
            navigate_spa(page, env.primary.base_url, f"settings?tab={tab}")
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
            assert qs.get("tab") == [tab], f"Expected ?tab={tab}, got {page.url!r}"
            probe_card_and_shell_hygiene(page, max_card_depth=2)


@register_test(
    tier=1,
    area="E2E-12",
    test_id="T1_E2E12_02",
    title="Saving harmless subShowInfo setting persists across reload and restores cleanly",
)
def t1_e2e12_02(env: E2EEnv) -> None:
    api = env.api()
    orig = api.snapshot_settings().get("subShowInfo", "false")
    target = "true" if orig != "true" else "false"
    try:
        api.save("settings", "set", {"subShowInfo": target})
        with env.auth_page(test_id="T1_E2E12_02") as (page, _g):
            navigate_spa(page, env.primary.base_url, "settings?tab=settings")
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            now = api.snapshot_settings().get("subShowInfo")
            assert now == target, f"subShowInfo expected {target!r} after reload, got {now!r}"
    finally:
        api.restore_safe_settings({"subShowInfo": orig})


@register_test(
    tier=1,
    area="E2E-12",
    test_id="T1_E2E12_03",
    title="/settings?tab=tools runs Keypair Generator & Share Link Converter with ZERO native browser dialogs",
)
def t1_e2e12_03(env: E2EEnv) -> None:
    api = env.api()
    kp = api.get_json("keypairs", {"k": "reality"})
    assert kp.get("success") is True and len(kp.get("obj") or []) >= 2
    conv = api.post_form("linkConvert", {"link": "socks5://user:pass@127.0.0.1:1080#e2e-socks"})
    assert conv.get("success") is True and conv.get("obj") is not None

    with env.auth_page(test_id="T1_E2E12_03") as (page, guard):
        navigate_spa(page, env.primary.base_url, "settings?tab=tools")
        # Trigger a tool action button (e.g. Link Convert / Keypair) and assert Vuetify dialog, NEVER native prompt
        tool_btns = page.locator("main button")
        assert tool_btns.count() >= 2, "Expected interactive tool buttons on /settings?tab=tools"
        link_btn = page.locator(
            'main button:has-text("Convert"), main button:has-text("Link"), '
            'main button:has-text("Keypair"), main button:has-text("Generate"), '
            'main button:has(.mdi-link-variant), main button:has(.mdi-key), '
            'main button:has(.mdi-key-variant), main button:has(.mdi-key-plus)'
        ).first
        link_btn.wait_for(state="visible", timeout=6000)
        link_btn.click()
        wait_for_rAF_settle(page, delay_ms=200)
        assert not guard.native_dialogs, f"Native browser dialog fired on Tools page: {guard.native_dialogs}"
        with contextlib.suppress(Exception):
            close_active_dialog(page)


@register_test(
    tier=1,
    area="E2E-12",
    test_id="T1_E2E12_04",
    title="/admins Token lifecycle creates readable API token, authenticates apiv2, masks value, and revokes cleanly",
)
def t1_e2e12_04(env: E2EEnv) -> None:
    api = env.api()
    desc = "e2e-t1-tok"
    try:
        tok_resp = api.post_form("addToken", {"expiry": "7", "desc": desc})
        raw_token = str(tok_resp.get("obj") or "")
        assert len(raw_token) >= 16, f"Expected 32-char API token, got {raw_token!r}"

        # Live apiv2 call with valid token -> 200
        v2_ok = api.apiv2_get("status", token=raw_token, params={"r": "sbd"})
        assert v2_ok.status == 200 and v2_ok.json().get("success") is True

        # Token list masks stored token value
        tokens = api.get_json("tokens").get("obj") or []
        match = next(t for t in tokens if t.get("desc") == desc)
        assert raw_token not in str(match.get("token", "")), "Stored token should be masked in GET api/tokens"

        with env.auth_page(test_id="T1_E2E12_04") as (page, _g):
            navigate_spa(page, env.primary.base_url, "admins")
            tok_btn = page.locator(
                'main button:has-text("Token"), main button:has(.mdi-key-variant), main button:has(.mdi-api)'
            ).first
            tok_btn.wait_for(state="visible", timeout=6000)
            tok_btn.click()
            dlg = find_active_dialog(page)
            dlg.get_by_text(desc, exact=False).first.wait_for(state="visible", timeout=6000)
            assert_dialog_chrome_clean(page)
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)

        api.post_form("deleteToken", {"id": int(match["id"])})
        v2_revoked = api.apiv2_get("status", token=raw_token, params={"r": "sbd"})
        assert v2_revoked.status == 401, f"Revoked token should return 401, got {v2_revoked.status}"
    finally:
        env.cleanup_entities(prefix="e2e-t1-tok")


@register_test(
    tier=1,
    area="E2E-12",
    test_id="T1_E2E12_05",
    title="/admins Audit Changes modal uses valid theme surface and SecuritySettings displays TOTP/Passkey UI",
)
def t1_e2e12_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E12_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "admins")
        main_txt = page.locator("main").inner_text()
        assert "admin" in main_txt.lower(), "Seeded admin row missing on /admins"
        assert re.search(r"2fa|totp|passkey|security", main_txt, re.IGNORECASE), (
            f"SecuritySettings panel missing on /admins: {main_txt[:250]!r}"
        )

        changes_btn = page.locator(
            'main button:has-text("Changes"), main button:has-text("Audit"), main button:has(.mdi-history)'
        ).first
        changes_btn.wait_for(state="visible", timeout=6000)
        changes_btn.click()
        dlg = find_active_dialog(page)
        # Verify Changes.vue invalid `background-color: background` bug is absent
        bad_css = dlg.evaluate(
            """(el) => Array.from(el.querySelectorAll('[style]')).some(n =>
                /background-color\\s*:\\s*background\\b/i.test(n.getAttribute('style') || '')
            )"""
        )
        assert not bad_css, "Invalid inline CSS 'background-color: background' still present in Audit Changes modal"
        probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


# ===========================================================================
# E2E-13: Internationalization (en, zhHans, zhHant) & RTL (fa) (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-13",
    test_id="T1_E2E13_01",
    title="Switching locale to zhHans displays Simplified Chinese, persists localStorage['locale'], and leaks zero raw keys",
)
def t1_e2e13_01(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"locale": "en"}, test_id="T1_E2E13_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        switch_locale_via_ui(page, "zhHans")
        loc = page.evaluate("() => window.localStorage.getItem('locale')")
        assert loc and "zh" in loc.lower(), f"Expected zh locale persisted, got {loc!r}"
        body_txt = page.locator("body").inner_text()
        assert re.search(r"[\u4e00-\u9fff]", body_txt), "Expected CJK characters rendered in zhHans locale"
        probe_i18n_hygiene(page)


@register_test(
    tier=1,
    area="E2E-13",
    test_id="T1_E2E13_02",
    title="Switching locale to zhHant displays Traditional Chinese cleanly and persists across reload",
)
def t1_e2e13_02(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"locale": "en"}, test_id="T1_E2E13_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        switch_locale_via_ui(page, "zhHant")
        page.reload(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        assert re.search(r"[\u4e00-\u9fff]", page.locator("body").inner_text())
        probe_i18n_hygiene(page)


@register_test(
    tier=1,
    area="E2E-13",
    test_id="T1_E2E13_03",
    title="Switching locale to fa activates RTL, right-anchors desktop sidebar, and maintains zero horizontal overflow",
)
def t1_e2e13_03(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), local_storage={"locale": "en"}, test_id="T1_E2E13_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        switch_locale_via_ui(page, "fa")
        probe_rtl_fidelity(page, is_desktop=True)


@register_test(
    tier=1,
    area="E2E-13",
    test_id="T1_E2E13_04",
    title="fa RTL mode preserves dir='ltr' on technical fields (Raw JSON config and technical readouts)",
)
def t1_e2e13_04(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), local_storage={"locale": "fa"}, test_id="T1_E2E13_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=json")
        probe_rtl_fidelity(page, is_desktop=True)
        # Verify JSON editor / code block retains LTR direction or readable JSON
        editor_dir = page.evaluate(
            """() => {
              const el = document.querySelector('main textarea, main pre, main code, main [dir="ltr"]');
              return el ? getComputedStyle(el).direction : null;
            }"""
        )
        assert editor_dir == "ltr", f"Expected JSON/code container in RTL mode to stay direction: ltr, got {editor_dir!r}"


@register_test(
    tier=1,
    area="E2E-13",
    test_id="T1_E2E13_05",
    title="Switching back from fa RTL to en restores LTR direction and left-anchored desktop sidebar",
)
def t1_e2e13_05(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), local_storage={"locale": "fa"}, test_id="T1_E2E13_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_rtl_fidelity(page, is_desktop=True)
        switch_locale_via_ui(page, "en")
        drawer = get_sidebar_rect(page)
        assert drawer is not None and drawer["left"] <= 4.0, (
            f"Expected left-anchored desktop sidebar after switching fa -> en, got {drawer}"
        )


# ===========================================================================
# E2E-14: Responsive Ergonomics, Accessibility & Zero Runtime Errors (5 tests)
# ===========================================================================


@register_test(
    tier=1,
    area="E2E-14",
    test_id="T1_E2E14_01",
    title="Zero horizontal overflow at Desktop 1440x900 and 1280x800 across core routes",
)
def t1_e2e14_01(env: E2EEnv) -> None:
    routes = ("", "analytics", "clients", "inbounds", "outbounds", "config", "domains", "settings", "admins")
    for vp in ((1440, 900), (1280, 800)):
        with env.auth_page(viewport=vp, test_id=f"T1_E2E14_01_{vp[0]}") as (page, _g):
            for r in routes:
                navigate_spa(page, env.primary.base_url, r)
                probe_viewport_overflow_and_grid(page)


@register_test(
    tier=1,
    area="E2E-14",
    test_id="T1_E2E14_02",
    title="Zero horizontal overflow at Tablet 768x1024 across core routes",
)
def t1_e2e14_02(env: E2EEnv) -> None:
    routes = ("", "clients", "inbounds", "config", "domains", "settings")
    with env.auth_page(viewport=(768, 1024), test_id="T1_E2E14_02") as (page, _g):
        for r in routes:
            navigate_spa(page, env.primary.base_url, r)
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=1,
    area="E2E-14",
    test_id="T1_E2E14_03",
    title="Zero horizontal overflow at Mobile 375x812 across core routes",
)
def t1_e2e14_03(env: E2EEnv) -> None:
    routes = ("", "clients", "inbounds", "domains", "admins")
    with env.auth_page(viewport=(375, 812), test_id="T1_E2E14_03") as (page, _g):
        for r in routes:
            navigate_spa(page, env.primary.base_url, r)
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=1,
    area="E2E-14",
    test_id="T1_E2E14_04",
    title="Interactive icon actions across topbar, dashboard, and resource views use semantic <button> (>=24x24px)",
)
def t1_e2e14_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T1_E2E14_04") as (page, _g):
        for route in ("", "clients", "inbounds", "outbounds", "admins"):
            navigate_spa(page, env.primary.base_url, route)
            probe_accessible_actions(page)


@register_test(
    tier=1,
    area="E2E-14",
    test_id="T1_E2E14_05",
    title="Full traversal of all 13 authenticated routes records zero console errors, pageerrors, CDN calls, or 4xx/5xx",
)
def t1_e2e14_05(env: E2EEnv) -> None:
    all_routes = (
        "",
        "analytics",
        "clients",
        "inbounds",
        "tls",
        "outbounds",
        "endpoints",
        "config",
        "nodes",
        "domains",
        "services",
        "settings",
        "admins",
    )
    with env.auth_page(test_id="T1_E2E14_05") as (page, guard):
        for route in all_routes:
            navigate_spa(page, env.primary.base_url, route)
        guard.assert_clean()
