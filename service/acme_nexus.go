// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package service

// s-ui-nexus additions around the ported acme.go: an injectable command runner
// (so tests can mock acme.sh / nginx / systemctl), configurable directories,
// a Linux-only guard, nginx reload that also works without systemd, explicit
// renewal (single domain and the daily cron) and acme.sh status/installation.

import (
	"net/mail"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/network"
	"github.com/felixbennettio/s-ui-next/util/common"
)

// CmdRunner runs one external command. Every command the HTTPS feature runs
// goes through it with argv-style arguments; nothing built from user input is
// ever handed to a shell. The only `sh -c` is the constant acme.sh installer.
type CmdRunner interface {
	Run(timeout time.Duration, home, name string, args ...string) (string, error)
}

type execRunner struct{}

// acmeExec is the runner in use; tests replace it.
var acmeExec CmdRunner = execRunner{}

func acmeRun(timeout time.Duration, home, name string, args ...string) (string, error) {
	return acmeExec.Run(timeout, home, name, args...)
}

// hostGOOS is runtime.GOOS, overridable in tests.
var hostGOOS = runtime.GOOS

// acmeSupported: acme.sh issuance and nginx generation are Linux-only.
// (2s-ui only excluded Windows; s-ui-nexus also excludes darwin/BSD, where
// none of the paths and service managers below apply.)
func acmeSupported() bool { return hostGOOS == "linux" }

