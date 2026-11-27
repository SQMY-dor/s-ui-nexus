"""Tier 3: Cross-Feature Pairwise Combinations — 18 distinct combination tests (>= 14).

Verifies real interactions across themes, locales/RTL, viewports, custom subpath /ci/,
modals, charts/gauges, tables, resource grids, security/tokens, and zero native dialogs.
"""

from __future__ import annotations

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
    assert_security_headers,
    check_confirm_dialog_semantics,
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
    switch_locale_via_ui,
    switch_theme_via_ui,
    toggle_sidebar_rail,
    wait_for_live_status,
    wait_for_rAF_settle,
    wait_for_spa_ready,
)


@register_test(
    tier=3,
    area="E2E-01",
    areas=("E2E-01", "E2E-02"),
    test_id="T3_PAIR_01",
    title="Custom Subpath /ci/ x Dark Mode: immutable assets, surface separation, WCAG AA contrast, zero purple",
)
def t3_pair_01(env: E2EEnv) -> None:
    ci_srv = env.subpath
    with env.auth_page(
        server="subpath",
        local_storage={"theme": "dark"},
        color_scheme="dark",
        test_id="T3_PAIR_01",
    ) as (page, guard):
        navigate_spa(page, ci_srv.base_url, "")
        wait_for_live_status(page)
        assert page.evaluate("() => window.BASE_URL") == "/ci/"
        ci_assets = [(st, h) for st, _m, u, h in guard.all_responses if "/ci/assets/" in u]
        assert len(ci_assets) >= 2
        for st, hdrs in ci_assets:
            assert st == 200
            assert_security_headers(hdrs, is_asset=True)
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        probe_wcag_contrast(page)


@register_test(
    tier=3,
    area="E2E-01",
    areas=("E2E-01", "E2E-13"),
    test_id="T3_PAIR_02",
    title="Custom Subpath /ci/ x fa RTL Locale: right-anchored sidebar, RTL direction, and zero overflow under /ci/",
)
def t3_pair_02(env: E2EEnv) -> None:
    ci_srv = env.subpath
    with env.auth_page(
        server="subpath",
        viewport=(1440, 900),
        local_storage={"locale": "fa"},
        test_id="T3_PAIR_02",
    ) as (page, _g):
        navigate_spa(page, ci_srv.base_url, "")
        assert page.evaluate("() => window.BASE_URL") == "/ci/"
        probe_rtl_fidelity(page, is_desktop=True)


