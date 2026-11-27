"""Shared-instance E2E harness: ManagedSuiServer, SuiApiClient, RuntimeGuard, E2EEnv, TestRegistry.

Strictly uses Python 3.12 stdlib + playwright.sync_api (zero third-party runners).
"""

from __future__ import annotations

import contextlib
import copy
import http.cookiejar
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from e2e import FEATURE_AREAS, TIERS

# Keys that MUST NEVER be modified on shared pooled servers
FORBIDDEN_SHARED_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "webPort",
        "webPath",
        "webListen",
        "webDomain",
        "webCertFile",
        "webKeyFile",
        "webNginx",
        "subPort",
        "subPath",
        "subListen",
        "subDomain",
        "subCertFile",
        "subKeyFile",
        "subNginx",
        "trafficAge",
    }
)

# Settings returned masked by GET api/settings that should never be blindly re-written
MASKED_SETTING_KEYS: frozenset[str] = frozenset({"oidcClientSecret"})

# Destructive POST actions forbidden on shared pooled servers (case-insensitive)
FORBIDDEN_SHARED_ACTIONS: frozenset[str] = frozenset(
    {
        "restartapp",
        "changepass",
        "totp-enable",
        "installacme",
        "issuecert",
        "renewcert",
        "syncnginxproxy",
        "applycert",
    }
)

