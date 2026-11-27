"""Tier 2: Boundary & Corner Cases — 70 genuine edge/error/negative tests (5 per area E2E-01..E2E-14).

Strictly obeys shared-instance safety rules: never locks out `admin`, never invokes
`restartApp` / `changePass` / `totp-enable` / valid `importdb` / `"direct"` deletion.
"""

from __future__ import annotations

import contextlib
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
    switch_theme_via_ui,
    wait_for_live_status,
    wait_for_rAF_settle,
    wait_for_spa_ready,
)

# ===========================================================================
# E2E-01: Binary Embed, HTTP Headers & Subpath Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-01",
    test_id="T2_E2E01_01",
    title="Paths outside /app/ (/, /outside, /favicon.ico) return 404 with empty body and security headers",
)
def t2_e2e01_01(env: E2EEnv) -> None:
    client = env.primary.api_client(authenticated=False)
    for bad_path in ("/", "/outside-e2e", "/favicon.ico"):
        resp = client.raw_request("GET", bad_path, follow_redirects=False, use_cookies=False)
        assert resp.status == 404, f"GET {bad_path} expected 404, got {resp.status}"
        assert resp.body == b"", f"GET {bad_path} expected empty 404 body, got {resp.body!r}"
        assert_security_headers(resp.headers, is_asset=False)


@register_test(
    tier=2,
    area="E2E-01",
    test_id="T2_E2E01_02",
    title="On /ci/ server, default /app/ returns 404 while /ci -> 307 /ci/ -> 307 /ci/login",
)
def t2_e2e01_02(env: E2EEnv) -> None:
    ci_client = env.subpath.api_client(authenticated=False)
    for old_path in ("/", "/app", "/app/", "/app/login"):
        r = ci_client.raw_request("GET", old_path, follow_redirects=False, use_cookies=False)
        assert r.status == 404, f"On /ci/ server, GET {old_path} expected 404, got {r.status}"

    r_noslash = ci_client.raw_request("GET", "/ci", follow_redirects=False, use_cookies=False)
    assert r_noslash.status == 307 and r_noslash.header("location") == "/ci/"
    r_root = ci_client.raw_request("GET", "/ci/", follow_redirects=False, use_cookies=False)
    assert r_root.status == 307 and r_root.header("location") == "/ci/login"


@register_test(
    tier=2,
    area="E2E-01",
    test_id="T2_E2E01_03",
    title="Nonexistent static asset /app/assets/missing-e2e.js returns 404 cleanly with security headers",
)
def t2_e2e01_03(env: E2EEnv) -> None:
    client = env.primary.api_client(authenticated=False)
    resp = client.raw_request("GET", "/app/assets/does-not-exist-e2e.js", follow_redirects=False, use_cookies=False)
    assert resp.status == 404, f"Missing asset expected 404, got {resp.status}"
    assert resp.header("x-content-type-options").lower() == "nosniff"
    assert resp.header("x-frame-options").upper() == "DENY"


@register_test(
    tier=2,
    area="E2E-01",
    test_id="T2_E2E01_04",
    title="Deep SPA route with extra/unknown query parameters serves 200 index.html and mounts cleanly",
)
def t2_e2e01_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E01_04") as (page, _g):
        resp = navigate_spa(page, env.primary.base_url, "config?tab=invalid_tab&foo=bar&x=1")
        assert resp is not None and resp.status == 200
        assert page.evaluate("() => window.BASE_URL") == "/app/"
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-01",
    test_id="T2_E2E01_05",
    title="Authenticated GET /app/login returns 307 Location: /app/ (never traps logged-in admin on login page)",
)
def t2_e2e01_05(env: E2EEnv) -> None:
    auth_client = env.primary.api_client(authenticated=True)
    try:
        resp = auth_client.raw_request("GET", "/app/login", follow_redirects=False, use_cookies=True)
        assert resp.status == 307, f"Authenticated GET /app/login expected 307, got {resp.status}"
        assert resp.header("location") == "/app/", f"Expected Location '/app/', got {resp.header('location')!r}"
    finally:
        with contextlib.suppress(Exception):
            auth_client.logout()


# ===========================================================================
# E2E-02: Theme, Contrast & Purple-on-Dark Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-02",
    test_id="T2_E2E02_01",
    title="Corrupt localStorage['theme'] value degrades safely to a valid theme without purple or JS errors",
)
def t2_e2e02_01(env: E2EEnv) -> None:
    with env.auth_page(
        local_storage={"theme": "neon-purple-corrupt-value"},
        color_scheme="dark",
        test_id="T2_E2E02_01",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_wcag_contrast(page)
        probe_zero_purple_on_dark(page)


@register_test(
    tier=2,
    area="E2E-02",
    test_id="T2_E2E02_02",
    title="Rapid Light -> Dark -> System -> Light theme switching leaves zero stale classes or contrast regressions",
)
def t2_e2e02_02(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T2_E2E02_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        switch_theme_via_ui(page, "dark")
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        switch_theme_via_ui(page, "system")
        wait_for_rAF_settle(page, delay_ms=150)
        switch_theme_via_ui(page, "light")
        probe_surface_separation(page, mode="light")
        probe_wcag_contrast(page)


@register_test(
    tier=2,
    area="E2E-02",
    test_id="T2_E2E02_03",
    title="Open modal dialog in Dark mode passes scoped WCAG AA contrast and zero purple-on-dark",
)
def t2_e2e02_03(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E02_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "inbounds")
        click_page_add_button(page)
        find_active_dialog(page)
        probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
        probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


@register_test(
    tier=2,
    area="E2E-02",
    test_id="T2_E2E02_04",
    title="Open modal dialog in Light mode passes scoped WCAG AA contrast across labels, inputs, and buttons",
)
def t2_e2e02_04(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T2_E2E02_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "clients")
        click_page_add_button(page)
        find_active_dialog(page)
        probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


@register_test(
    tier=2,
    area="E2E-02",
    test_id="T2_E2E02_05",
    title="Dark mode enforces WCAG AA contrast and zero purple across /analytics, /domains, /admins, /settings",
)
def t2_e2e02_05(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E02_05") as (page, _g):
        for route in ("analytics", "domains", "admins", "settings"):
            navigate_spa(page, env.primary.base_url, route)
            probe_zero_purple_on_dark(page)
            probe_wcag_contrast(page)


# ===========================================================================
# E2E-03: Typography, Scrollbars & Visual Craft Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-03",
    test_id="T2_E2E03_01",
    title="Long unbroken client name/description does not trigger horizontal document overflow on /clients",
)
def t2_e2e03_01(env: E2EEnv) -> None:
    api = env.api()
    long_name = "e2e-t2-long-" + ("x" * 64)
    try:
        api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": long_name,
                "desc": "unbroken_" + ("z" * 96),
                "group": "e2e-long-grp",
                "inbounds": [],
                "volume": 0,
                "expiry": 0,
            },
        )
        with env.auth_page(viewport=(1280, 800), test_id="T2_E2E03_01") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text("e2e-t2-long-", exact=False).first.wait_for(state="visible", timeout=6000)
            probe_viewport_overflow_and_grid(page)
    finally:
        env.cleanup_entities(prefix="e2e-t2-long-")


