"""Opaque-box 4-tier Playwright + live ./sui binary E2E test suite (Feature F-19)."""

from __future__ import annotations

__version__ = "1.0.0"

FEATURE_AREAS: tuple[str, ...] = (
    "E2E-01",
    "E2E-02",
    "E2E-03",
    "E2E-04",
    "E2E-05",
    "E2E-06",
    "E2E-07",
    "E2E-08",
    "E2E-09",
    "E2E-10",
    "E2E-11",
    "E2E-12",
    "E2E-13",
    "E2E-14",
)

FEATURE_AREA_NAMES: dict[str, str] = {
    "E2E-01": "Binary Embed, HTTP Headers & Subpath Serving",
    "E2E-02": "Theme System, WCAG-AA Contrast & Zero Purple-on-Dark",
    "E2E-03": "Offline Typography, Tabular Numerals & Visual Craft Hygiene",
    "E2E-04": "Login Page, Authentication Lifecycle & 401 Protection",
    "E2E-05": "App Shell — Collapsible Sidebar, Flat Top Bar & Routing",
    "E2E-06": "Dashboard Overview, Core Status & Host Telemetry",
    "E2E-07": "SVG Gauges & Theme-Reactive Charts",
    "E2E-08": "Clients Management, Inline Filters, Bulk/QR Modals & Mobile",
    "E2E-09": "Access & Network Resource Grids & Modals",
    "E2E-10": "Core Config Tabbed Workspace (Rules, DNS, Basics, Raw JSON)",
    "E2E-11": "Operations — Cluster Nodes, Domains/Certs & Nginx Proxy",
    "E2E-12": "Settings, Tools, Admins & Security Settings (Zero Native Dialogs)",
    "E2E-13": "Internationalization (en, zhHans, zhHant) & RTL (fa) Fidelity",
    "E2E-14": "Responsive Ergonomics, Accessibility & Zero Runtime Errors",
}

TIERS: tuple[int, ...] = (1, 2, 3, 4)

# Quantitative coverage thresholds from TEST_INFRA.md (N = 14)
N_AREAS: int = len(FEATURE_AREAS)
MIN_TIER1_PER_AREA: int = 5
MIN_TIER1_TOTAL: int = MIN_TIER1_PER_AREA * N_AREAS  # 70
MIN_TIER2_PER_AREA: int = 5
MIN_TIER2_TOTAL: int = MIN_TIER2_PER_AREA * N_AREAS  # 70
MIN_TIER3_TOTAL: int = N_AREAS  # 14
MIN_TIER4_TOTAL: int = max(5, N_AREAS // 2)  # 7

__all__ = [
    "__version__",
    "FEATURE_AREAS",
    "FEATURE_AREA_NAMES",
    "TIERS",
    "N_AREAS",
    "MIN_TIER1_PER_AREA",
    "MIN_TIER1_TOTAL",
    "MIN_TIER2_PER_AREA",
    "MIN_TIER2_TOTAL",
    "MIN_TIER3_TOTAL",
    "MIN_TIER4_TOTAL",
]