func envDir(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

// certBaseDir is where issued certificates are installed: <dir>/<domain>/
// fullchain.pem + privkey.pem. Same as the s-ui.sh script by default;
// SUI_CERT_DIR overrides it (tests, non-root layouts).
func certBaseDir() string { return envDir("SUI_CERT_DIR", "/root/cert") }

// nginxConfDir is where generated nginx files go (ACME validation blocks and
// reverse-proxy vhosts). SUI_NGINX_CONF_DIR overrides it, e.g. /etc/nginx/http.d
// on Alpine.
func nginxConfDir() string { return envDir("SUI_NGINX_CONF_DIR", "/etc/nginx/conf.d") }

// reloadNginx reloads nginx: through systemd when systemd runs it, otherwise
// with `nginx -s reload` (containers, OpenRC, hand-started nginx).
func reloadNginx() (string, error) {
	if out, err := acmeRun(cmdDetectTO, "/root", "systemctl", "is-active", "nginx"); err == nil && strings.TrimSpace(out) == "active" {
		return acmeRun(cmdDetectTO, "/root", "systemctl", "reload", "nginx")
	}
	return acmeRun(cmdDetectTO, "/root", "nginx", "-s", "reload")
}

// nginxPidAlive reports whether an nginx master process is alive according to
// its pid file. Variable so tests can stub it.
var nginxPidAlive = func() bool {
	for _, p := range []string{"/run/nginx.pid", "/var/run/nginx.pid"} {
		raw, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
		if err != nil || pid <= 0 {
			continue
		}
		if _, err := os.Stat(filepath.Join("/proc", strconv.Itoa(pid))); err == nil {
			return true
		}
	}
	return false
}

// validEmail accepts an empty string (email is optional) or one bare address.
// It goes to acme.sh as an argv element, but a leading '-' would still read as
// an option, and anything net/mail rewrites (display names, comments) is refused.
func validEmail(e string) bool {
	if e == "" {
		return true
	}
	if len(e) > 254 || strings.HasPrefix(e, "-") || strings.ContainsAny(e, " \t\r\n'\"`;|&$<>()\\") {
		return false
	}
	addr, err := mail.ParseAddress(e)
	if err != nil || addr.Address != e || addr.Name != "" {
		return false
	}
	at := strings.LastIndexByte(e, '@')
	return at > 0 && validDomain(e[at+1:])
}

// notifyCertsChanged forces the panel/subscription TLS reloaders to re-read
// their files (mtime polling already catches renewals; this is the explicit
// signal after we know files changed).
func notifyCertsChanged() {
	for name, err := range network.ReloadCerts() {
		logger.Warning("reload ", name, " certificate failed (still serving the previous one): ", err)
	}
}

// acmeDataDir is acme.sh's own home (the directory holding the acme.sh script
// and one sub-directory per certificate). "" when acme.sh is not installed.
func acmeDataDir() string {
	bin, _ := resolveAcmeSh()
	if bin == "" {
		return ""
	}
	return filepath.Dir(bin)
}

// AcmeStatus is what the Domains page shows about acme.sh.
type AcmeStatus struct {
	Supported bool   `json:"supported"` // Linux only
	Installed bool   `json:"installed"`
	Path      string `json:"path"`
	Version   string `json:"version"`
	CertDir   string `json:"certDir"` // where issued certs are installed
	Root      bool   `json:"root"`    // panel runs as root (port 80 / /root/cert need it)
}

func (a *AcmeService) Status() AcmeStatus {
	st := AcmeStatus{Supported: acmeSupported(), CertDir: certBaseDir(), Root: os.Geteuid() == 0}
	if !st.Supported {
		return st
	}
	bin, home := resolveAcmeSh()
	if bin == "" {
		return st
	}
	st.Installed, st.Path = true, bin
	if out, err := acmeRun(cmdDetectTO, home, bin, "--version"); err == nil {
		st.Version = parseAcmeVersion(out)
	}
	return st
}

// parseAcmeVersion picks "v3.0.7" out of `acme.sh --version`, which prints the
// project URL on one line and the version on the next.
func parseAcmeVersion(out string) string {
	lines := strings.Split(strings.TrimSpace(out), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		l := strings.TrimSpace(lines[i])
		if len(l) > 1 && l[0] == 'v' && l[1] >= '0' && l[1] <= '9' {
			return l
		}
	}
	return ""
}

// Install makes sure acme.sh is installed (it is also installed on demand by
// the first issuance) and returns the resulting status.
func (a *AcmeService) Install() (AcmeStatus, error) {
	if !acmeSupported() {
		return a.Status(), common.NewError("自动 HTTPS 仅支持 Linux")
	}
	if _, _, err := ensureAcmeSh(); err != nil {
		return a.Status(), err
	}
	return a.Status(), nil
}

// RenewResult reports one renewal.
type RenewResult struct {
	Domain  string `json:"domain"`
	Renewed bool   `json:"renewed"` // false: acme.sh skipped it (not due yet)
	Output  string `json:"output"`
}

// acmeSkipped recognises acme.sh's "not due yet" answer (exit code 2).
func acmeSkipped(out string) bool {
	return strings.Contains(out, "Skipping. Next renewal time") ||
		strings.Contains(out, "Skip, Next renewal time")
}

// RenewCert renews one acme.sh-managed certificate with the validation method
// it was issued with (acme.sh remembers it in the domain conf). force renews
// even when not due — it counts against Let's Encrypt rate limits.
func (a *AcmeService) RenewCert(domain string, force bool) (*RenewResult, error) {
	if !acmeSupported() {
		return nil, common.NewError("自动 HTTPS 仅支持 Linux")
	}
	domain = strings.ToLower(strings.TrimSpace(domain))
	if !validCertDomain(domain) {
		return nil, common.NewErrorf("域名无效: %q", domain)
	}
	if !acmeIssuing.TryLock() {
		return nil, common.NewError("已有证书申请/续期正在进行,请稍后再试")
	}
	defer acmeIssuing.Unlock()

	bin, home := resolveAcmeSh()
	if bin == "" {
		return nil, common.NewError("未安装 acme.sh,无法续期")
	}
	args := []string{"--renew", "-d", domain}
	if st, err := os.Stat(filepath.Join(filepath.Dir(bin), domain+"_ecc")); err == nil && st.IsDir() {
		args = append(args, "--ecc")
	}
	if force {
		args = append(args, "--force")
	}
	out, err := acmeRun(acmeIssueTO, home, bin, args...)
	if err != nil {
		if acmeSkipped(out) {
			return &RenewResult{Domain: domain, Renewed: false, Output: out}, nil
		}
		return nil, common.NewErrorf("续期失败:\n%s", out)
	}
	notifyCertsChanged()
	return &RenewResult{Domain: domain, Renewed: !acmeSkipped(out), Output: out}, nil
}

// RenewDue runs acme.sh's own renewal pass (`--cron`), which renews whatever is
// due and runs each certificate's reloadcmd, then hot-reloads the panel and
// subscription certificates. It is a no-op when acme.sh is not installed or
// another issuance holds the lock (the next day's run catches up).
func (a *AcmeService) RenewDue() (string, error) {
	if !acmeSupported() {
		return "", nil
	}
	bin, home := resolveAcmeSh()
	if bin == "" {
		return "", nil
	}
	if !acmeIssuing.TryLock() {
		return "", common.NewError("证书申请正在进行,跳过本次续期检查")
	}
	defer acmeIssuing.Unlock()
	out, err := acmeRun(acmeIssueTO*3, home, bin, "--cron", "--home", filepath.Dir(bin))
	notifyCertsChanged()
	if err != nil {
		return out, common.NewErrorf("acme.sh --cron 失败:\n%s", out)
	}
	return out, nil
}

// HasGeneratedVhosts reports whether any reverse-proxy file this panel family
// generated sits in the nginx conf dir (so startup knows whether reconciling
// with an empty wanted set can change anything).
func (a *AcmeService) HasGeneratedVhosts() bool {
	return acmeSupported() && len(listProxyConfs(nginxConfDir())) > 0
}