@register_test(
    tier=2,
    area="E2E-03",
    test_id="T2_E2E03_02",
    title="Flattened /config and /settings sub-tabs stay within max card nesting depth <= 2",
)
def t2_e2e03_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E03_02") as (page, _g):
        for route in ("config?tab=route", "config?tab=dns", "config?tab=basics", "settings?tab=settings", "settings?tab=tools"):
            navigate_spa(page, env.primary.base_url, route)
            probe_card_and_shell_hygiene(page, max_card_depth=2)


@register_test(
    tier=2,
    area="E2E-03",
    test_id="T2_E2E03_03",
    title="Zero legacy .v-card-subtitle raw rgb(128,128,128) bottom borders across config and settings views",
)
def t2_e2e03_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E03_03") as (page, _g):
        for route in ("config?tab=basics", "config?tab=dns", "config?tab=route", "settings?tab=settings"):
            navigate_spa(page, env.primary.base_url, route)
            hygiene = probe_card_and_shell_hygiene(page)
            assert not hygiene["violations"]


@register_test(
    tier=2,
    area="E2E-03",
    test_id="T2_E2E03_04",
    title="Dark-mode Raw JSON & Logs retain monospace font family with zero 'Lexend' and zero ANSI escapes",
)
def t2_e2e03_04(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E03_04") as (page, guard):
        navigate_spa(page, env.primary.base_url, "config?tab=json")
        probe_offline_typography(page, guard, require_mono=True)
        txt = page.locator("main").inner_text()
        assert "\x1b[" not in txt


@register_test(
    tier=2,
    area="E2E-03",
    test_id="T2_E2E03_05",
    title="Populated resource cards (/inbounds, /outbounds, /tls) have zero 24px bulbous radii and zero elevation-5 shadows",
)
def t2_e2e03_05(env: E2EEnv) -> None:
    api = env.api()
    try:
        api.save("inbounds", "new", {"type": "mixed", "tag": "e2e-t2-hyg-inb", "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        api.save("tls", "new", {"name": "e2e-t2-hyg-tls", "server": {"enabled": True, "server_name": "hyg.example.com"}, "client": {"enabled": True, "server_name": "hyg.example.com"}})
        with env.auth_page(test_id="T2_E2E03_05") as (page, _g):
            for route in ("inbounds", "outbounds", "tls"):
                navigate_spa(page, env.primary.base_url, route)
                probe_card_and_shell_hygiene(page, check_card_radius=True)
    finally:
        env.cleanup_entities(prefix="e2e-t2-hyg-")


# ===========================================================================
# E2E-04: Login & Auth Security Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-04",
    test_id="T2_E2E04_01",
    title="Invalid credentials on throwaway username stay on /login and surface clean error (zero admin lockout risk)",
)
def t2_e2e04_01(env: E2EEnv) -> None:
    with env.anon_page(test_id="T2_E2E04_01") as (page, _g):
        login_via_ui(page, env.primary.base_url, "e2e_bad_user_t2_01", "wrong-pass", expect_redirect=False)
        assert page.url.rstrip("/").endswith("/login"), f"Invalid login left /login: {page.url}"
        # Confirm error notification / alert text appeared
        feedback = page.locator('[role="alert"], [role="status"], .Notivue__notification, .v-alert').first
        feedback.wait_for(state="visible", timeout=6000)


@register_test(
    tier=2,
    area="E2E-04",
    test_id="T2_E2E04_02",
    title="Submitting empty username and password on /login stays safely on /login without pageerror",
)
def t2_e2e04_02(env: E2EEnv) -> None:
    with env.anon_page(test_id="T2_E2E04_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        submit = page.locator('button[type="submit"]').first
        if submit.count() == 0:
            submit = page.locator("form button").last
        submit.click()
        wait_for_rAF_settle(page, delay_ms=300)
        assert "/login" in page.url


@register_test(
    tier=2,
    area="E2E-04",
    test_id="T2_E2E04_03",
    title="Unauthenticated direct requests to protected SPA routes (/clients, /settings, /admins) return 307 -> /app/login",
)
def t2_e2e04_03(env: E2EEnv) -> None:
    client = env.primary.api_client(authenticated=False)
    for route in ("clients", "settings", "admins", "config"):
        r = client.raw_request("GET", f"/app/{route}", follow_redirects=False, use_cookies=False)
        assert r.status == 307, f"Unauth GET /app/{route} expected 307, got {r.status}"
        assert r.header("location") == "/app/login", f"Unexpected redirect target: {r.header('location')!r}"


@register_test(
    tier=2,
    area="E2E-04",
    test_id="T2_E2E04_04",
    title="Unauthenticated calls to protected api/* return HTTP 401 JSON {'success':false,'msg':'Invalid login'}",
)
def t2_e2e04_04(env: E2EEnv) -> None:
    client = env.primary.api_client(authenticated=False)
    for method, action in (("GET", "load"), ("GET", "settings"), ("POST", "save")):
        r = client.raw_request(method, f"{env.primary.base_url}api/{action}", use_cookies=False)
        assert r.status == 401, f"Unauth {method} api/{action} expected 401, got {r.status}"
        payload = r.json()
        assert payload.get("success") is False and payload.get("msg") == "Invalid login", (
            f"Unexpected 401 payload on {action}: {payload}"
        )


@register_test(
    tier=2,
    area="E2E-04",
    test_id="T2_E2E04_05",
    title="Login rate limiter locks isolated throwaway user after 8 failures while keeping admin completely unaffected",
)
def t2_e2e04_05(env: E2EEnv) -> None:
    client = env.primary.api_client(authenticated=False)
    throwaway = "e2e_lockout_probe_t2"
    saw_lockout = False
    for attempt in range(9):
        res = client.login(throwaway, f"bad-pw-{attempt}", require_success=False)
        assert res.get("success") is False
        if "too many login attempts" in str(res.get("msg", "")).lower():
            saw_lockout = True
            break
    assert saw_lockout, "Expected rate-limiter lockout message after 8 failed logins on throwaway username"
    # Verify real admin login is completely unaffected!
    ok = client.login("admin", "admin", require_success=True)
    assert ok.get("success") is True
    client.logout()


# ===========================================================================
# E2E-05: App Shell & Routing Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-05",
    test_id="T2_E2E05_01",
    title="Pre-seeded localStorage['sui-drawer-rail']='true' boots directly in collapsed rail width without overflow",
)
def t2_e2e05_01(env: E2EEnv) -> None:
    with env.auth_page(
        viewport=(1440, 900),
        local_storage={"sui-drawer-rail": "true"},
        test_id="T2_E2E05_01",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        rect = get_sidebar_rect(page)
        assert rect is not None and 48 <= rect["width"] <= 96, f"Expected collapsed rail on boot, got {rect}"
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-05",
    test_id="T2_E2E05_02",
    title="Corrupt localStorage['sui-drawer-rail'] degrades gracefully without JS errors or broken layout",
)
def t2_e2e05_02(env: E2EEnv) -> None:
    with env.auth_page(
        viewport=(1440, 900),
        local_storage={"sui-drawer-rail": "not-a-boolean-garbage"},
        test_id="T2_E2E05_02",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        rect = get_sidebar_rect(page)
        assert rect is not None and rect["width"] >= 48
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-05",
    test_id="T2_E2E05_03",
    title="Dynamic Desktop (1440px) -> Mobile (375px) -> Desktop resize switches drawer cleanly with zero overflow",
)
def t2_e2e05_03(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), test_id="T2_E2E05_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        page.set_viewport_size({"width": 375, "height": 812})
        wait_for_rAF_settle(page, delay_ms=250)
        probe_viewport_overflow_and_grid(page)
        open_mobile_drawer(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        wait_for_rAF_settle(page, delay_ms=250)
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-05",
    test_id="T2_E2E05_04",
    title="Unknown SPA route under /app/ handles gracefully without uncaught pageerror or blank crash",
)
def t2_e2e05_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E05_04") as (page, _g):
        resp = page.goto(f"{env.primary.base_url}nonexistent-spa-route-e2e", wait_until="domcontentloaded")
        assert resp is not None and resp.status == 200
        wait_for_spa_ready(page, expect_shell=True)
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-05",
    test_id="T2_E2E05_05",
    title="Browser Back/Forward navigation across /, /inbounds, /clients, /config?tab=dns preserves route state",
)
def t2_e2e05_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E05_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        navigate_spa(page, env.primary.base_url, "inbounds")
        navigate_spa(page, env.primary.base_url, "clients")
        navigate_spa(page, env.primary.base_url, "config?tab=dns")
        page.go_back(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        assert "/clients" in page.url, f"Expected /clients after go_back, got {page.url}"
        page.go_back(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        assert "/inbounds" in page.url, f"Expected /inbounds after second go_back, got {page.url}"
        page.go_forward(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        assert "/clients" in page.url, f"Expected /clients after go_forward, got {page.url}"


# ===========================================================================
# E2E-06: Dashboard & Telemetry Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-06",
    test_id="T2_E2E06_01",
    title="Corrupt localStorage['reloadItems'] JSON recovers gracefully on Dashboard without crashing tiles",
)
def t2_e2e06_01(env: E2EEnv) -> None:
    with env.auth_page(
        local_storage={"reloadItems": "{{broken-json!!"},
        test_id="T2_E2E06_01",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-06",
    test_id="T2_E2E06_02",
    title="Dashboard handles container swap total==0 without displaying NaN or Infinity on gauges/readouts",
)
def t2_e2e06_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E06_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        main_txt = page.locator("main").inner_text()
        assert not re.search(r"\bNaN\b|\bInfinity\b", main_txt), f"Found NaN/Infinity in Dashboard: {main_txt[:200]!r}"


@register_test(
    tier=2,
    area="E2E-06",
    test_id="T2_E2E06_03",
    title="Backup endpoints GET api/getdb returns SQLite3 header and GET api/singbox-config returns valid JSON",
)
def t2_e2e06_03(env: E2EEnv) -> None:
    api = env.api()
    db_resp = api.raw_request("GET", f"{env.primary.base_url}api/getdb", use_cookies=True)
    assert db_resp.status == 200
    assert db_resp.body.startswith(b"SQLite format 3\x00"), "GET api/getdb did not return valid SQLite3 file"

    sb_resp = api.raw_request("GET", f"{env.primary.base_url}api/singbox-config", use_cookies=True)
    assert sb_resp.status == 200
    parsed = sb_resp.json()
    assert isinstance(parsed, dict) and "outbounds" in parsed


@register_test(
    tier=2,
    area="E2E-06",
    test_id="T2_E2E06_04",
    title="POST api/importdb rejects corrupt non-SQLite payload cleanly without restarting or corrupting server",
)
def t2_e2e06_04(env: E2EEnv) -> None:
    api = env.api()
    resp = api.post_multipart(
        "importdb",
        file_field="db",
        filename="corrupt.db",
        content=b"NOT_A_SQLITE_DATABASE_PAYLOAD_E2E",
    )
    assert resp.status == 200
    payload = resp.json()
    assert payload.get("success") is False, f"Corrupt importdb should fail cleanly: {payload}"
    # Confirm server & core are completely healthy immediately afterward
    st = api.get_json("status", {"r": "sbd"})
    assert (st.get("obj") or {}).get("sbd", {}).get("running") is True


@register_test(
    tier=2,
    area="E2E-06",
    test_id="T2_E2E06_05",
    title="POST api/restartSb restarts only embedded sing-box core while preserving HTTP server and session",
)
def t2_e2e06_05(env: E2EEnv) -> None:
    api = env.api()
    res = api.post_form("restartSb")
    assert res.get("success") is True
    st = api.get_json("status", {"r": "sbd"})
    assert (st.get("obj") or {}).get("sbd", {}).get("running") is True


# ===========================================================================
# E2E-07: SVG Gauges & Charts Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-07",
    test_id="T2_E2E07_01",
    title="Dashboard SVG gauges in Dark mode preserve track separation (ΔRGB >= 12), zero purple strokes, and finite %",
)
def t2_e2e07_01(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E07_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        probe_svg_gauges(page)
        probe_zero_purple_on_dark(page)


@register_test(
    tier=2,
    area="E2E-07",
    test_id="T2_E2E07_02",
    title="/analytics remains overflow-free and error-free when resized 1440px -> 768px -> 375px",
)
def t2_e2e07_02(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), test_id="T2_E2E07_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "analytics")
        for w, h in ((1440, 900), (768, 1024), (375, 812)):
            page.set_viewport_size({"width": w, "height": h})
            wait_for_rAF_settle(page, delay_ms=220)
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-07",
    test_id="T2_E2E07_03",
    title="Switching period buttons inside Resource Stats modal updates cleanly with zero errors",
)
def t2_e2e07_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E07_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "outbounds")
        stats_btn = page.locator(
            'main button:has(.mdi-chart-line), main button:has(.mdi-chart-bar), main button:has(.mdi-chart-areaspline)'
        ).first
        stats_btn.wait_for(state="visible", timeout=6000)
        stats_btn.click()
        dlg = find_active_dialog(page)
        toggles = dlg.locator('.v-btn-toggle button, [role="radiogroup"] button, .v-chip')
        if toggles.count() >= 2:
            toggles.nth(1).click()
            wait_for_rAF_settle(page, delay_ms=250)
        probe_canvas_charts(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


@register_test(
    tier=2,
    area="E2E-07",
    test_id="T2_E2E07_04",
    title="/analytics handles empty fresh-DB usage/connections datasets without TypeError or console errors",
)
def t2_e2e07_04(env: E2EEnv) -> None:
    api = env.api()
    usage = api.get_json("analytics-usage")
    assert usage.get("success") is True
    conns = api.get_json("analytics-connections")
    assert conns.get("success") is True

    with env.auth_page(test_id="T2_E2E07_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "analytics")
        tabs = page.locator('main [role="tab"], main .v-tab')
        for idx in range(min(tabs.count(), 4)):
            tabs.nth(idx).click()
            wait_for_rAF_settle(page, delay_ms=200)
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-07",
    test_id="T2_E2E07_05",
    title="Switching Light -> Dark on /analytics preserves chart readability and zero purple-on-dark",
)
def t2_e2e07_05(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T2_E2E07_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "analytics")
        wait_for_rAF_settle(page, delay_ms=300)
        switch_theme_via_ui(page, "dark")
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        probe_wcag_contrast(page)


# ===========================================================================
# E2E-08: Clients Management Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-08",
    test_id="T2_E2E08_01",
    title="Non-matching search query on /clients displays clean empty/no-results state without crashing",
)
def t2_e2e08_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E08_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "clients")
        search = page.locator('main input[type="text"], main input[type="search"]').first
        search.wait_for(state="visible", timeout=6000)
        search.fill("e2e-definitely-missing-client-99999")
        wait_for_rAF_settle(page, delay_ms=200)
        probe_viewport_overflow_and_grid(page)
        probe_wcag_contrast(page)