LOCALHOST_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def allocate_distinct_free_ports(count: int, exclude: Optional[set[int]] = None) -> list[int]:
    """Allocate `count` distinct free TCP ports bound simultaneously on 0.0.0.0:0.

    Binding wildcard 0.0.0.0 mirrors Go's `net.Listen("tcp", ":<port>")` and holding
    all sockets open until every port is assigned prevents duplicate allocations.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    excluded = set(exclude or ())
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        attempts = 0
        while len(ports) < count:
            attempts += 1
            if attempts > count * 20:
                raise RuntimeError(f"Unable to allocate {count} distinct free ports")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("0.0.0.0", 0))
            port = int(s.getsockname()[1])
            if port in excluded or port in ports:
                s.close()
                continue
            ports.append(port)
            sockets.append(s)
        return ports
    finally:
        for s in sockets:
            with contextlib.suppress(OSError):
                s.close()


def create_temp_self_signed_cert(domain: str, directory: Path) -> tuple[Path, Path]:
    """Generate a temporary self-signed PEM cert + private key with SAN DNS:<domain>."""
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", domain)
    cert_path = directory / f"{safe_name}.crt"
    key_path = directory / f"{safe_name}.key"
    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-days",
        "7",
        "-nodes",
        "-subj",
        f"/CN={domain}",
        "-addext",
        f"subjectAltName=DNS:{domain}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if res.returncode != 0:
        raise RuntimeError(f"openssl failed generating self-signed cert for {domain}: {res.stderr}")
    return cert_path, key_path


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """HTTP redirect handler that returns 3xx responses untouched to the caller."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass
class RawResponse:
    """Opaque-box HTTP response wrapper preserving status, headers, Set-Cookie, and body."""

    status: int
    headers: dict[str, str]
    set_cookies: list[str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


class SuiApiClient:
    """Opaque-box stdlib urllib + CookieJar client for ./sui web & sub endpoints."""

    def __init__(
        self,
        origin: str,
        web_path: str,
        sub_port: int = 0,
        *,
        shared_server_guard: bool = False,
    ) -> None:
        self.origin = origin.rstrip("/")
        if not web_path.startswith("/"):
            web_path = "/" + web_path
        if not web_path.endswith("/"):
            web_path += "/"
        self.web_path = web_path
        self.base_url = f"{self.origin}{self.web_path}"
        self.sub_port = sub_port
        self.sub_origin = f"http://127.0.0.1:{sub_port}" if sub_port else ""
        self.shared_server_guard = shared_server_guard
        self.cookie_jar = http.cookiejar.CookieJar()
        self._cookie_processor = urllib.request.HTTPCookieProcessor(self.cookie_jar)
        self._opener_follow = urllib.request.build_opener(self._cookie_processor)
        self._opener_nofollow = urllib.request.build_opener(self._cookie_processor, _NoRedirectHandler())
        self._opener_anon_nofollow = urllib.request.build_opener(_NoRedirectHandler())
        self._opener_anon_follow = urllib.request.build_opener()

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        if path_or_url.startswith("/"):
            return f"{self.origin}{path_or_url}"
        return f"{self.base_url}{path_or_url}"

    def raw_request(
        self,
        method: str,
        path_or_url: str,
        *,
        follow_redirects: bool = False,
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        use_cookies: bool = True,
        timeout: float = 10.0,
    ) -> RawResponse:
        """Issue an HTTP request and always return a RawResponse (including 3xx/4xx/5xx)."""
        url = self._resolve_url(path_or_url)
        req_headers = dict(headers or {})
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
        if use_cookies:
            opener = self._opener_follow if follow_redirects else self._opener_nofollow
        else:
            opener = self._opener_anon_follow if follow_redirects else self._opener_anon_nofollow
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw_hdrs = resp.headers
                lower_hdrs = {k.lower(): v for k, v in raw_hdrs.items()}
                set_cookies = raw_hdrs.get_all("Set-Cookie") or []
                return RawResponse(
                    status=int(resp.status),
                    headers=lower_hdrs,
                    set_cookies=list(set_cookies),
                    body=resp.read(),
                    url=resp.geturl(),
                )
        except urllib.error.HTTPError as exc:
            raw_hdrs = exc.headers
            lower_hdrs = {k.lower(): v for k, v in raw_hdrs.items()} if raw_hdrs else {}
            set_cookies = (raw_hdrs.get_all("Set-Cookie") or []) if raw_hdrs else []
            body = exc.read() if exc.fp is not None else b""
            return RawResponse(
                status=int(exc.code),
                headers=lower_hdrs,
                set_cookies=list(set_cookies),
                body=body,
                url=exc.geturl() or url,
            )

    def login(
        self,
        username: str = "admin",
        password: str = "admin",
        *,
        otp: Optional[str] = None,
        require_success: bool = True,
    ) -> dict[str, Any]:
        fields: dict[str, str] = {"user": username, "pass": password}
        if otp is not None:
            fields["otp"] = otp
        body = urllib.parse.urlencode(fields).encode("utf-8")
        resp = self.raw_request(
            "POST",
            f"{self.base_url}api/login",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
            use_cookies=True,
        )
        if resp.status != 200:
            raise AssertionError(f"POST api/login returned HTTP {resp.status}: {resp.text[:400]}")
        payload = resp.json()
        if require_success and payload.get("success") is not True:
            raise AssertionError(f"Login failed for {username!r}: {payload}")
        return payload

    def logout(self) -> dict[str, Any]:
        resp = self.raw_request("GET", f"{self.base_url}api/logout", use_cookies=True)
        if resp.status != 200:
            raise AssertionError(f"GET api/logout returned HTTP {resp.status}: {resp.text[:300]}")
        return resp.json()

    def get_json(
        self,
        action: str,
        params: Optional[dict[str, Any]] = None,
        *,
        check_success: bool = True,
        use_cookies: bool = True,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        url = f"{self.base_url}api/{action.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        resp = self.raw_request("GET", url, use_cookies=use_cookies, timeout=timeout)
        if resp.status != 200:
            raise AssertionError(f"GET {url} returned HTTP {resp.status}: {resp.text[:400]}")
        payload = resp.json()
        if check_success and payload.get("success") is not True:
            raise AssertionError(f"GET api/{action} returned success=false: {payload}")
        return payload

    def post_form(
        self,
        action: str,
        fields: Optional[dict[str, Any]] = None,
        *,
        check_success: bool = True,
        use_cookies: bool = True,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        clean_action = action.lstrip("/")
        if self.shared_server_guard and clean_action.lower() in FORBIDDEN_SHARED_ACTIONS:
            raise RuntimeError(f"Refused forbidden destructive action on shared server: api/{clean_action}")
        url = f"{self.base_url}api/{clean_action}"
        encoded = urllib.parse.urlencode({k: str(v) for k, v in (fields or {}).items()}).encode("utf-8")
        resp = self.raw_request(
            "POST",
            url,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            use_cookies=use_cookies,
            timeout=timeout,
        )
        if resp.status != 200:
            raise AssertionError(f"POST {url} returned HTTP {resp.status}: {resp.text[:400]}")
        payload = resp.json()
        if check_success and payload.get("success") is not True:
            raise AssertionError(f"POST api/{action} returned success=false: {payload}")
        return payload

    def post_multipart(
        self,
        action: str,
        *,
        file_field: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        extra_fields: Optional[dict[str, str]] = None,
        use_cookies: bool = True,
        timeout: float = 15.0,
    ) -> RawResponse:
        clean_action = action.lstrip("/")
        if (
            self.shared_server_guard
            and clean_action.lower() == "importdb"
            and content.startswith(b"SQLite format 3\x00")
        ):
            raise RuntimeError("Refused valid SQLite importdb on shared server (triggers SIGHUP)")
        boundary = f"----SuiE2EBoundary{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for k, v in (extra_fields or {}).items():
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
            )
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(content)
        chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)
        url = f"{self.base_url}api/{clean_action}"
        return self.raw_request(
            "POST",
            url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            use_cookies=use_cookies,
            timeout=timeout,
        )

    def save(
        self,
        obj: str,
        action: str,
        data: Any,
        *,
        apply: bool = True,
        init_users: Optional[str] = None,
        check_success: bool = True,
    ) -> dict[str, Any]:
        if self.shared_server_guard:
            if obj == "outbounds" and action == "del" and str(data) == "direct":
                raise RuntimeError("Refused deleting seeded 'direct' outbound on shared server")
            if obj == "settings" and action == "set" and isinstance(data, dict):
                bad_keys = sorted(FORBIDDEN_SHARED_SETTING_KEYS.intersection(data.keys()))
                if bad_keys:
                    raise RuntimeError(
                        f"Refused mutating forbidden structural setting key(s) on shared server: {bad_keys}"
                    )
        fields: dict[str, Any] = {
            "object": obj,
            "action": action,
            "data": json.dumps(data, separators=(",", ":")),
            "apply": "true" if apply else "false",
        }
        if init_users is not None:
            fields["initUsers"] = init_users
        return self.post_form("save", fields, check_success=check_success)

    def apiv2_get(
        self,
        action: str,
        token: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float = 10.0,
    ) -> RawResponse:
        url = f"{self.base_url}apiv2/{action.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Token": token} if token is not None else {}
        return self.raw_request("GET", url, headers=headers, use_cookies=False, timeout=timeout)

    def load_normalized(self) -> dict[str, Any]:
        """Fetch GET api/load and normalize Go JSON `null` list fields to `[]`."""
        payload = self.get_json("load")
        obj = dict(payload.get("obj") or {})
        for list_key in ("inbounds", "clients", "endpoints", "services", "outbounds", "tls", "nodes"):
            obj[list_key] = list(obj.get(list_key) or [])
        return obj

    def snapshot_config(self) -> dict[str, Any]:
        payload = self.get_json("config")
        return json.loads(json.dumps((payload.get("obj") or {}).get("config") or {}))

    def restore_config(self, cfg: dict[str, Any]) -> None:
        self.save("config", "set", cfg, apply=True, check_success=True)

    def snapshot_settings(self) -> dict[str, str]:
        payload = self.get_json("settings")
        raw = payload.get("obj") or {}
        return {str(k): str(v) for k, v in raw.items()}

    def restore_safe_settings(self, subset: dict[str, str]) -> None:
        excluded = FORBIDDEN_SHARED_SETTING_KEYS | MASKED_SETTING_KEYS
        safe = {k: v for k, v in subset.items() if k not in excluded}
        if safe:
            self.save("settings", "set", safe, apply=True, check_success=True)

    def cleanup_e2e_entities(
        self,
        *,
        prefix: str = "e2e-",
        baseline_config: Optional[dict[str, Any]] = None,
        baseline_settings: Optional[dict[str, str]] = None,
    ) -> None:
        """FK-safe teardown removing all entities matching `prefix`.

        Never touches seeded `"direct"` outbound or `admin` password/TOTP.
        """
        # 1. Restore config only if baseline_config provided AND current config actually diverged
        if baseline_config is not None:
            with contextlib.suppress(Exception):
                if self.snapshot_config() != baseline_config:
                    self.restore_config(baseline_config)

        try:
            state = self.load_normalized()
        except Exception:
            return

        # 2. Delete clients matching prefix
        client_ids = [
            int(c["id"])
            for c in state.get("clients", [])
            if isinstance(c, dict) and str(c.get("name", "")).startswith(prefix) and c.get("id") is not None
        ]
        if client_ids:
            with contextlib.suppress(Exception):
                self.save("clients", "delbulk", client_ids, apply=True, check_success=False)

        # 3. Delete inbounds matching prefix
        for inb in state.get("inbounds", []):
            tag = str(inb.get("tag", "")) if isinstance(inb, dict) else ""
            if tag.startswith(prefix):
                with contextlib.suppress(Exception):
                    self.save("inbounds", "del", tag, apply=True, check_success=False)

        # 4. Delete services matching prefix
        for svc in state.get("services", []):
            tag = str(svc.get("tag", "")) if isinstance(svc, dict) else ""
            if tag.startswith(prefix):
                with contextlib.suppress(Exception):
                    self.save("services", "del", tag, apply=True, check_success=False)

        # 5. Delete endpoints matching prefix
        for ep in state.get("endpoints", []):
            tag = str(ep.get("tag", "")) if isinstance(ep, dict) else ""
            if tag.startswith(prefix):
                with contextlib.suppress(Exception):
                    self.save("endpoints", "del", tag, apply=True, check_success=False)

        # 6. Delete outbounds matching prefix (NEVER touch "direct")
        for out in state.get("outbounds", []):
            tag = str(out.get("tag", "")) if isinstance(out, dict) else ""
            if tag.startswith(prefix) and tag != "direct":
                with contextlib.suppress(Exception):
                    self.save("outbounds", "del", tag, apply=True, check_success=False)

        # 7. Delete TLS profiles matching prefix (after inbounds/services are removed)
        for tls in state.get("tls", []):
            name = str(tls.get("name", "")) if isinstance(tls, dict) else ""
            if name.startswith(prefix) and tls.get("id") is not None:
                with contextlib.suppress(Exception):
                    self.save("tls", "del", int(tls["id"]), apply=True, check_success=False)

        # 8. Delete nodes matching prefix
        for node in state.get("nodes", []):
            name = str(node.get("name", "")) if isinstance(node, dict) else ""
            if name.startswith(prefix) and node.get("id") is not None:
                with contextlib.suppress(Exception):
                    self.save("nodes", "del", int(node["id"]), apply=True, check_success=False)

        # 9. Delete API tokens matching prefix
        with contextlib.suppress(Exception):
            tok_resp = self.get_json("tokens", check_success=False)
            for tok in tok_resp.get("obj") or []:
                desc = str(tok.get("desc", "")) if isinstance(tok, dict) else ""
                if desc.startswith(prefix) and tok.get("id") is not None:
                    self.post_form("deleteToken", {"id": int(tok["id"])}, check_success=False)

        # 10. Delete manual certs matching prefix
        with contextlib.suppress(Exception):
            cert_resp = self.get_json("certs", check_success=False)
            for cert in cert_resp.get("obj") or []:
                dom = str(cert.get("domain", "")) if isinstance(cert, dict) else ""
                if dom.startswith(prefix):
                    self.post_form("deleteCert", {"domain": dom}, check_success=False)

        # 11. Restore only changed safe settings keys
        if baseline_settings:
            with contextlib.suppress(Exception):
                current_settings = self.snapshot_settings()
                excluded = FORBIDDEN_SHARED_SETTING_KEYS | MASKED_SETTING_KEYS
                diff = {
                    k: v
                    for k, v in baseline_settings.items()
                    if k not in excluded and current_settings.get(k) != v
                }
                if diff:
                    self.restore_safe_settings(diff)


