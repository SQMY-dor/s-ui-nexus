"""Tier 4: Real-World Multi-Step End-to-End Admin Workflows (W4-01..W4-08).

Each scenario exercises realistic cross-page admin lifecycles via opaque-box browser
interactions, reload persistence verification, live HTTP/API checks, and deterministic
FK-safe `try ... finally` cleanup.
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
    check_confirm_dialog_semantics,
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
    tier=4,
    area="E2E-08",
    areas=("E2E-04", "E2E-05", "E2E-08", "E2E-09", "E2E-14"),
    test_id="W4_01",
    title="W4-01: Full Inbound -> Client -> QR Subscription (Dark quiet zone + HTTP 200) -> Cleanup Lifecycle",
)
def w4_01_inbound_client_qr_lifecycle(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    inb_tag = "e2e-wf1-inb"
    cli_name = "e2e-wf1-client"
    cli_grp = "e2e-wf1-grp"
    free_port = allocate_distinct_free_ports(1)[0]

    try:
        with env.anon_page(viewport=(1440, 900), test_id="W4_01") as (page, guard):
            # Step 1: Real UI login & verify S-UI Nexus brand
            login_via_ui(page, srv.base_url, "admin", "admin", expect_redirect=True)
            assert "S-UI Nexus" in page.locator("nav, .v-navigation-drawer").first.inner_text()

            # Step 2: Create loopback VLESS inbound and observe it on /inbounds
            inb_r = api.save(
                "inbounds",
                "new",
                {"type": "vless", "tag": inb_tag, "enable": True, "listen": "127.0.0.1", "listen_port": free_port},
            )
            inb_id = next(int(x["id"]) for x in (inb_r.get("obj") or {}).get("inbounds") or [] if x.get("tag") == inb_tag)

            navigate_spa(page, srv.base_url, "inbounds")
            inb_card = page.locator("main .v-card").filter(has_text=inb_tag).first
            inb_card.wait_for(state="visible", timeout=8000)

            # Step 3: Toggle enable switch off -> persist across reload -> toggle back on
            sw = inb_card.locator('input[type="checkbox"], [role="switch"]').first
            with page.expect_response(lambda r: "/api/save" in r.url and r.status == 200, timeout=10000):
                sw.click(force=True)
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            assert next(x for x in api.load_normalized()["inbounds"] if x.get("tag") == inb_tag)["enable"] is False
            inb_card = page.locator("main .v-card").filter(has_text=inb_tag).first
            sw = inb_card.locator('input[type="checkbox"], [role="switch"]').first
            with page.expect_response(lambda r: "/api/save" in r.url and r.status == 200, timeout=10000):
                sw.click(force=True)
            assert next(x for x in api.load_normalized()["inbounds"] if x.get("tag") == inb_tag)["enable"] is True

            # Step 4: Create client bound to inbound in group and filter inline on /clients
            cli_r = api.save(
                "clients",
                "new",
                {
                    "enable": True,
                    "name": cli_name,
                    "desc": "Workflow 1 client",
                    "group": cli_grp,
                    "inbounds": [inb_id],
                    "volume": 2147483648,
                    "expiry": 0,
                    "config": {
                        "vless": {
                            "name": cli_name,
                            "uuid": "55555555-5555-4555-8555-555555555555",
                            "flow": "",
                        }
                    },
                },
            )
            sub_id = next(
                str(c.get("subId", ""))
                for c in (cli_r.get("obj") or {}).get("clients") or []
                if c.get("name") == cli_name
            )
            assert sub_id

            navigate_spa(page, srv.base_url, "clients")
            search = page.locator('main input[type="text"], main input[type="search"]').first
            search.fill(cli_name)
            wait_for_rAF_settle(page, delay_ms=200)
            row = page.locator("tr, .v-card").filter(has_text=cli_name).first
            row.wait_for(state="visible", timeout=6000)

            # Step 5: Switch to Dark mode, open QR modal verifying white quiet-zone + HTTP 200 sub URL
            switch_theme_via_ui(page, "dark")
            row.locator('button:has(.mdi-qrcode)').first.click()
            find_active_dialog(page)
            probe_qr_quiet_zone(page, require_dark_backdrop=True)
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)

            sub_resp = api.raw_request("GET", f"{srv.sub_base_url}{sub_id}", use_cookies=False)
            assert sub_resp.status == 200 and len(sub_resp.body) > 0

            # Step 6: Delete client via ConfirmDialog (Cancel first -> preserves, Confirm -> removes)
            row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            check_confirm_dialog_semantics(page)
            close_active_dialog(page)
            assert row.is_visible(), "Client deleted despite cancelling ConfirmDialog"

            row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            dlg = find_active_dialog(page)
            dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=cli_name, timeout=8000)

            # Delete inbound via UI ConfirmDialog
            navigate_spa(page, srv.base_url, "inbounds")
            inb_card = page.locator("main .v-card").filter(has_text=inb_tag).first
            inb_card.wait_for(state="visible", timeout=6000)
            inb_card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            dlg = find_active_dialog(page)
            dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=inb_tag, timeout=8000)

            # Verify sing-box core stays running
            st = api.get_json("status", {"r": "sbd"})
            assert (st.get("obj") or {}).get("sbd", {}).get("running") is True
            guard.assert_clean()
    finally:
        env.cleanup_entities(prefix="e2e-wf1-")


@register_test(
    tier=4,
    area="E2E-09",
    areas=("E2E-09", "E2E-14"),
    test_id="W4_02",
    title="W4-02: TLS Profile, Inbound Association, Reference Guard ('tls in use') & Ordered Teardown",
)
def w4_02_tls_inbound_reference_guard(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    tls_name = "e2e-wf2-tls"
    inb_tag = "e2e-wf2-inb"
    try:
        # Verify Reality keypair generator works cleanly
        kp = api.get_json("keypairs", {"k": "reality"})
        assert kp.get("success") is True and len(kp.get("obj") or []) >= 2

        tls_r = api.save(
            "tls",
            "new",
            {
                "name": tls_name,
                "server": {"enabled": True, "server_name": "e2e-wf2.example.com"},
                "client": {"enabled": True, "server_name": "e2e-wf2.example.com"},
            },
        )
        tls_id = next(int(x["id"]) for x in (tls_r.get("obj") or {}).get("tls") or [] if x.get("name") == tls_name)

        api.save(
            "inbounds",
            "new",
            {
                "type": "vless",
                "tag": inb_tag,
                "enable": True,
                "listen": "127.0.0.1",
                "listen_port": 0,
                "tls_id": tls_id,
            },
        )

        with env.auth_page(test_id="W4_02") as (page, _g):
            # Step 1: Attempt to delete referenced TLS profile via UI -> backend refuses and card stays!
            navigate_spa(page, srv.base_url, "tls")
            tls_card = page.locator("main .v-card").filter(has_text=tls_name).first
            tls_card.wait_for(state="visible", timeout=6000)
            tls_card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            dlg = find_active_dialog(page)
            with page.expect_response(lambda r: "/api/save" in r.url and r.status == 200, timeout=10000) as resp_info:
                dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
            payload = resp_info.value.json()
            assert payload.get("success") is False, f"Expected refusal deleting referenced TLS: {payload}"
            wait_for_rAF_settle(page, delay_ms=250)
            assert page.locator("main .v-card").filter(has_text=tls_name).count() >= 1, (
                "Referenced TLS card disappeared despite backend refusal"
            )

            # Step 2: Delete referencing inbound on /inbounds
            navigate_spa(page, srv.base_url, "inbounds")
            inb_card = page.locator("main .v-card").filter(has_text=inb_tag).first
            inb_card.wait_for(state="visible", timeout=6000)
            inb_card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            dlg = find_active_dialog(page)
            dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=inb_tag, timeout=8000)

            # Step 3: Return to /tls and delete TLS profile cleanly + verify reload persistence
            navigate_spa(page, srv.base_url, "tls")
            tls_card = page.locator("main .v-card").filter(has_text=tls_name).first
            tls_card.wait_for(state="visible", timeout=6000)
            tls_card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
            dlg = find_active_dialog(page)
            dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
            page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=tls_name, timeout=8000)
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            assert tls_name not in page.locator("main").inner_text()
    finally:
        env.cleanup_entities(prefix="e2e-wf2-")


@register_test(
    tier=4,
    area="E2E-10",
    areas=("E2E-05", "E2E-09", "E2E-10"),
    test_id="W4_03",
    title="W4-03: Core Config Routing + DNS + Raw JSON + Legacy Redirect Deep-Links & Persistence",
)
def w4_03_core_config_routing_workflow(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    baseline_cfg = api.snapshot_config()
    out_tag = "e2e-wf3-block"
    domain_rule = "e2e-wf3.invalid"

    try:
        # Step 1: Create temporary block outbound
        api.save("outbounds", "new", {"type": "block", "tag": out_tag})

        # Step 2: Add routing rule targeting e2e-wf3-block and save config
        updated_cfg = json.loads(json.dumps(baseline_cfg))
        rules = list((updated_cfg.get("route") or {}).get("rules") or [])
        rules.append({"domain_suffix": [domain_rule], "action": "route", "outbound": out_tag})
        updated_cfg.setdefault("route", {})["rules"] = rules
        api.save("config", "set", updated_cfg, apply=True)

        with env.auth_page(test_id="W4_03") as (page, _g):
            # Step 3: Legacy redirect /rules -> /config?tab=route
            navigate_spa(page, srv.base_url, "rules")
            parsed = urllib.parse.urlsplit(page.url)
            assert parsed.path.endswith("/config") and urllib.parse.parse_qs(parsed.query).get("tab") == ["route"]
            assert domain_rule in page.locator("main").inner_text() or out_tag in page.locator("main").inner_text()
            assert page.locator('main .mdi-drag, main .mdi-drag-vertical, main [draggable="true"]').count() >= 1
            probe_card_and_shell_hygiene(page, max_card_depth=2)

            # Step 4: Legacy redirects /dns -> ?tab=dns and /basics -> ?tab=basics
            navigate_spa(page, srv.base_url, "dns")
            assert urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query).get("tab") == ["dns"]
            navigate_spa(page, srv.base_url, "basics")
            assert urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query).get("tab") == ["basics"]

            # Step 5: Inspect Raw JSON tab for saved rule & outbound
            navigate_spa(page, srv.base_url, "config?tab=json")
            raw_json = page.evaluate(
                """() => {
                  const ta = document.querySelector('main textarea');
                  return ta ? ta.value : (document.querySelector('main pre, main code')?.textContent || '');
                }"""
            )
            assert domain_rule in raw_json and out_tag in raw_json, "Saved routing rule missing in Raw JSON tab"

            # Step 6: Reload /config?tab=route and confirm persistence + sing-box running
            navigate_spa(page, srv.base_url, "config?tab=route")
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            assert (api.get_json("status", {"r": "sbd"}).get("obj") or {}).get("sbd", {}).get("running") is True
    finally:
        api.restore_config(baseline_cfg)
        env.cleanup_entities(prefix="e2e-wf3-")


@register_test(
    tier=4,
    area="E2E-06",
    areas=("E2E-02", "E2E-06", "E2E-07"),
    test_id="W4_04",
    title="W4-04: Dashboard Telemetry, Live Gauges/Charts (Light+Dark), Tile Picker, Logs, Backup & Core Restart",
)
def w4_04_dashboard_telemetry_modals_restart(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()

    with env.auth_page(local_storage={"theme": "light"}, color_scheme="light", test_id="W4_04") as (page, guard):
        navigate_spa(page, srv.base_url, "")
        wait_for_live_status(page)
        page.wait_for_timeout(2200)

        # Verify Light mode gauges & history canvas + theme switch diff to Dark
        probe_svg_gauges(page)
        res = probe_canvas_charts(
            page,
            verify_theme_diff=True,
            toggle_theme_fn=lambda: switch_theme_via_ui(page, "dark"),
        )
        assert res["theme_changed"] is True
        probe_svg_gauges(page)
        probe_zero_purple_on_dark(page)

        # Open & close Tile Picker
        tile_btn = page.locator(
            'main button:has(.mdi-view-dashboard-edit-outline), main button:has(.mdi-view-dashboard-edit), '
            'main button:has(.mdi-widgets-outline), main button:has(.mdi-tune), main button:has-text("Tile")'
        ).first
        tile_btn.wait_for(state="visible", timeout=6000)
        tile_btn.click()
        find_active_dialog(page)
        close_active_dialog(page)

        # Open Logs modal -> verify monospace & zero ANSI
        logs_btn = page.locator(
            'main button:has-text("Log"), main button:has(.mdi-text-box-outline), main button:has(.mdi-file-document-outline), main button:has(.mdi-console)'
        ).first
        logs_btn.click()
        dlg = find_active_dialog(page)
        wait_for_rAF_settle(page, delay_ms=250)
        assert "\x1b[" not in dlg.inner_text()
        probe_offline_typography(page, guard, require_mono=True)
        close_active_dialog(page)

        # Open Usage Stats modal if available on Quick Actions
        usage_btn = page.locator(
            'main button:has-text("Usage"), main button:has-text("Stats"), '
            'main button:has(.mdi-chart-donut), main button:has(.mdi-chart-pie)'
        ).first
        if usage_btn.count() > 0 and usage_btn.is_visible():
            usage_btn.click()
            assert_dialog_chrome_clean(page)
            close_active_dialog(page)

        # Open Backup modal + verify singbox-config JSON & getdb SQLite header
        backup_btn = page.locator(
            'main button:has-text("Backup"), main button:has(.mdi-database-export-outline), main button:has(.mdi-backup-restore)'
        ).first
        backup_btn.click()
        assert_dialog_chrome_clean(page)
        close_active_dialog(page)

        sb_resp = api.raw_request("GET", f"{srv.base_url}api/singbox-config", use_cookies=True)
        assert sb_resp.status == 200 and isinstance(sb_resp.json(), dict)
        db_resp = api.raw_request("GET", f"{srv.base_url}api/getdb", use_cookies=True)
        assert db_resp.status == 200 and db_resp.body.startswith(b"SQLite format 3\x00")

        # Trigger safe Restart Sing-box (POST api/restartSb) and verify core stays running
        restart_sb_btn = page.locator(
            'main button:has-text("Restart"), main button:has(.mdi-restart)'
        ).first
        restart_sb_btn.wait_for(state="visible", timeout=6000)
        restart_sb_btn.click()
        wait_for_rAF_settle(page, delay_ms=500)
        # If Restart opened a confirm dialog, confirm it cleanly (ONLY on Dashboard where restart is sing-box!)
        active_dlg = page.locator('.v-overlay--active .v-overlay__content .v-card').last
        if active_dlg.count() > 0 and active_dlg.is_visible():
            confirm = active_dlg.locator('button:has-text("Confirm"), button:has-text("Restart"), button:has-text("Yes")').last
            if confirm.count() > 0 and confirm.is_visible():
                confirm.click()
                wait_for_rAF_settle(page, delay_ms=400)
        assert (api.get_json("status", {"r": "sbd"}).get("obj") or {}).get("sbd", {}).get("running") is True
        guard.assert_clean()


@register_test(
    tier=4,
    area="E2E-12",
    areas=("E2E-04", "E2E-06", "E2E-12"),
    test_id="W4_05",
    title="W4-05: Admin Security, API Token Lifecycle (Create -> apiv2 200 -> Mask -> Delete -> 401) & Audit Trail",
)
def w4_05_admin_security_token_audit(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    tok_desc = "e2e-wf5-tok"
    try:
        # Create token via API & verify apiv2 auth works
        tok_r = api.post_form("addToken", {"expiry": "7", "desc": tok_desc})
        raw_tok = str(tok_r.get("obj") or "")
        assert len(raw_tok) >= 16
        assert api.apiv2_get("status", token=raw_tok, params={"r": "sbd"}).status == 200

        with env.auth_page(local_storage={"theme": "dark"}, color_scheme="dark", test_id="W4_05") as (page, _g):
            navigate_spa(page, srv.base_url, "admins")
            assert "admin" in page.locator("main").inner_text().lower()

            # Open Tokens modal -> verify description & masked value + WCAG contrast
            tok_btn = page.locator('main button:has-text("Token"), main button:has(.mdi-key-variant), main button:has(.mdi-api)').first
            tok_btn.wait_for(state="visible", timeout=6000)
            tok_btn.click()
            dlg = find_active_dialog(page)
            dlg.get_by_text(tok_desc).first.wait_for(state="visible", timeout=6000)
            assert raw_tok not in dlg.inner_text(), "Raw token should be masked on reopen"
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            # Delete token inside modal if delete icon present, else via API
            row = dlg.locator("tr, .v-list-item, .v-card").filter(has_text=tok_desc).first
            del_icon = row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first
            if del_icon.count() > 0 and del_icon.is_visible():
                del_icon.click()
                wait_for_rAF_settle(page, delay_ms=300)
            close_active_dialog(page)

            # Ensure token deleted and apiv2 now rejects with 401
            tokens_left = [t for t in (api.get_json("tokens").get("obj") or []) if t.get("desc") == tok_desc]
            for t in tokens_left:
                api.post_form("deleteToken", {"id": int(t["id"])})
            assert api.apiv2_get("status", token=raw_tok, params={"r": "sbd"}).status == 401

            # Open Audit Changes modal -> clean surface & WCAG contrast
            chg_btn = page.locator('main button:has-text("Changes"), main button:has-text("Audit"), main button:has(.mdi-history)').first
            chg_btn.click()
            find_active_dialog(page)
            probe_wcag_contrast(page, scope_selector=".v-overlay--active .v-overlay__content")
            probe_zero_purple_on_dark(page, scope_selector=".v-overlay--active .v-overlay__content")
            close_active_dialog(page)

            # Open TOTP enrollment dialog if available and Cancel safely (NEVER enable 2FA on shared admin!)
            totp_btn = page.locator('main button:has-text("2FA"), main button:has-text("TOTP"), main button:has-text("Enable")').first
            if totp_btn.count() > 0 and totp_btn.is_visible():
                totp_btn.click()
                wait_for_rAF_settle(page, delay_ms=250)
                with contextlib.suppress(Exception):
                    close_active_dialog(page)
    finally:
        env.cleanup_entities(prefix="e2e-wf5-")


@register_test(
    tier=4,
    area="E2E-12",
    areas=("E2E-01", "E2E-03", "E2E-12"),
    test_id="W4_06",
    title="W4-06: Settings Persistence, Tools Utilities (Keypair/LinkConvert/Restart Cancel) & Zero Native Dialogs",
)
def w4_06_settings_tools_zero_native_dialogs(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    orig_sub_info = api.snapshot_settings().get("subShowInfo", "false")
    flipped = "true" if orig_sub_info != "true" else "false"

    try:
        with env.auth_page(test_id="W4_06") as (page, guard):
            # Step 1: Visit /settings?tab=settings, check flattened hierarchy
            navigate_spa(page, srv.base_url, "settings?tab=settings")
            probe_card_and_shell_hygiene(page, max_card_depth=2)

            # Persist harmless subShowInfo flag and verify reload persistence
            api.save("settings", "set", {"subShowInfo": flipped})
            page.reload(wait_until="domcontentloaded")
            wait_for_spa_ready(page)
            assert api.snapshot_settings().get("subShowInfo") == flipped
            api.restore_safe_settings({"subShowInfo": orig_sub_info})

            # Step 2: Switch to /settings?tab=tools via UI tab or direct link
            tools_tab = page.locator('main [role="tab"]:has-text("Tool"), main .v-tab:has-text("Tool")').first
            if tools_tab.count() > 0 and tools_tab.is_visible():
                tools_tab.click()
                wait_for_rAF_settle(page, delay_ms=200)
            else:
                navigate_spa(page, srv.base_url, "settings?tab=tools")
            assert urllib.parse.parse_qs(urllib.parse.urlsplit(page.url).query).get("tab") == ["tools"]

            # Step 3: Exercise Keypair & Share Link backend + Tools buttons with ZERO native dialogs
            kp = api.get_json("keypairs", {"k": "wireguard"})
            assert kp.get("success") is True and len(kp.get("obj") or []) >= 2
            conv = api.post_form("linkConvert", {"link": "socks5://user:pass@127.0.0.1:1080#e2e-wf6"})
            assert conv.get("success") is True

            # Click Restart Panel and cancel Vuetify ConfirmDialog (never native confirm!)
            restart_btn = page.locator(
                'main button:has-text("Restart Panel"), main button:has-text("Restart App"), '
                'main button:has(.mdi-restart-alert), main button:has-text("Restart"), main button:has(.mdi-restart)'
            ).first
            restart_btn.wait_for(state="visible", timeout=6000)
            restart_btn.click()
            wait_for_rAF_settle(page, delay_ms=200)
            assert not guard.native_dialogs, f"Native dialog fired in Tools: {guard.native_dialogs}"
            check_confirm_dialog_semantics(page)
            close_active_dialog(page)
            guard.assert_clean()
    finally:
        api.restore_safe_settings({"subShowInfo": orig_sub_info})


@register_test(
    tier=4,
    area="E2E-13",
    areas=("E2E-02", "E2E-03", "E2E-05", "E2E-13", "E2E-14"),
    test_id="W4_07",
    title="W4-07: Multi-Theme, Multi-Locale (en -> zhHans -> fa RTL), Sidebar Rail & Mobile 375px Logout Journey",
)
def w4_07_theme_locale_rtl_rail_mobile_journey(env: E2EEnv) -> None:
    srv = env.primary
    with env.anon_page(viewport=(1440, 900), local_storage={"theme": "light", "locale": "en"}, test_id="W4_07") as (page, guard):
        # Step 1: Login & test Desktop 1440px rail collapse -> reload -> expand
        login_via_ui(page, srv.base_url, "admin", "admin", expect_redirect=True)
        collapsed = toggle_sidebar_rail(page)
        assert collapsed["width"] <= 96
        page.reload(wait_until="domcontentloaded")
        wait_for_spa_ready(page)
        assert (get_sidebar_rect(page) or {}).get("width", 999) <= 96
        expanded = toggle_sidebar_rail(page)
        assert expanded["width"] >= 200

        # Step 2: Light -> Dark across core routes (zero purple + WCAG AA)
        switch_theme_via_ui(page, "dark")
        for route in ("", "analytics", "clients", "inbounds", "domains"):
            navigate_spa(page, srv.base_url, route)
            probe_zero_purple_on_dark(page)
            probe_wcag_contrast(page)

        # Step 3: Switch en -> zhHans (zero raw key leaks) -> fa RTL (right-edge sidebar + LTR JSON + zero overflow)
        switch_locale_via_ui(page, "zhHans")
        assert re.search(r"[\u4e00-\u9fff]", page.locator("body").inner_text())
        probe_i18n_hygiene(page)

        switch_locale_via_ui(page, "fa")
        navigate_spa(page, srv.base_url, "config?tab=json")
        probe_rtl_fidelity(page, is_desktop=True)
        editor_dir = page.evaluate(
            """() => {
              const el = document.querySelector('main textarea, main pre, main code, main [dir="ltr"]');
              return el ? getComputedStyle(el).direction : null;
            }"""
        )
        assert editor_dir == "ltr"

        # Step 4: Resize to Mobile 375x812, open mobile drawer, inspect /clients & /domains
        page.set_viewport_size({"width": 375, "height": 812})
        wait_for_rAF_settle(page, delay_ms=250)
        for route in ("clients", "domains"):
            navigate_spa(page, srv.base_url, route)
            probe_viewport_overflow_and_grid(page)
        open_mobile_drawer(page)

        # Step 5: Logout & assert redirect to /login and 401 on protected api/load
        logout_el = page.locator(
            'nav .v-list-item:has(.mdi-logout), nav button:has(.mdi-logout), .v-navigation-drawer :has(.mdi-logout-variant)'
        ).first
        logout_el.wait_for(state="visible", timeout=6000)
        logout_el.click()
        page.wait_for_function("() => window.location.pathname.endsWith('/login')", timeout=10000)

        with guard.allow_http_status(401, "/api/load"):
            status_code = page.evaluate(
                """async (baseUrl) => (await fetch(baseUrl + 'api/load')).status""",
                srv.base_url,
            )
            assert status_code == 401, f"Expected 401 on api/load after logout, got {status_code}"
        guard.assert_clean()


@register_test(
    tier=4,
    area="E2E-11",
    areas=("E2E-09", "E2E-11", "E2E-14"),
    test_id="W4_08",
    title="W4-08: Cluster Nodes CRUD + Offline Probe & Domains Manual Cert Validation + Valid PEM Lifecycle",
)
def w4_08_nodes_and_domains_cert_workflow(env: E2EEnv) -> None:
    srv = env.primary
    api = env.api()
    node_name = "e2e-wf8-node"
    cert_domain = "e2e-wf8.example.com"
    closed_port = allocate_distinct_free_ports(1)[0]

    with tempfile.TemporaryDirectory(prefix="sui-e2e-wf8-") as td:
        cert_file, key_file = create_temp_self_signed_cert(cert_domain, Path(td))
        try:
            with env.auth_page(viewport=(1280, 800), test_id="W4_08") as (page, guard):
                # Step 1: Visit /nodes empty state, probe offline node gracefully, create disabled node
                navigate_spa(page, srv.base_url, "nodes")
                probe_viewport_overflow_and_grid(page)

                probe_res = api.post_form(
                    "testNode",
                    {
                        "data": json.dumps(
                            {
                                "enable": False,
                                "name": node_name,
                                "baseUrl": f"http://127.0.0.1:{closed_port}",
                                "webPath": "/app/",
                                "token": "e2e-dummy-token",
                                "insecure": False,
                            }
                        )
                    },
                    check_success=False,
                )
                obj = probe_res.get("obj") or {}
                assert probe_res.get("success") is False or obj.get("online") is False

                node_r = api.save(
                    "nodes",
                    "new",
                    {
                        "enable": False,
                        "name": node_name,
                        "baseUrl": f"http://127.0.0.1:{closed_port}",
                        "webPath": "/app/",
                        "token": "e2e-dummy-token",
                        "insecure": False,
                        "certPin": "",
                        "desc": "WF8 initial",
                    },
                )
                node_obj = next(n for n in (node_r.get("obj") or {}).get("nodes") or [] if n.get("name") == node_name)
                # Edit node description & verify on /nodes
                api.save("nodes", "edit", {**node_obj, "token": "e2e-dummy-token", "desc": "WF8 updated"})
                navigate_spa(page, srv.base_url, "nodes")
                card = page.locator("main .v-card").filter(has_text=node_name).first
                card.wait_for(state="visible", timeout=6000)

                # Delete node via ConfirmDialog
                card.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first.click()
                check_confirm_dialog_semantics(page)
                dlg = find_active_dialog(page)
                dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
                page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=node_name, timeout=8000)

                # Step 2: Visit /domains -> verify invalid manual cert rejected, then register valid PEM cert
                navigate_spa(page, srv.base_url, "domains")
                bad_cert = api.post_form(
                    "saveManualCert",
                    {
                        "domain": "e2e-wf8-bad.example.com",
                        "certFile": "/tmp/e2e-does-not-exist-wf8.pem",
                        "keyFile": "/tmp/e2e-does-not-exist-wf8.key",
                    },
                    check_success=False,
                )
                assert bad_cert.get("success") is False

                ok_cert = api.post_form(
                    "saveManualCert",
                    {"domain": cert_domain, "certFile": str(cert_file), "keyFile": str(key_file)},
                )
                assert ok_cert.get("success") is True

                page.reload(wait_until="domcontentloaded")
                wait_for_spa_ready(page)
                page.get_by_text(cert_domain, exact=False).first.wait_for(state="visible", timeout=8000)
                probe_viewport_overflow_and_grid(page)

                # Delete valid cert via UI ConfirmDialog or API
                cert_row = page.locator("tr, .v-card").filter(has_text=cert_domain).first
                del_btn = cert_row.locator('button:has(.mdi-delete-outline), button:has(.mdi-delete)').first
                if del_btn.count() > 0 and del_btn.is_visible():
                    del_btn.click()
                    dlg = find_active_dialog(page)
                    dlg.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes"), button.bg-error').last.click()
                    page.wait_for_function("(t) => !document.body.innerText.includes(t)", arg=cert_domain, timeout=8000)
                else:
                    api.post_form("deleteCert", {"domain": cert_domain})
                guard.assert_clean()
        finally:
            env.cleanup_entities(prefix="e2e-wf8")