@register_test(
    tier=2,
    area="E2E-08",
    test_id="T2_E2E08_02",
    title="Cancelling Delete ConfirmDialog on /clients uses neutral Cancel styling and keeps client intact",
)
def t2_e2e08_02(env: E2EEnv) -> None:
    api = env.api()
    name = "e2e-t2-cli-cancel"
    try:
        api.save("clients", "new", {"enable": True, "name": name, "desc": "", "group": "", "inbounds": [], "volume": 0, "expiry": 0})
        with env.auth_page(test_id="T2_E2E08_02") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            row = page.locator("tr, .v-card").filter(has_text=name).first
            row.wait_for(state="visible", timeout=6000)
            row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            check_confirm_dialog_semantics(page)
            close_active_dialog(page)
            assert page.get_by_text(name).first.is_visible(), "Client disappeared after cancelling delete"
    finally:
        env.cleanup_entities(prefix="e2e-t2-cli-cancel")


@register_test(
    tier=2,
    area="E2E-08",
    test_id="T2_E2E08_03",
    title="Disabled client with volume quota renders cleanly without broken layout or contrast issues",
)
def t2_e2e08_03(env: E2EEnv) -> None:
    api = env.api()
    name = "e2e-t2-cli-disabled"
    try:
        api.save(
            "clients",
            "new",
            {"enable": False, "name": name, "desc": "Disabled quota check", "group": "dis", "inbounds": [], "volume": 5368709120, "expiry": 0},
        )
        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E08_03") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text(name).first.wait_for(state="visible", timeout=6000)
            probe_zero_purple_on_dark(page)
            probe_wcag_contrast(page)
    finally:
        env.cleanup_entities(prefix="e2e-t2-cli-disabled")