class ManagedSuiServer:
    """Two-phase disposable ./sui binary controller with 3-stage readiness & SIGTERM teardown."""

    def __init__(
        self,
        binary: Path,
        web_path: str = "/app/",
        *,
        web_port: Optional[int] = None,
        sub_port: Optional[int] = None,
        debug: bool = False,
        startup_timeout: float = 20.0,
        exclude_ports: Optional[set[int]] = None,
    ) -> None:
        self.binary = Path(binary).resolve()
        if not web_path.startswith("/"):
            web_path = "/" + web_path
        if not web_path.endswith("/"):
            web_path += "/"
        self.web_path = web_path
        self.debug = debug
        self.startup_timeout = startup_timeout

        effective_exclude = set(exclude_ports or ())
        if web_port is not None:
            effective_exclude.add(web_port)
        if sub_port is not None:
            effective_exclude.add(sub_port)
        if web_port is None and sub_port is None:
            self.web_port, self.sub_port = allocate_distinct_free_ports(2, exclude=effective_exclude)
        elif web_port is None:
            assert sub_port is not None
            self.web_port = allocate_distinct_free_ports(1, exclude=effective_exclude)[0]
            self.sub_port = sub_port
        elif sub_port is None:
            self.web_port = web_port
            self.sub_port = allocate_distinct_free_ports(1, exclude=effective_exclude)[0]
        else:
            self.web_port = web_port
            self.sub_port = sub_port
        if self.web_port == self.sub_port:
            raise ValueError("web_port and sub_port must be distinct")

        self.workdir: Optional[Path] = None
        self.log_path: Optional[Path] = None
        self.process: Optional[subprocess.Popen[bytes]] = None
        self._log_fh: Any = None

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.web_port}"

    @property
    def base_url(self) -> str:
        return f"{self.origin}{self.web_path}"

    @property
    def sub_base_url(self) -> str:
        return f"http://127.0.0.1:{self.sub_port}/sub/"

    def start(self) -> "ManagedSuiServer":
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            raise FileNotFoundError(f"./sui binary not found or not executable: {self.binary}")

        slug = re.sub(r"[^a-zA-Z0-9_-]", "", self.web_path.strip("/")) or "root"
        self.workdir = Path(tempfile.mkdtemp(prefix=f"sui-e2e-{slug}-"))
        db_dir = self.workdir / "db"
        nginx_dir = self.workdir / "nginx"
        cert_dir = self.workdir / "certs"
        acme_dir = self.workdir / "acme"
        for d in (db_dir, nginx_dir, cert_dir, acme_dir):
            d.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "SUI_DB_FOLDER": str(db_dir),
            "container": "true",
            "SUI_DEBUG": "true" if self.debug else "false",
            "SUI_LOG_LEVEL": "debug" if self.debug else "info",
            "SUI_NGINX_CONF_DIR": str(nginx_dir),
            "SUI_CERT_DIR": str(cert_dir),
            "SUI_ACME_HOME": str(acme_dir),
        }

        # Phase 1: Seed disposable SQLite DB + write ports & paths
        setting_cmd = [
            str(self.binary),
            "setting",
            "-port",
            str(self.web_port),
            "-path",
            self.web_path,
            "-subPort",
            str(self.sub_port),
            "-subPath",
            "/sub/",
        ]
        setting_res = subprocess.run(
            setting_cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if setting_res.returncode != 0:
            self._cleanup_workdir()
            raise RuntimeError(
                f"./sui setting exited {setting_res.returncode}:\n"
                f"STDOUT:\n{setting_res.stdout}\nSTDERR:\n{setting_res.stderr}"
            )
        # cmd/setting.go exits 0 even on errors; explicitly assert all 4 success markers
        stdout_lower = setting_res.stdout.lower()
        required_markers = (
            "set port success",
            "set path success",
            "set sub port success",
            "set sub path success",
        )
        missing = [m for m in required_markers if m not in stdout_lower]
        if missing or "failed" in stdout_lower:
            self._cleanup_workdir()
            raise RuntimeError(
                f"./sui setting failed verification (missing={missing}):\n"
                f"STDOUT:\n{setting_res.stdout}\nSTDERR:\n{setting_res.stderr}"
            )

        # Phase 2: Launch ./sui daemon with ZERO arguments
        self.log_path = self.workdir / "server.log"
        self._log_fh = open(self.log_path, "wb")
        self.process = subprocess.Popen(
            [str(self.binary)],
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        try:
            self._wait_until_ready()
            return self
        except Exception:
            self.stop()
            raise

    def _wait_until_ready(self) -> None:
        assert self.process is not None
        deadline = time.monotonic() + self.startup_timeout
        probe_client = SuiApiClient(self.origin, self.web_path, self.sub_port)

        # Stage 1: GET <base>api/auth-meta returns 200 & success == True
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"./sui exited prematurely during startup (rc={self.process.returncode}):\n{self.tail_log()}"
                )
            try:
                resp = probe_client.raw_request("GET", f"{self.base_url}api/auth-meta", use_cookies=False, timeout=2.0)
                if resp.status == 200 and resp.json().get("success") is True:
                    break
            except Exception:
                pass
            time.sleep(0.15)
        else:
            raise RuntimeError(f"Stage 1 readiness failed for {self.base_url}api/auth-meta:\n{self.tail_log()}")

        # Stage 2: Subscription server TCP port accepts connections
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"./sui exited during subServer bind (rc={self.process.returncode}):\n{self.tail_log()}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.sub_port), timeout=1.0):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Stage 2 readiness failed: subPort :{self.sub_port} not listening:\n{self.tail_log()}")

        # Stage 3: Disposable login + poll api/status?r=sbd until running == True + logout
        probe_client.login("admin", "admin", require_success=True)
        try:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"./sui exited during StartCore (rc={self.process.returncode}):\n{self.tail_log()}"
                    )
                try:
                    status_payload = probe_client.get_json("status", {"r": "sbd"}, check_success=False, timeout=3.0)
                    sbd = (status_payload.get("obj") or {}).get("sbd") or {}
                    if status_payload.get("success") is True and sbd.get("running") is True:
                        return
                except Exception:
                    pass
                time.sleep(0.15)
            raise RuntimeError(f"Stage 3 readiness failed: sing-box core never reached running=True:\n{self.tail_log()}")
        finally:
            with contextlib.suppress(Exception):
                probe_client.logout()

    def api_client(self, *, authenticated: bool = True, shared_server_guard: bool = False) -> SuiApiClient:
        client = SuiApiClient(
            self.origin,
            self.web_path,
            self.sub_port,
            shared_server_guard=shared_server_guard,
        )
        if authenticated:
            client.login("admin", "admin", require_success=True)
        return client

    def tail_log(self, max_chars: int = 4000) -> str:
        if self.log_path is None:
            return "<no log path>"
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
        except OSError:
            return "<log unavailable>"

    def _cleanup_workdir(self) -> None:
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    def stop(self, timeout: float = 10.0) -> None:
        """Stop ./sui cleanly using SIGTERM (`process.terminate()`, NEVER SIGINT)."""
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()  # main.go:28 traps SIGTERM -> app.Stop() -> exit 0
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5.0)
        finally:
            self.process = None
            if self._log_fh is not None:
                with contextlib.suppress(Exception):
                    self._log_fh.close()
                self._log_fh = None
            self._cleanup_workdir()

    def __enter__(self) -> "ManagedSuiServer":
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        self.stop()