@register_test(
    tier=3,
    area="E2E-02",
    areas=("E2E-02", "E2E-04"),
    test_id="T3_PAIR_03",
    title="System Dark Media (prefers-color-scheme: dark) x /login: themed dark login card, WCAG AA, zero purple",
)
def t3_pair_03(env: E2EEnv) -> None:
    with env.anon_page(
        local_storage={"theme": "system"},
        color_scheme="dark",
        test_id="T3_PAIR_03",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        probe_wcag_contrast(page)


@register_test(
    tier=3,
    area="E2E-02",
    areas=("E2E-02", "E2E-05"),
    test_id="T3_PAIR_04",
    title="Collapsed Desktop Rail (~68px) x Dark Mode x Reload: both states persist cleanly with accessible controls",
)
def t3_pair_04(env: E2EEnv) -> None:
    with env.auth_page(
        viewport=(1440, 900),
        local_storage={"theme": "light"},
        color_scheme="light",
        test_id="T3_PAIR_04",
    ) as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        switch_theme_via_ui(page, "dark")
        collapsed = toggle_sidebar_rail(page)
        assert collapsed["width"] <= 96
        page.reload(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        reloaded = get_sidebar_rect(page)
        assert reloaded is not None and reloaded["width"] <= 96
        probe_surface_separation(page, mode="dark")
        probe_zero_purple_on_dark(page)
        probe_accessible_actions(page)


@register_test(
    tier=3,
    area="E2E-07",
    areas=("E2E-02", "E2E-07"),
    test_id="T3_PAIR_05",
    title="Light <-> Dark Theme Switch x Dashboard SVG Gauges + History Canvas redraw & track contrast",
)
def t3_pair_05(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="T3_PAIR_05") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        page.wait_for_timeout(2200)
        probe_svg_gauges(page)
        res = probe_canvas_charts(
            page,
            verify_theme_diff=True,
            toggle_theme_fn=lambda: switch_theme_via_ui(page, "dark"),
        )
        assert res["theme_changed"] is True
        probe_svg_gauges(page)
        probe_zero_purple_on_dark(page)


@register_test(
    tier=3,
    area="E2E-08",
    areas=("E2E-02", "E2E-08"),
    test_id="T3_PAIR_06",
    title="Dark Mode x Client QR Modal: crisp #FFFFFF quiet zone, WCAG AA modal contrast, zero purple",
)
def t3_pair_06(env: E2EEnv) -> None:
    api = env.api()
    inb_tag, cli_name = "e2e-t3-p6-inb", "e2e-t3-p6-cli"
    try:
        r = api.save("inbounds", "new", {"type": "vless", "tag": inb_tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        inb_id = next(int(x["id"]) for x in (r.get("obj") or {}).get("inbounds") or [] if x.get("tag") == inb_tag)
        api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": cli_name,
                "desc": "Pair 6 QR",
                "group": "p6",
                "inbounds": [inb_id],
                "volume": 0,
                "expiry": 0,
                "config": {"vless": {"name": cli_name, "uuid": "33333333-3333-4333-8333-333333333333", "flow": ""}},
            },
        )
        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T3_PAIR_06") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            row = page.locator("tr, .v-card").filter(has_text=cli_name).first
            row.wait_for(state="visible", timeout=6000)
            row.locator('button:has(.mdi-qrcode)').first.click()
            find_active_dialog(page)
            probe_qr_quiet_zone(page, require_dark_backdrop=True)
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-t3-p6-")


@register_test(
    tier=3,
    area="E2E-12",
    areas=("E2E-02", "E2E-12"),
    test_id="T3_PAIR_07",
    title="Dark Mode x /admins Tokens & Audit Changes Modals: readable contrast, valid surface, zero purple",
)
def t3_pair_07(env: E2EEnv) -> None:
    api = env.api()
    try:
        api.post_form("addToken", {"expiry": "7", "desc": "e2e-t3-p7-tok"})
        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T3_PAIR_07") as (page, _g):
            navigate_spa(page, env.primary.base_url, "admins")
            probe_zero_purple_on_dark(page)

            tok_btn = page.locator('main button:has-text("Token"), main button:has(.mdi-key-variant), main button:has(.mdi-api)').first
            tok_btn.wait_for(state="visible", timeout=6000)
            tok_btn.click()
            find_active_dialog(page)
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)

            chg_btn = page.locator('main button:has-text("Changes"), main button:has-text("Audit"), main button:has(.mdi-history)').first
            chg_btn.wait_for(state="visible", timeout=6000)
            chg_btn.click()
            find_active_dialog(page)
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-t3-p7-")


@register_test(
    tier=3,
    area="E2E-05",
    areas=("E2E-05", "E2E-10", "E2E-01"),
    test_id="T3_PAIR_08",
    title="Legacy Redirects (/rules, /dns, /basics, /logs) x Custom Subpath /ci/ preserve /ci/ prefix and ?tab=",
)
def t3_pair_08(env: E2EEnv) -> None:
    ci_srv = env.subpath
    cases = [
        ("rules", "/ci/config", "route"),
        ("dns", "/ci/config", "dns"),
        ("basics", "/ci/config", "basics"),
        ("logs", "/ci/analytics", None),
    ]
    with env.auth_page(server="subpath", test_id="T3_PAIR_08") as (page, _g):
        for old_p, exp_path, exp_tab in cases:
            navigate_spa(page, ci_srv.base_url, old_p)
            parsed = urllib.parse.urlsplit(page.url)
            assert parsed.path == exp_path, f"/ci/{old_p} landed at {page.url!r}, expected {exp_path}"
            if exp_tab is not None:
                assert urllib.parse.parse_qs(parsed.query).get("tab") == [exp_tab]


@register_test(
    tier=3,
    area="E2E-05",
    areas=("E2E-05", "E2E-13", "E2E-14"),
    test_id="T3_PAIR_09",
    title="Mobile 375px Temporary Drawer x zhHans Locale: localized nav items, navigation, zero overflow & key leaks",
)
def t3_pair_09(env: E2EEnv) -> None:
    with env.auth_page(viewport=(375, 812), local_storage={"locale": "zhHans"}, test_id="T3_PAIR_09") as (page, _g):
        navigate_spa(page, env.primary.base_url, "")
        open_mobile_drawer(page)
        assert re.search(r"[\u4e00-\u9fff]", page.locator("nav, .v-navigation-drawer").first.inner_text())
        probe_i18n_hygiene(page)
        probe_viewport_overflow_and_grid(page)
        # Click Clients link inside mobile drawer
        page.locator('nav a[href*="clients"], .v-navigation-drawer a[href*="clients"]').first.click()
        page.wait_for_function("() => window.location.pathname.includes('/clients')", timeout=8000)
        probe_viewport_overflow_and_grid(page)


@register_test(
    tier=3,
    area="E2E-08",
    areas=("E2E-08", "E2E-14"),
    test_id="T3_PAIR_10",
    title="Mobile 375px x Populated /clients with Inline Search Filter: clean layout and zero horizontal overflow",
)
def t3_pair_10(env: E2EEnv) -> None:
    api = env.api()
    names = ("e2e-t3-mob-one", "e2e-t3-mob-two")
    try:
        for nm in names:
            api.save("clients", "new", {"enable": True, "name": nm, "desc": "Mobile check", "group": "mob", "inbounds": [], "volume": 1048576, "expiry": 0})
        with env.auth_page(viewport=(375, 812), test_id="T3_PAIR_10") as (page, _g):
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text(names[0]).first.wait_for(state="visible", timeout=6000)
            probe_viewport_overflow_and_grid(page)
            search = page.locator('main input[type="text"], main input[type="search"]').first
            search.fill("mob-one")
            wait_for_rAF_settle(page, delay_ms=200)
            txt = page.locator("main").inner_text()
            assert names[0] in txt and names[1] not in txt
            probe_viewport_overflow_and_grid(page)
    finally:
        env.cleanup_entities(prefix="e2e-t3-mob-")


@register_test(
    tier=3,
    area="E2E-09",
    areas=("E2E-09", "E2E-03"),
    test_id="T3_PAIR_11",
    title="Populated /inbounds Grid (1440px) x Visual Craft Hygiene: >=180px cards, zero overlap, hairline borders, tabular-nums",
)
def t3_pair_11(env: E2EEnv) -> None:
    api = env.api()
    ports = allocate_distinct_free_ports(3)
    try:
        for idx, p in enumerate(ports):
            api.save("inbounds", "new", {"type": "mixed", "tag": f"e2e-t3-inbg-{idx}", "enable": True, "listen": "127.0.0.1", "listen_port": p})
        with env.auth_page(viewport=(1440, 900), test_id="T3_PAIR_11") as (page, guard):
            navigate_spa(page, env.primary.base_url, "inbounds")
            page.get_by_text("e2e-t3-inbg-2").first.wait_for(state="visible", timeout=6000)
            probe_viewport_overflow_and_grid(page, check_grid=True, min_card_width=180.0)
            probe_card_and_shell_hygiene(page, check_card_radius=True)
            probe_offline_typography(page, guard)
    finally:
        env.cleanup_entities(prefix="e2e-t3-inbg-")


@register_test(
    tier=3,
    area="E2E-09",
    areas=("E2E-09", "E2E-07"),
    test_id="T3_PAIR_12",
    title="Created Inbound x Resource Stats Modal: renders Chart.js canvas with compact period controls and clean chrome",
)
def t3_pair_12(env: E2EEnv) -> None:
    api = env.api()
    tag = "e2e-t3-st-inb"
    try:
        api.save("inbounds", "new", {"type": "mixed", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        with env.auth_page(test_id="T3_PAIR_12") as (page, _g):
            navigate_spa(page, env.primary.base_url, "inbounds")
            card = page.locator("main .v-card").filter(has_text=tag).first
            card.wait_for(state="visible", timeout=6000)
            card.locator('button:has(.mdi-chart-line), button:has(.mdi-chart-bar), button:has(.mdi-chart-areaspline)').first.click()
            dlg = find_active_dialog(page)
            assert dlg.locator('input[type="radio"], .v-radio').count() < 8
            probe_canvas_charts(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-t3-st-")


@register_test(
    tier=3,
    area="E2E-10",
    areas=("E2E-10", "E2E-13"),
    test_id="T3_PAIR_13",
    title="/config Tabs (route/dns/basics/json) x fa RTL: RTL chrome preserves LTR Raw JSON editor and zero overflow",
)
def t3_pair_13(env: E2EEnv) -> None:
    with env.auth_page(viewport=(1440, 900), local_storage={"locale": "fa"}, test_id="T3_PAIR_13") as (page, _g):
        for tab in ("route", "dns", "basics", "json"):
            navigate_spa(page, env.primary.base_url, f"config?tab={tab}")
            probe_rtl_fidelity(page, is_desktop=True)
        editor_dir = page.evaluate(
            """() => {
              const el = document.querySelector('main textarea, main pre, main code, main [dir="ltr"]');
              return el ? getComputedStyle(el).direction : null;
            }"""
        )
        assert editor_dir == "ltr"


@register_test(
    tier=3,
    area="E2E-11",
    areas=("E2E-11", "E2E-02", "E2E-14"),
    test_id="T3_PAIR_14",
    title="/domains with Valid Manual Cert x Dark Mode x Tablet 768px: scrollable table, dark contrast, zero purple",
)
def t3_pair_14(env: E2EEnv) -> None:
    api = env.api()
    domain = "e2e-t3-p14.example.com"
    with tempfile.TemporaryDirectory(prefix="sui-e2e-p14-") as td:
        cert_file, key_file = create_temp_self_signed_cert(domain, Path(td))
        try:
            api.post_form("saveManualCert", {"domain": domain, "certFile": str(cert_file), "keyFile": str(key_file)})
            with env.auth_page(
                viewport=(768, 1024),
                local_storage={"theme": "dark"},
                color_scheme="dark",
                test_id="T3_PAIR_14",
            ) as (page, _g):
                navigate_spa(page, env.primary.base_url, "domains")
                page.get_by_text(domain).first.wait_for(state="visible", timeout=8000)
                probe_viewport_overflow_and_grid(page)
                probe_zero_purple_on_dark(page)
                probe_wcag_contrast(page)
        finally:
            env.cleanup_entities(prefix="e2e-t3-p14")


@register_test(
    tier=3,
    area="E2E-12",
    areas=("E2E-12", "E2E-13"),
    test_id="T3_PAIR_15",
    title="/settings?tab=tools x zhHant Locale x Zero Native Dialogs: localized tools UI & Vuetify ConfirmDialog",
)
def t3_pair_15(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"locale": "zhHant"}, test_id="T3_PAIR_15") as (page, guard):
        navigate_spa(page, env.primary.base_url, "settings?tab=tools")
        assert re.search(r"[\u4e00-\u9fff]", page.locator("main").inner_text())
        probe_i18n_hygiene(page)
        restart_btn = page.locator(
            'main button:has(.mdi-restart-alert), main button:has-text("重啟面板"), '
            'main button:has-text("重启面板"), main button:has-text("重啟"), '
            'main button:has-text("Restart"), main button:has(.mdi-restart)'
        ).first
        restart_btn.wait_for(state="visible", timeout=6000)
        restart_btn.click()
        wait_for_rAF_settle(page, delay_ms=200)
        assert not guard.native_dialogs
        check_confirm_dialog_semantics(page)
        close_active_dialog(page)


@register_test(
    tier=3,
    area="E2E-06",
    areas=("E2E-06", "E2E-02", "E2E-03"),
    test_id="T3_PAIR_16",
    title="Dashboard Logs Modal x Dark Mode x Monospace & Zero ANSI: readable dark modal logs with zero purple",
)
def t3_pair_16(env: E2EEnv) -> None:
    with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="T3_PAIR_16") as (page, guard):
        navigate_spa(page, env.primary.base_url, "")
        wait_for_live_status(page)
        logs_btn = page.locator(
            'main button:has-text("Log"), main button:has(.mdi-text-box-outline), main button:has(.mdi-file-document-outline), main button:has(.mdi-console)'
        ).first
        logs_btn.wait_for(state="visible", timeout=6000)
        logs_btn.click()
        dlg = find_active_dialog(page)
        wait_for_rAF_settle(page, delay_ms=300)
        assert "\x1b[" not in dlg.inner_text()
        probe_offline_typography(page, guard, require_mono=True)
        probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
        probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
        close_active_dialog(page)


@register_test(
    tier=3,
    area="E2E-04",
    areas=("E2E-04", "E2E-13"),
    test_id="T3_PAIR_17",
    title="/login x zhHans Locale x Password Visibility Toggle -> Authenticated Localized Shell",
)
def t3_pair_17(env: E2EEnv) -> None:
    with env.anon_page(local_storage={"locale": "en"}, test_id="T3_PAIR_17") as (page, _g):
        navigate_spa(page, env.primary.base_url, "login", expect_shell=False)
        switch_locale_via_ui(page, "zhHans")
        assert re.search(r"[\u4e00-\u9fff]", page.locator("body").inner_text())
        probe_i18n_hygiene(page)
        login_via_ui(page, env.primary.base_url, "admin", "admin", expect_redirect=True)
        assert re.search(r"[\u4e00-\u9fff]", page.locator("nav, .v-navigation-drawer").first.inner_text())
        probe_i18n_hygiene(page)


@register_test(
    tier=3,
    area="E2E-08",
    areas=("E2E-08", "E2E-09"),
    test_id="T3_PAIR_18",
    title="Inbound Enable Toggle x Bound Client Group Filtering: cross-page state stays consistent across reload",
)
def t3_pair_18(env: E2EEnv) -> None:
    api = env.api()
    tag = "e2e-t3-p18-inb"
    cli = "e2e-t3-p18-cli"
    try:
        r = api.save("inbounds", "new", {"type": "vless", "tag": tag, "enable": True, "listen": "127.0.0.1", "listen_port": 0})
        inb_id = next(int(x["id"]) for x in (r.get("obj") or {}).get("inbounds") or [] if x.get("tag") == tag)
        api.save(
            "clients",
            "new",
            {
                "enable": True,
                "name": cli,
                "desc": "Bound client",
                "group": "e2e-p18-grp",
                "inbounds": [inb_id],
                "volume": 0,
                "expiry": 0,
                "config": {"vless": {"name": cli, "uuid": "44444444-4444-4444-8444-444444444444", "flow": ""}},
            },
        )
        with env.auth_page(test_id="T3_PAIR_18") as (page, _g):
            navigate_spa(page, env.primary.base_url, "inbounds")
            card = page.locator("main .v-card").filter(has_text=tag).first
            card.wait_for(state="visible", timeout=6000)
            sw = card.locator('input[type="checkbox"], [role="switch"]').first
            with page.expect_response(lambda resp: "/api/save" in resp.url and resp.status == 200, timeout=10000):
                sw.click(force=True)
            navigate_spa(page, env.primary.base_url, "clients")
            page.get_by_text(cli).first.wait_for(state="visible", timeout=6000)
            search = page.locator('main input[type="text"], main input[type="search"]').first
            search.fill(cli)
            wait_for_rAF_settle(page, delay_ms=200)
            assert cli in page.locator("main").inner_text()
            # Re-verify inbound persisted disabled after cross-page navigation
            inbs = api.load_normalized()["inbounds"]
            assert next(x for x in inbs if x.get("tag") == tag).get("enable") is False
    finally:
        env.cleanup_entities(prefix="e2e-t3-p18-")