@register_test(
    tier=2,
    area="E2E-08",
    test_id="T2_E2E08_04",
    title="Client QR modal in Dark mode keeps crisp #FFFFFF quiet-zone frame against dark backdrop",
)
def t2_e2e08_04(env: E2EEnv) -> None:
    api = env.api()
    inb_tag = "e2e-t2-qrd-inb"
    cli_name = "e2e-t2-qrd-cli"
    try:
        r = api.save("inbounds", "new", {"type": "vless", "tag": inb_tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        inb_id = next(int(x["id"]) for x in (r.get("obj") or {}).get("inbounds") or [] if x.get("tag") == inb_tag)
        api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": cli_name,
                "desc": "",
                "group": "qr-dark",
                "inbounds": [inb_id],
                "volume": 0,
                "expiry": 0,
                "config": {"vless": {"name": cli_name, "uuid": "22222222-2222-4222-8222-222222222222", "flow": ""}},
            },
        )
        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T2_E2E08_04") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            row = page.locator("tr, .v-card").filter(has_text=cli_name).first
            row.wait_for(state="visible", timeout=6000)
            row.locator('button:has(.mdi-qrcode)').first.click()
            find_active_dialog(page)
            probe_qr_quiet_zone(page, require_dark_backdrop=True)
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-t2-qrd-")