class RuntimeGuard:
    """Playwright Page runtime health guard.

    Traps and fails on ANY:
      - Native browser dialog (window.alert / window.confirm / window.prompt)
      - console.error message
      - Uncaught pageerror
      - External non-localhost HTTP(S) request
      - Unexpected HTTP 4xx/5xx response (unless explicitly allowlisted via allow_http_status)
    """

    def __init__(self, page: Any) -> None:
        self.page = page
        self.native_dialogs: list[tuple[str, str]] = []
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.external_requests: list[tuple[str, str]] = []
        self.http_errors: list[tuple[int, str, str]] = []
        self.all_requests: list[tuple[str, str]] = []
        self.all_responses: list[tuple[int, str, str, dict[str, str]]] = []
        self._allowed_statuses: list[tuple[int, str]] = []

        self._on_dialog = self._handle_dialog
        self._on_console = self._handle_console
        self._on_pageerror = self._handle_pageerror
        self._on_request = self._handle_request
        self._on_response = self._handle_response

        page.on("dialog", self._on_dialog)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _handle_dialog(self, dialog: Any) -> None:
        self.native_dialogs.append((str(dialog.type), str(dialog.message)))
        with contextlib.suppress(Exception):
            dialog.dismiss()

    def _handle_console(self, msg: Any) -> None:
        if getattr(msg, "type", "") != "error":
            return
        text = str(msg.text)
        # Chromium automatically emits a console error for HTTP >= 400 responses even when
        # explicitly allowlisted via guard.allow_http_status(code, path_substr).
        if self._allowed_statuses and "failed to load resource" in text.lower():
            m = re.search(r"status of (\d{3})\b", text)
            if m:
                code = int(m.group(1))
                loc = getattr(msg, "location", None)
                loc_url = str(loc.get("url", "")) if isinstance(loc, dict) else ""
                if self._is_status_allowed(code, loc_url) or (
                    not loc_url and any(c == code for c, _ in self._allowed_statuses)
                ):
                    return
        self.console_errors.append(text)

    def _handle_pageerror(self, err: Any) -> None:
        self.page_errors.append(str(err))

    def _handle_request(self, request: Any) -> None:
        url = str(request.url)
        method = str(request.method)
        self.all_requests.append((method, url))
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme in ("http", "https", "ws", "wss"):
            host = (parsed.hostname or "").lower()
            if host not in LOCALHOST_HOSTS:
                self.external_requests.append((method, url))

    def _handle_response(self, response: Any) -> None:
        status = int(response.status)
        url = str(response.url)
        method = str(response.request.method)
        hdrs: dict[str, str] = {}
        with contextlib.suppress(Exception):
            hdrs = {k.lower(): v for k, v in response.headers.items()}
        self.all_responses.append((status, method, url, hdrs))
        if status >= 400:
            if not self._is_status_allowed(status, url):
                self.http_errors.append((status, method, url))

    def _is_status_allowed(self, status: int, url: str) -> bool:
        for allowed_code, path_substr in self._allowed_statuses:
            if status == allowed_code and (not path_substr or path_substr in url):
                return True
        return False

    @contextlib.contextmanager
    def allow_http_status(self, status: int, path_substr: str = "") -> Iterator[None]:
        rule = (int(status), str(path_substr))
        self._allowed_statuses.append(rule)
        try:
            yield
        finally:
            if rule in self._allowed_statuses:
                self._allowed_statuses.remove(rule)

    def clear(self) -> None:
        self.native_dialogs.clear()
        self.console_errors.clear()
        self.page_errors.clear()
        self.external_requests.clear()
        self.http_errors.clear()

    def violations(
        self,
        *,
        allow_external: bool = False,
        allow_http_errors: bool = False,
        allow_console_errors: bool = False,
    ) -> list[str]:
        issues: list[str] = []
        if self.native_dialogs:
            issues.append(f"Native browser dialog(s) triggered: {self.native_dialogs}")
        if self.page_errors:
            issues.append(f"Uncaught pageerror(s): {self.page_errors}")
        if not allow_console_errors and self.console_errors:
            issues.append(f"console.error message(s): {self.console_errors}")
        if not allow_external and self.external_requests:
            issues.append(f"External non-localhost request(s): {self.external_requests}")
        if not allow_http_errors and self.http_errors:
            issues.append(f"Unexpected HTTP 4xx/5xx response(s): {self.http_errors}")
        return issues

    def assert_clean(
        self,
        *,
        allow_external: bool = False,
        allow_http_errors: bool = False,
        allow_console_errors: bool = False,
    ) -> None:
        issues = self.violations(
            allow_external=allow_external,
            allow_http_errors=allow_http_errors,
            allow_console_errors=allow_console_errors,
        )
        if issues:
            raise AssertionError("RuntimeGuard detected browser runtime violation(s):\n  - " + "\n  - ".join(issues))


