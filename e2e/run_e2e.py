#!/usr/bin/env python3
"""CLI runner for the S-UI Nexus opaque-box 4-tier E2E test suite (Feature F-19).

Usage:
    python3 e2e/run_e2e.py [--binary ./sui] [--tier 1|2|3|4|all] [--headed]
                           [--screenshots <dir>] [--check] [--filter <substr>]
    python3 -m e2e.run_e2e [same flags]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# Ensure repository root is on sys.path when invoked directly as `python3 e2e/run_e2e.py`
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from e2e import FEATURE_AREAS, FEATURE_AREA_NAMES, TIERS  # noqa: E402
from e2e.harness import E2EEnv, REGISTRY, TestCaseMeta  # noqa: E402


def load_all_tiers() -> None:
    """Import all four E2E tier modules so their @register_test decorators populate REGISTRY."""
    import e2e.tier1_features  # noqa: F401
    import e2e.tier2_boundaries  # noqa: F401
    import e2e.tier3_pairwise  # noqa: F401
    import e2e.tier4_workflows  # noqa: F401


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_e2e.py",
        description="S-UI Nexus Opaque-Box 4-Tier E2E Test Suite (Python stdlib + Playwright)",
    )
    parser.add_argument(
        "--binary",
        default="./sui",
        help="Path to the compiled ./sui binary (default: ./sui)",
    )
    parser.add_argument(
        "--tier",
        default="all",
        choices=["1", "2", "3", "4", "all"],
        help="Select test tier to execute: 1, 2, 3, 4, or all (default: all)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Chromium in headed mode instead of headless",
    )
    parser.add_argument(
        "--screenshots",
        default=None,
        help="Directory to save full-page PNG screenshots on test failures",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run self-verification harness & live PASS/FAIL Chromium probe checks (e2e/test_harness.py)",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Optional case-insensitive substring filter matching test_id, area, or title",
    )
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


@dataclass
class TestOutcome:
    meta: TestCaseMeta
    passed: bool
    duration_s: float
    error_summary: str = ""
    traceback_text: str = ""
    screenshot_path: Optional[Path] = None


def run_self_check() -> int:
    """Execute e2e/test_harness.py via unittest and return 0 ONLY on 100% pass."""
    import e2e.test_harness as test_harness

    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromModule(test_harness)
    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    if result.testsRun == 0 or not result.wasSuccessful():
        return 1
    return 0


def _print_summary(outcomes: list[TestOutcome], total_elapsed_s: float) -> None:
    print("\n" + "=" * 88)
    print("S-UI NEXUS E2E SUMMARY")
    print("=" * 88)

    # Per-tier summary
    print(f"{'Tier':<12} {'Passed':>8} {'Failed':>8} {'Total':>8} {'Time (s)':>10}")
    print("-" * 52)
    for tier in TIERS:
        tier_rows = [o for o in outcomes if o.meta.tier == tier]
        if not tier_rows:
            continue
        p = sum(1 for o in tier_rows if o.passed)
        f = len(tier_rows) - p
        dt = sum(o.duration_s for o in tier_rows)
        print(f"Tier {tier:<7} {p:>8} {f:>8} {len(tier_rows):>8} {dt:>10.2f}")

    # Per-area summary
    print("\n" + f"{'Area':<10} {'Passed':>7} {'Failed':>7} {'Total':>7}  {'Feature Area'}")
    print("-" * 88)
    for area in FEATURE_AREAS:
        area_rows = [o for o in outcomes if o.meta.area == area]
        if not area_rows:
            continue
        p = sum(1 for o in area_rows if o.passed)
        f = len(area_rows) - p
        print(f"{area:<10} {p:>7} {f:>7} {len(area_rows):>7}  {FEATURE_AREA_NAMES[area]}")

    passed_total = sum(1 for o in outcomes if o.passed)
    failed_rows = [o for o in outcomes if not o.passed]
    print("-" * 88)
    print(
        f"TOTAL: {passed_total}/{len(outcomes)} passed, {len(failed_rows)} failed "
        f"in {total_elapsed_s:.2f}s"
    )
    print("=" * 88)

    if failed_rows:
        print("\nFAILURE DIAGNOSTICS:")
        for o in failed_rows:
            print("\n" + "-" * 88)
            print(f"[FAIL] Tier {o.meta.tier} | {o.meta.area} | {o.meta.test_id}: {o.meta.title}")
            if o.screenshot_path and o.screenshot_path.exists():
                print(f"Screenshot: {o.screenshot_path}")
            print(o.traceback_text.rstrip())


def run_live_suite(args: argparse.Namespace) -> int:
    binary = Path(args.binary).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        print(
            f"ERROR: --binary path does not exist or is not executable: {binary.resolve()}\n"
            "Build the ./sui binary first (or pass --check to run self-verification).",
            file=sys.stderr,
        )
        return 2

    load_all_tiers()
    selected = REGISTRY.filter_tests(tier=args.tier, name_filter=args.filter)
    if not selected:
        print(
            f"ERROR: 0 tests matched selection (--tier={args.tier!r}, --filter={args.filter!r}).",
            file=sys.stderr,
        )
        return 2

    screenshots_dir = Path(args.screenshots).expanduser() if args.screenshots else None
    print(
        f"Launching S-UI Nexus E2E suite: binary={binary.resolve()} | tier={args.tier} | "
        f"selected={len(selected)} test(s) | headed={args.headed}"
    )

    suite_t0 = time.monotonic()
    outcomes: list[TestOutcome] = []

    try:
        with E2EEnv(binary, headed=args.headed, screenshots_dir=screenshots_dir) as env:
            print(
                f"Primary server ready: {env.primary.base_url} "
                f"(web_port={env.primary.web_port}, sub_port={env.primary.sub_port})"
            )
            for idx, meta in enumerate(selected, start=1):
                env.current_test_id = meta.test_id
                env.last_screenshot = None
                t0 = time.monotonic()
                try:
                    meta.fn(env)
                    dt = time.monotonic() - t0
                    outcomes.append(TestOutcome(meta=meta, passed=True, duration_s=dt))
                    print(
                        f"[{idx:>3}/{len(selected)}] [PASS] T{meta.tier} {meta.area} {meta.test_id} "
                        f"({dt:.2f}s) - {meta.title}",
                        flush=True,
                    )
                except Exception as exc:
                    dt = time.monotonic() - t0
                    tb = traceback.format_exc()
                    shot = env.last_screenshot
                    if shot is None and screenshots_dir is not None:
                        cand = screenshots_dir.resolve() / f"{meta.test_id}.png"
                        if cand.exists():
                            shot = cand
                    outcomes.append(
                        TestOutcome(
                            meta=meta,
                            passed=False,
                            duration_s=dt,
                            error_summary=f"{type(exc).__name__}: {exc}",
                            traceback_text=tb,
                            screenshot_path=shot,
                        )
                    )
                    print(
                        f"[{idx:>3}/{len(selected)}] [FAIL] T{meta.tier} {meta.area} {meta.test_id} "
                        f"({dt:.2f}s) - {type(exc).__name__}: {exc}",
                        flush=True,
                    )
    except (RuntimeError, FileNotFoundError, OSError) as exc:
        print(f"ERROR: E2E environment failed to initialize:\n{exc}", file=sys.stderr)
        return 1

    total_elapsed = time.monotonic() - suite_t0
    _print_summary(outcomes, total_elapsed)

    passed_count = sum(1 for o in outcomes if o.passed)
    return 0 if (passed_count > 0 and passed_count == len(outcomes)) else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.check:
        return run_self_check()
    return run_live_suite(args)


if __name__ == "__main__":
    sys.exit(main())