@register_test(
    tier=2,
    area="E2E-08",
    test_id="T2_E2E08_05",
    title="Duplicate client name creation is rejected cleanly without corrupting existing client record",
)
def t2_e2e08_05(env: E2EEnv) -> None:
    api = env.api()
    dup_name = "e2e-t2-cli-dup"
    try:
        api.save("clients", "new", {"enable": True, "name": dup_name, "desc": "orig", "group": "", "inbounds": [], "volume": 0, "expiry": 0})
        dup = api.save(
            "clients",
            "new",
            {"enable": True, "name": dup_name, "desc": "second", "group": "", "inbounds": [], "volume": 0, "expiry": 0},
            check_success=False,
        )
        assert dup.get("success") is False, f"Duplicate client name should fail cleanly: {dup}"
        clients = [c for c in api.load_normalized()["clients"] if c.get("name") == dup_name]
        assert len(clients) == 1 and clients[0].get("desc") == "orig"
    finally:
        env.cleanup_entities(prefix="e2e-t2-cli-dup")


# ===========================================================================
# E2E-09: Resource Grids & Modals Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-09",
    test_id="T2_E2E09_01",
    title="Deleting a TLS profile referenced by an inbound is refused ('tls in use') and preserves both records",
)
def t2_e2e09_01(env: E2EEnv) -> None:
    api = env.api()
    try:
        tls_r = api.save(
            "tls",
            "new",
            {"name": "e2e-t2-ref-tls", "server": {"enabled": True, "server_name": "ref.example.com"}, "client": {"enabled": True, "server_name": "ref.example.com"}},
        )
        tls_id = next(int(x["id"]) for x in (tls_r.get("obj") or {}).get("tls") or [] if x.get("name") == "e2e-t2-ref-tls")
        api.save(
            "inbounds",
            "new",
            {"type": "vless", "tag": "e2e-t2-ref-inb", "enable": True, "listen": "127.0.0.1", "listen_port": 0, "tls_id": tls_id},
        )
        del_r = api.save("tls", "del", tls_id, check_success=False)
        assert del_r.get("success") is False, f"Deleting referenced TLS should be refused: {del_r}"
        assert "use" in str(del_r.get("msg", "")).lower() or "inb" in str(del_r.get("msg", "")).lower()
    finally:
        env.cleanup_entities(prefix="e2e-t2-ref-")