def launch_chromium(playwright: Any, *, headed: bool = False) -> Any:
    """Launch Chromium with --disable-dev-shm-usage and container --no-sandbox fallback."""
    base_args = ["--disable-dev-shm-usage"]
    try:
        return playwright.chromium.launch(headless=not headed, args=base_args)
    except Exception:
        return playwright.chromium.launch(
            headless=not headed,
            args=base_args + ["--no-sandbox", "--disable-setuid-sandbox"],
        )


class E2EEnv:
    """Shared server + single Chromium browser pool with strictly isolated BrowserContexts."""

    def __init__(
        self,
        binary: Path,
        *,
        headed: bool = False,
        screenshots_dir: Optional[Path] = None,
    ) -> None:
        self.binary = Path(binary).resolve()
        self.headed = headed
        self.screenshots_dir = Path(screenshots_dir).resolve() if screenshots_dir else None

        self._playwright: Any = None
        self.browser: Any = None
        self._primary: Optional[ManagedSuiServer] = None
        self._subpath: Optional[ManagedSuiServer] = None
        self._auth_states: dict[str, dict[str, Any]] = {}
        self._api_clients: dict[str, SuiApiClient] = {}
        self._baseline_configs: dict[str, dict[str, Any]] = {}
        self._baseline_settings: dict[str, dict[str, str]] = {}
        self._used_ports: set[int] = set()
        self.current_test_id: str = "unknown"
        self.last_screenshot: Optional[Path] = None

    @property
    def primary(self) -> ManagedSuiServer:
        if self._primary is None:
            raise RuntimeError("E2EEnv has not been entered")
        return self._primary

    @property
    def subpath(self) -> ManagedSuiServer:
        """Lazily boot secondary /ci/ server only when a subpath test requests it."""
        if self._subpath is None:
            srv = ManagedSuiServer(self.binary, "/ci/", exclude_ports=self._used_ports).start()
            self._used_ports.update({srv.web_port, srv.sub_port})
            self._subpath = srv
            api = srv.api_client(authenticated=True, shared_server_guard=True)
            self._api_clients["subpath"] = api
            self._baseline_configs["subpath"] = api.snapshot_config()
            self._baseline_settings["subpath"] = api.snapshot_settings()
        return self._subpath

    def get_server(self, server: str = "primary") -> ManagedSuiServer:
        if server == "primary":
            return self.primary
        if server == "subpath":
            return self.subpath
        raise ValueError(f"Unknown server key: {server!r}")

    def api(self, server: str = "primary") -> SuiApiClient:
        if server == "subpath":
            _ = self.subpath  # ensure lazily booted
        client = self._api_clients.get(server)
        if client is None:
            srv = self.get_server(server)
            client = srv.api_client(authenticated=True, shared_server_guard=True)
            self._api_clients[server] = client
        # Verify session is still live; re-login if expired
        try:
            chk = client.get_json("auth-check", check_success=False)
            if not (chk.get("obj") or {}).get("authenticated"):
                client.login("admin", "admin", require_success=True)
        except Exception:
            client.login("admin", "admin", require_success=True)
        return client

    def cleanup_entities(
        self,
        server: str = "primary",
        prefix: str = "e2e-",
        *,
        restore_config: bool = False,
        restore_settings: bool = False,
    ) -> None:
        api = self.api(server)
        api.cleanup_e2e_entities(
            prefix=prefix,
            baseline_config=self._baseline_configs.get(server) if restore_config else None,
            baseline_settings=self._baseline_settings.get(server) if restore_settings else None,
        )

    def _capture_auth_state(self, server_key: str) -> dict[str, Any]:
        """Perform one real login inside a dedicated per-server BrowserContext and cache storage_state."""
        if server_key in self._auth_states:
            return self._auth_states[server_key]
        srv = self.get_server(server_key)
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            page = ctx.new_page()
            # Perform genuine HTTP form login through browser's own origin cookie store
            resp = page.goto(f"{srv.base_url}login", wait_until="domcontentloaded", timeout=15000)
            if resp is None or resp.status >= 400:
                raise RuntimeError(f"Failed loading {srv.base_url}login for auth_state capture")
            login_result = page.evaluate(
                """async (baseUrl) => {
                    const body = new URLSearchParams({ user: 'admin', pass: 'admin' });
                    const r = await fetch(baseUrl + 'api/login', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: body.toString(),
                        credentials: 'same-origin',
                    });
                    return { status: r.status, json: await r.json() };
                }""",
                srv.base_url,
            )
            if login_result.get("status") != 200 or not (login_result.get("json") or {}).get("success"):
                raise RuntimeError(f"Auth state login failed for {server_key}: {login_result}")
            state = ctx.storage_state()
            self._auth_states[server_key] = state
            return state
        finally:
            ctx.close()

    def _save_failure_screenshot(self, page: Any, label: Optional[str] = None) -> Optional[Path]:
        if self.screenshots_dir is None:
            return None
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", label or self.current_test_id or "failure")
        out_path = self.screenshots_dir / f"{safe}.png"
        with contextlib.suppress(Exception):
            page.screenshot(path=str(out_path), full_page=True)
            self.last_screenshot = out_path
            return out_path
        return None

    @staticmethod
    def build_storage_state(
        base_state: Optional[dict[str, Any]],
        origin: str,
        local_storage: Optional[dict[str, str]],
    ) -> dict[str, Any]:
        """Merge one-shot localStorage seeds into a Playwright storage_state dict.

        Using `storage_state["origins"]` seeds localStorage ONCE at context creation so
        subsequent UI theme/locale/rail mutations persist across `page.reload()` and `page.goto()`.
        """
        state: dict[str, Any] = copy.deepcopy(base_state) if base_state else {"cookies": [], "origins": []}
        if not local_storage:
            return state
        origins: list[dict[str, Any]] = list(state.get("origins") or [])
        target_origin = origin.rstrip("/")
        entry = next((o for o in origins if str(o.get("origin", "")).rstrip("/") == target_origin), None)
        if entry is None:
            entry = {"origin": target_origin, "localStorage": []}
            origins.append(entry)
        merged: dict[str, str] = {
            str(item["name"]): str(item["value"])
            for item in (entry.get("localStorage") or [])
            if "name" in item and "value" in item
        }
        for k, v in local_storage.items():
            merged[str(k)] = str(v)
        entry["localStorage"] = [{"name": k, "value": v} for k, v in merged.items()]
        state["origins"] = origins
        return state

    @contextlib.contextmanager
    def _open_page(
        self,
        *,
        authenticated: bool,
        viewport: tuple[int, int] = (1280, 800),
        color_scheme: str = "light",
        local_storage: Optional[dict[str, str]] = None,
        server: str = "primary",
        check_clean: bool = True,
        test_id: Optional[str] = None,
    ) -> Iterator[tuple[Any, RuntimeGuard]]:
        srv = self.get_server(server)
        kwargs: dict[str, Any] = {
            "viewport": {"width": int(viewport[0]), "height": int(viewport[1])},
            "color_scheme": color_scheme,
        }
        base_state = self._capture_auth_state(server) if authenticated else None
        if authenticated or local_storage:
            kwargs["storage_state"] = self.build_storage_state(base_state, srv.origin, local_storage)
        ctx = self.browser.new_context(**kwargs)
        page = ctx.new_page()
        guard = RuntimeGuard(page)
        try:
            yield page, guard
            if check_clean:
                guard.assert_clean()
        except Exception:
            self._save_failure_screenshot(page, test_id or self.current_test_id)
            raise
        finally:
            with contextlib.suppress(Exception):
                page.close()
            with contextlib.suppress(Exception):
                ctx.close()

    @contextlib.contextmanager
    def auth_page(
        self,
        *,
        viewport: tuple[int, int] = (1280, 800),
        color_scheme: str = "light",
        local_storage: Optional[dict[str, str]] = None,
        server: str = "primary",
        check_clean: bool = True,
        test_id: Optional[str] = None,
    ) -> Iterator[tuple[Any, RuntimeGuard]]:
        """Open a fresh authenticated BrowserContext isolated per server (/app/ vs /ci/)."""
        with self._open_page(
            authenticated=True,
            viewport=viewport,
            color_scheme=color_scheme,
            local_storage=local_storage,
            server=server,
            check_clean=check_clean,
            test_id=test_id,
        ) as pair:
            yield pair

    @contextlib.contextmanager
    def anon_page(
        self,
        *,
        viewport: tuple[int, int] = (1280, 800),
        color_scheme: str = "light",
        local_storage: Optional[dict[str, str]] = None,
        server: str = "primary",
        check_clean: bool = True,
        test_id: Optional[str] = None,
    ) -> Iterator[tuple[Any, RuntimeGuard]]:
        """Open a fresh unauthenticated BrowserContext (no s-ui-next cookie)."""
        with self._open_page(
            authenticated=False,
            viewport=viewport,
            color_scheme=color_scheme,
            local_storage=local_storage,
            server=server,
            check_clean=check_clean,
            test_id=test_id,
        ) as pair:
            yield pair

    def __enter__(self) -> "E2EEnv":
        from playwright.sync_api import sync_playwright

        try:
            self._primary = ManagedSuiServer(self.binary, "/app/", exclude_ports=self._used_ports).start()
            self._used_ports.update({self._primary.web_port, self._primary.sub_port})
            api = self._primary.api_client(authenticated=True, shared_server_guard=True)
            self._api_clients["primary"] = api
            self._baseline_configs["primary"] = api.snapshot_config()
            self._baseline_settings["primary"] = api.snapshot_settings()

            self._playwright = sync_playwright().start()
            self.browser = launch_chromium(self._playwright, headed=self.headed)
            # Pre-warm primary storage_state once
            self._capture_auth_state("primary")
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        # CRITICAL: Close Playwright browser BEFORE stopping ./sui servers so HTTP
        # keep-alive/polling sockets do not cause 30s http.Server.Shutdown drain hangs!
        if self.browser is not None:
            with contextlib.suppress(Exception):
                self.browser.close()
            self.browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None

        if self._subpath is not None:
            with contextlib.suppress(Exception):
                self._subpath.stop()
            self._subpath = None
        if self._primary is not None:
            with contextlib.suppress(Exception):
                self._primary.stop()
            self._primary = None