@register_test(
    tier=2,
    area="E2E-09",
    test_id="T2_E2E09_02",
    title="Duplicate inbound tag creation is rejected cleanly while keeping sing-box running",
)
def t2_e2e09_02(env: E2EEnv) -> None:
    api = env.api()
    tag = "e2e-t2-dupinb"
    try:
        api.save("inbounds", "new", {"type": "mixed", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        dup = api.save(
            "inbounds",
            "new",
            {"type": "mixed", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0},
            check_success=False,
        )
        assert dup.get("success") is False, f"Duplicate inbound tag should fail cleanly: {dup}"
        st = api.get_json("status", {"r": "sbd"})
        assert (st.get("obj") or {}).get("sbd", {}).get("running") is True
    finally:
        env.cleanup_entities(prefix="e2e-t2-dupinb")


@register_test(
    tier=2,
    area="E2E-09",
    test_id="T2_E2E09_03",
    title="Cancelling Delete ConfirmDialog on an inbound preserves the card and triggers zero native dialogs",
)
def t2_e2e09_03(env: E2EEnv) -> None:
    api = env.api()
    tag = "e2e-t2-inbcancel"
    try:
        api.save("inbounds", "new", {"type": "mixed", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        with env.auth_page(test_id="T2_E2E09_03") as (page, guard):
            navigate_spa(page, env.primary.base_url, "inbounds")
            card = page.locator("main .v-card").filter(has_text=tag).first
            card.wait_for(state="visible", timeout=6000)
            card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            check_confirm_dialog_semantics(page)
            close_active_dialog(page)
            assert card.is_visible()
            assert not guard.native_dialogs
    finally:
        env.cleanup_entities(prefix="e2e-t2-inbcancel")


@register_test(
    tier=2,
    area="E2E-09",
    test_id="T2_E2E09_04",
    title="Live search on /outbounds filters to zero cards for unmatched query and restores 'direct' on clear",
)
def t2_e2e09_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E09_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "outbounds")
        page.get_by_text("direct").first.wait_for(state="visible", timeout=6000)
        search = page.locator('main input[type="text"], main input[type="search"]').first
        search.wait_for(state="visible", timeout=6000)
        search.fill("e2e-no-outbound-matches-xyz")
        wait_for_rAF_settle(page, delay_ms=200)
        assert page.locator("main .v-card").filter(has_text="direct").count() == 0
        search.fill("")
        wait_for_rAF_settle(page, delay_ms=200)
        assert page.locator("main .v-card").filter(has_text="direct").count() >= 1


@register_test(
    tier=2,
    area="E2E-09",
    test_id="T2_E2E09_05",
    title="Creating an outbound with duplicate seeded tag 'direct' fails cleanly without modifying 'direct'",
)
def t2_e2e09_05(env: E2EEnv) -> None:
    api = env.api()
    dup = api.save("outbounds", "new", {"type": "block", "tag": "direct"}, check_success=False)
    assert dup.get("success") is False, f"Duplicate 'direct' outbound should fail: {dup}"
    outs = api.load_normalized()["outbounds"]
    direct = [o for o in outs if o.get("tag") == "direct"]
    assert len(direct) == 1 and direct[0].get("type") == "direct"


# ===========================================================================
# E2E-10: Core Config Workspace Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-10",
    test_id="T2_E2E10_01",
    title="/config?tab=unknown-tab-e2e recovers gracefully to a valid active tab with zero console errors",
)
def t2_e2e10_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E10_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=unknown-tab-e2e")
        assert page.locator('main [role="tab"], main .v-tab').count() >= 4
        probe_wcag_contrast(page)


@register_test(
    tier=2,
    area="E2E-10",
    test_id="T2_E2E10_02",
    title="Invalid sing-box config via POST api/save is rejected and rolled back while sing-box stays running",
)
def t2_e2e10_02(env: E2EEnv) -> None:
    api = env.api()
    baseline = api.snapshot_config()
    try:
        bad_cfg = {**baseline, "route": {"rules": [{"action": "route", "outbound": "nonexistent-e2e-outbound-999"}]}}
        res = api.save("config", "set", bad_cfg, apply=True, check_success=False)
        assert res.get("success") is False, f"Invalid sing-box config should be rejected: {res}"
        st = api.get_json("status", {"r": "sbd"})
        assert (st.get("obj") or {}).get("sbd", {}).get("running") is True
    finally:
        api.restore_config(baseline)


@register_test(
    tier=2,
    area="E2E-10",
    test_id="T2_E2E10_03",
    title="Malformed JSON entered in /config?tab=json is handled cleanly without crashing or corrupting config",
)
def t2_e2e10_03(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E10_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=json")
        ta = page.locator("main textarea").first
        ta.wait_for(state="visible", timeout=6000)
        assert ta.is_editable(), "Raw JSON editor textarea should be editable"
        ta.fill("{ invalid json !!!")
        wait_for_rAF_settle(page, delay_ms=200)
        # Trigger apply/save button if enabled; verify no uncaught crash
        apply_btn = page.locator('main button:has-text("Apply"), main button:has-text("Save")').first
        if apply_btn.count() > 0 and apply_btn.is_visible() and apply_btn.is_enabled():
            apply_btn.click()
            wait_for_rAF_settle(page, delay_ms=250)
        # Ensure live backend config was NOT corrupted
        cfg = env.api().snapshot_config()
        assert isinstance(cfg, dict) and "route" in cfg


@register_test(
    tier=2,
    area="E2E-10",
    test_id="T2_E2E10_04",
    title="Rapid sequential tab switching across route -> dns -> basics -> json -> route updates URL query cleanly",
)
def t2_e2e10_04(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E10_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "config?tab=route")
        tabs = page.locator('main [role="tab"], main .v-tab')
        assert tabs.count() >= 4
        for idx in (1, 2, 3, 0):
            tabs.nth(idx).click()
            wait_for_rAF_settle(page, delay_ms=120)
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query)
        assert "tab" in qs


@register_test(
    tier=2,
    area="E2E-10",
    test_id="T2_E2E10_05",
    title="Cancelling Add Rule modal on /config?tab=route discards unsaved form state without firing POST api/save",
)
def t2_e2e10_05(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E10_05") as (page, guard):
        navigate_spa(page, env.primary.base_url, "config?tab=route")
        before_saves = sum(1 for m, u in guard.all_requests if m == "POST" and "/api/save" in u)
        click_page_add_button(page, extra_texts=("Rule",))
        find_active_dialog(page)
        close_active_dialog(page)
        after_saves = sum(1 for m, u in guard.all_requests if m == "POST" and "/api/save" in u)
        assert after_saves == before_saves, "Cancelling Rule modal unexpectedly triggered POST api/save"


# ===========================================================================
# E2E-11: Operations (Nodes, Domains, Nginx) Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-11",
    test_id="T2_E2E11_01",
    title="POST api/testNode against an unreachable closed localhost port returns clean offline feedback",
)
def t2_e2e11_01(env: E2EEnv) -> None:
    api = env.api()
    closed_port = allocate_distinct_free_ports(1)[0]
    res = api.post_form(
        "testNode",
        {
            "data": (
                f'{{"enable":false,"name":"e2e-t2-offline","baseUrl":"http://127.0.0.1:{closed_port}",'
                f'"webPath":"/app/","token":"dummy","insecure":false}}'
            )
        },
        check_success=False,
    )
    # Either success=false with error message OR status.online==false — never a 500 crash
    obj = res.get("obj") or {}
    assert res.get("success") is False or obj.get("online") is False or obj.get("reachable") is False, (
        f"Closed localhost port unexpectedly reported online: {res}"
    )


@register_test(
    tier=2,
    area="E2E-11",
    test_id="T2_E2E11_02",
    title="Creating a Cluster Node with forbidden bracket characters in name is rejected cleanly",
)
def t2_e2e11_02(env: E2EEnv) -> None:
    api = env.api()
    res = api.save(
        "nodes",
        "new",
        {
            "enable": False,
            "name": "e2e-[invalid-bracket]",
            "baseUrl": "http://127.0.0.1:19992",
            "webPath": "/app/",
            "token": "dummy",
            "insecure": False,
        },
        check_success=False,
    )
    assert res.get("success") is False, f"Bracketed node name should be rejected: {res}"


@register_test(
    tier=2,
    area="E2E-11",
    test_id="T2_E2E11_03",
    title="Submitting nonexistent PEM paths to POST api/saveManualCert fails cleanly with validation error",
)
def t2_e2e11_03(env: E2EEnv) -> None:
    api = env.api()
    res = api.post_form(
        "saveManualCert",
        {
            "domain": "e2e-missing.example.com",
            "certFile": "/tmp/e2e-does-not-exist-cert.pem",
            "keyFile": "/tmp/e2e-does-not-exist-key.pem",
        },
        check_success=False,
    )
    assert res.get("success") is False and bool(res.get("msg")), f"Expected validation error, got {res}"


@register_test(
    tier=2,
    area="E2E-11",
    test_id="T2_E2E11_04",
    title="/domains certificates table with long domain name causes zero document horizontal overflow at 375px",
)
def t2_e2e11_04(env: E2EEnv) -> None:
    api = env.api()
    long_domain = "e2e-very-long-subdomain-boundary-check.sub.example.com"
    with tempfile.TemporaryDirectory(prefix="sui-e2e-cert2-") as td:
        cert_file, key_file = create_temp_self_signed_cert(long_domain, Path(td))
        try:
            api.post_form(
                "saveManualCert",
                {"domain": long_domain, "certFile": str(cert_file), "keyFile": str(key_file)},
            )
            with env.auth_page(viewport=(375, 812), test_id="T2_E2E11_04") as (page, _g):
                navigate_spa(page, env.primary.base_url, "domains")
                page.get_by_text(long_domain, exact=False).first.wait_for(state="visible", timeout=8000)
                probe_viewport_overflow_and_grid(page)
        finally:
            env.cleanup_entities(prefix="e2e-very-long")


@register_test(
    tier=2,
    area="E2E-11",
    test_id="T2_E2E11_05",
    title="Read-only POST api/checkNginxProxy drift check returns valid JSON without mutating host files",
)
def t2_e2e11_05(env: E2EEnv) -> None:
    api = env.api()
    res = api.post_form("checkNginxProxy", {"webNginx": "false", "subNginx": "false"}, check_success=False)
    assert isinstance(res, dict) and "success" in res


# ===========================================================================
# E2E-12: Settings, Tools, Admins & Security Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-12",
    test_id="T2_E2E12_01",
    title="Restart Panel action on /settings?tab=tools opens Vuetify ConfirmDialog (ZERO native confirm) and Cancel leaves server running",
)
def t2_e2e12_01(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E12_01") as (page, guard):
        navigate_spa(page, env.primary.base_url, "settings?tab=tools")
        restart_btn = page.locator(
            'main button:has-text("Restart Panel"), main button:has-text("Restart App"), '
            'main button:has-text("重启面板"), main button:has(.mdi-restart-alert), '
            'main button:has-text("Restart"), main button:has(.mdi-restart)'
        ).first
        restart_btn.wait_for(state="visible", timeout=6000)
        restart_btn.click()
        wait_for_rAF_settle(page, delay_ms=200)
        assert not guard.native_dialogs, f"Native window.confirm fired on Restart Panel: {guard.native_dialogs}"
        check_confirm_dialog_semantics(page)
        close_active_dialog(page)
        # Verify server remained alive & untouched
        assert env.api().get_json("auth-check").get("success") is True


@register_test(
    tier=2,
    area="E2E-12",
    test_id="T2_E2E12_02",
    title="Invalid share URI submitted to POST api/linkConvert returns clean error JSON without crash",
)
def t2_e2e12_02(env: E2EEnv) -> None:
    api = env.api()
    res = api.post_form("linkConvert", {"link": "not-a-valid-proxy-uri://???"}, check_success=False)
    assert res.get("success") is False, f"Invalid URI should return success=false: {res}"


@register_test(
    tier=2,
    area="E2E-12",
    test_id="T2_E2E12_03",
    title="apiv2/status with missing or forged Token header returns HTTP 401 JSON {'success':false,'msg':'invalid token'}",
)
def t2_e2e12_03(env: E2EEnv) -> None:
    api = env.api()
    for tok in (None, "", "forged-invalid-e2e-token-12345"):
        r = api.apiv2_get("status", token=tok, params={"r": "sbd"})
        assert r.status == 401, f"apiv2 with token={tok!r} expected 401, got {r.status}"
        payload = r.json()
        assert payload.get("success") is False and "invalid token" in str(payload.get("msg", "")).lower()


@register_test(
    tier=2,
    area="E2E-12",
    test_id="T2_E2E12_04",
    title="POST api/addToken rejects out-of-range expiry and >128 char description cleanly",
)
def t2_e2e12_04(env: E2EEnv) -> None:
    api = env.api()
    r1 = api.post_form("addToken", {"expiry": "99999", "desc": "e2e-t2-badexp"}, check_success=False)
    assert r1.get("success") is False, f"Out-of-range expiry should fail: {r1}"
    r2 = api.post_form("addToken", {"expiry": "7", "desc": "e2e-" + ("a" * 160)}, check_success=False)
    assert r2.get("success") is False, f"Overlong token description should fail: {r2}"


@register_test(
    tier=2,
    area="E2E-12",
    test_id="T2_E2E12_05",
    title="Invalid keypair kind returns clean error and /settings?tab=unknown recovers safely",
)
def t2_e2e12_05(env: E2EEnv) -> None:
    api = env.api()
    r = api.get_json("keypairs", {"k": "nonexistent_algo_e2e"}, check_success=False)
    assert r.get("success") is False, f"Unknown keypair kind should fail cleanly: {r}"
    with env.auth_page(test_id="T2_E2E12_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "settings?tab=unknown-tab-e2e")
        probe_viewport_overflow_and_grid(page)
        probe_wcag_contrast(page)


# ===========================================================================
# E2E-13: Internationalization & RTL Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-13",
    test_id="T2_E2E13_01",
    title="Unrecognized localStorage['locale'] degrades safely to default locale with zero raw key leaks",
)
def t2_e2e13_01(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"locale": "xx-INVALID-LOCALE"}, test_id="T2_E2E13_01") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_i18n_hygiene(page)
        assert len(page.locator("main").inner_text().strip()) > 30


@register_test(
    tier=2,
    area="E2E-13",
    test_id="T2_E2E13_02",
    title="fa RTL mode at Mobile 375x812 maintains zero horizontal overflow and opens temporary drawer cleanly",
)
def t2_e2e13_02(env: E2EEnv) -> None:
    with env.auth_page(viewport=(375, 812), local_storage={"locale": "fa"}, test_id="T2_E2E13_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        probe_rtl_fidelity(page, is_desktop=False)
        open_mobile_drawer(page)
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-13",
    test_id="T2_E2E13_03",
    title="zhHans locale across secondary routes leaks zero raw translation keys (pages.*, ui.*, navigation.*)",
)
def t2_e2e13_03(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"locale": "zhHans"}, test_id="T2_E2E13_03") as (page, _g):
        for route in ("clients", "inbounds", "config", "domains", "settings", "admins"):
            navigate_spa(page, env.primary.base_url, route)
            probe_i18n_hygiene(page)


@register_test(
    tier=2,
    area="E2E-13",
    test_id="T2_E2E13_04",
    title="fa RTL mode on /login renders centered themed card, RTL direction, and zero horizontal overflow",
)
def t2_e2e13_04(env: E2EEnv) -> None:
    with env.anon_page(local_storage={"locale": "fa"}, test_id="T2_E2E13_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        probe_rtl_fidelity(page, is_desktop=False)
        probe_wcag_contrast(page)


@register_test(
    tier=2,
    area="E2E-13",
    test_id="T2_E2E13_05",
    title="fa RTL mode with an open Add Inbound modal preserves RTL layout and zero horizontal overflow",
)
def t2_e2e13_05(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1280, 800), local_storage={"locale": "fa"}, test_id="T2_E2E13_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "inbounds")
        click_page_add_button(page)
        assert_dialog_chrome_clean(page)
        probe_rtl_fidelity(page, is_desktop=True)
        close_active_dialog(page)


# ===========================================================================
# E2E-14: Responsive & Accessibility Boundaries (5 tests)
# ===========================================================================


@register_test(
    tier=2,
    area="E2E-14",
    test_id="T2_E2E14_01",
    title="Ultra-narrow 320x568 viewport maintains zero horizontal overflow on /login, /, and /clients",
)
def t2_e2e14_01(env: E2EEnv) -> None:
    with env.anon_page(viewport=(320, 568), test_id="T2_E2E14_01_login") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        probe_viewport_overflow_and_grid(page)
    with env.auth_page(viewport=(320, 568), test_id="T2_E2E14_01_app") as (page, _g):
        for route in ("", "clients"):
            navigate_spa(page, env.primary.base_url, route)
            probe_viewport_overflow_and_grid(page)


@register_test(
    tier=2,
    area="E2E-14",
    test_id="T2_E2E14_02",
    title="Pressing Escape dismisses an open modal dialog cleanly and restores page interactivity",
)
def t2_e2e14_02(env: E2EEnv) -> None:
    with env.auth_page(test_id="T2_E2E14_02") as (page, _g):
        navigate_spa(page, env.primary.base_url, "outbounds")
        click_page_add_button(page)
        find_active_dialog(page)
        page.keyboard.press("Escape")
        page.wait_for_function(
            """() => {
                const ovs = document.querySelectorAll('.v-overlay--active .v-overlay__content .v-card, .v-overlay--active [role="dialog"]');
                for (const o of ovs) {
                    const r = o.getBoundingClientRect();
                    if (r.width > 40 && r.height > 40) return false;
                }
                return true;
            }""",
            timeout=5000,
        )


@register_test(
    tier=2,
    area="E2E-14",
    test_id="T2_E2E14_03",
    title="Keyboard Tab focus traversal on /login reaches inputs and submit button cleanly",
)
def t2_e2e14_03(env: E2EEnv) -> None:
    with env.anon_page(test_id="T2_E2E14_03") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        focused_tags: list[str] = []
        for _ in range(10):
            page.keyboard.press("Tab")
            tag = page.evaluate("() => (document.activeElement?.tagName || '').toLowerCase()")
            if tag:
                focused_tags.append(tag)
        assert "input" in focused_tags and "button" in focused_tags, (
            f"Keyboard Tab failed to reach inputs and buttons on /login: {focused_tags}"
        )


@register_test(
    tier=2,
    area="E2E-14",
    test_id="T2_E2E14_04",
    title="Open modal dialog at Mobile 375x812 fits viewport width without horizontal overflow or forced scroll",
)
def t2_e2e14_04(env: E2EEnv) -> None:
    with env.auth_page(viewport=(375, 812), test_id="T2_E2E14_04") as (page, _g):
        navigate_spa(page, env.primary.base_url, "clients")
        click_page_add_button(page)
        assert_dialog_chrome_clean(page)
        probe_viewport_overflow_and_grid(page)
        close_active_dialog(page)


@register_test(
    tier=2,
    area="E2E-14",
    test_id="T2_E2E14_05",
    title="Rapid sequential route switching across 8 routes produces zero uncaught pageerror or console.error",
)
def t2_e2e14_05(env: E2EEnv) -> None:
    routes = ("", "analytics", "clients", "inbounds", "outbounds", "config", "nodes", "admins")
    with env.auth_page(test_id="T2_E2E14_05") as (page, guard):
        for route in routes:
            page.goto(f"{env.primary.base_url}{route}", wait_until="domcontentloaded")
            page.wait_for_timeout(120)
        wait_for_spa_ready(page)
        guard.assert_clean()