@dataclass(frozen=True)
class TestCaseMeta:
    tier: int
    area: str
    test_id: str
    title: str
    fn: Callable[[E2EEnv], None]
    areas: tuple[str, ...] = field(default_factory=tuple)


class TestRegistry:
    """Central test registry enforcing valid tier, valid feature area, and unique test_id."""

    def __init__(self) -> None:
        self._tests: list[TestCaseMeta] = []
        self._by_id: dict[str, TestCaseMeta] = {}

    def register(
        self,
        *,
        tier: int,
        area: str,
        test_id: str,
        title: str,
        areas: Optional[tuple[str, ...]] = None,
    ) -> Callable[[Callable[[E2EEnv], None]], Callable[[E2EEnv], None]]:
        if tier not in TIERS:
            raise ValueError(f"Invalid tier {tier!r}; must be in {TIERS}")
        if area not in FEATURE_AREAS:
            raise ValueError(f"Invalid area {area!r}; must be in {FEATURE_AREAS}")
        if not test_id or not isinstance(test_id, str):
            raise ValueError("test_id must be a non-empty string")
        if not title or not isinstance(title, str):
            raise ValueError("title must be a non-empty string")
        extra_areas = tuple(areas) if areas else (area,)
        for a in extra_areas:
            if a not in FEATURE_AREAS:
                raise ValueError(f"Invalid extra area {a!r} on {test_id}")

        def decorator(fn: Callable[[E2EEnv], None]) -> Callable[[E2EEnv], None]:
            if test_id in self._by_id:
                raise ValueError(f"Duplicate test_id registered: {test_id}")
            meta = TestCaseMeta(
                tier=tier,
                area=area,
                test_id=test_id,
                title=title,
                fn=fn,
                areas=extra_areas,
            )
            self._tests.append(meta)
            self._by_id[test_id] = meta
            return fn

        return decorator

    def all_tests(self) -> list[TestCaseMeta]:
        return list(self._tests)

    def filter_tests(
        self,
        *,
        tier: str | int = "all",
        name_filter: Optional[str] = None,
    ) -> list[TestCaseMeta]:
        selected = list(self._tests)
        if str(tier).lower() != "all":
            tier_num = int(tier)
            selected = [t for t in selected if t.tier == tier_num]
        if name_filter:
            needle = name_filter.lower()
            selected = [
                t
                for t in selected
                if needle in t.test_id.lower()
                or needle in t.title.lower()
                or needle in t.area.lower()
            ]
        return selected

    def counts_by_tier_and_area(self) -> dict[int, dict[str, int]]:
        counts: dict[int, dict[str, int]] = {tier: {a: 0 for a in FEATURE_AREAS} for tier in TIERS}
        for t in self._tests:
            counts[t.tier][t.area] += 1
        return counts


REGISTRY = TestRegistry()


def register_test(
    *,
    tier: int,
    area: str,
    test_id: str,
    title: str,
    areas: Optional[tuple[str, ...]] = None,
) -> Callable[[Callable[[E2EEnv], None]], Callable[[E2EEnv], None]]:
    """Decorator registering an opaque-box E2E test in the global REGISTRY."""
    return REGISTRY.register(tier=tier, area=area, test_id=test_id, title=title, areas=areas)
