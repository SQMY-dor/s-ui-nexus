// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package service

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/felixbennettio/s-ui-next/database"
	projectLogger "github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/network"

	"github.com/op/go-logging"
)

// ---- test doubles ----------------------------------------------------------

type fakeCall struct {
	name string
	args []string
}

type fakeRunner struct {
	mu    sync.Mutex
	calls []fakeCall
	fn    func(name string, args []string) (string, error)
}

func (f *fakeRunner) Run(_ time.Duration, _ string, name string, args ...string) (string, error) {
	f.mu.Lock()
	f.calls = append(f.calls, fakeCall{name: name, args: append([]string(nil), args...)})
	f.mu.Unlock()
	if f.fn == nil {
		return "", nil
	}
	return f.fn(name, args)
}

func (f *fakeRunner) has(name string, args ...string) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, c := range f.calls {
		if c.name == name && strings.Join(c.args, "\x00") == strings.Join(args, "\x00") {
			return true
		}
	}
	return false
}

type hostEnv struct {
	port80Free bool
}

// withFakeHost swaps every host probe the HTTPS code uses for a controllable
// double, and points all directories at temp dirs.
func withFakeHost(t *testing.T, env *hostEnv, fn func(name string, args []string) (string, error)) *fakeRunner {
	t.Helper()
	projectLogger.InitLogger(logging.ERROR)
	r := &fakeRunner{fn: fn}
	oldExec, oldGOOS, oldBin, oldP80, oldPid, oldV6, oldV4 :=
		acmeExec, hostGOOS, resolveBin, port80Free, nginxPidAlive, ipv6Available, hasGlobalIPv4
	acmeExec = r
	hostGOOS = "linux"
	resolveBin = func(name string) (string, bool) { return name, true }
	port80Free = func() bool { return env.port80Free }
	nginxPidAlive = func() bool { return false }
	ipv6Available = func() bool { return false }
	hasGlobalIPv4 = func() bool { return true }
	t.Cleanup(func() {
		acmeExec, hostGOOS, resolveBin, port80Free, nginxPidAlive, ipv6Available, hasGlobalIPv4 =
			oldExec, oldGOOS, oldBin, oldP80, oldPid, oldV6, oldV4
	})
	t.Setenv("HOME", t.TempDir())
	t.Setenv("SUI_ACME_HOME", filepath.Join(t.TempDir(), ".acme.sh"))
	t.Setenv("SUI_CERT_DIR", t.TempDir())
	t.Setenv("SUI_NGINX_CONF_DIR", t.TempDir())
	t.Setenv("SUI_DB_FOLDER", t.TempDir())
	return r
}

// fakeAcmeSh creates an executable acme.sh in $SUI_ACME_HOME.
func fakeAcmeSh(t *testing.T) string {
	t.Helper()
	dir := os.Getenv("SUI_ACME_HOME")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "acme.sh")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\nexit 0\n"), 0755); err != nil {
		t.Fatal(err)
	}
	return bin
}

func genCertPEM(t *testing.T, cn string, notAfter time.Time) (certPEM, keyPEM []byte) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: cn},
		Issuer:       pkix.Name{CommonName: cn},
		DNSNames:     []string{cn},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     notAfter,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	kb, _ := x509.MarshalECPrivateKey(key)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kb})
}

func writeCertPair(t *testing.T, dir, cn string, notAfter time.Time) (string, string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatal(err)
	}
	c, k := genCertPEM(t, cn, notAfter)
	cp, kp := filepath.Join(dir, "fullchain.pem"), filepath.Join(dir, "privkey.pem")
	if err := os.WriteFile(cp, c, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(kp, k, 0600); err != nil {
		t.Fatal(err)
	}
	return cp, kp
}

// ---- validation ------------------------------------------------------------

func TestValidDomainRejectsInjection(t *testing.T) {
	good := []string{"a.com", "panel.Example.COM", "xn--fiqs8s.cn", "a-b.c-d.example.org", "1a.example.com"}
	bad := []string{
		"", "a", "localhost", "-d.com", "--force.com", "a..com", ".a.com", "a.com.",
		"a.com;id", "$(id).a.com", "`id`.a.com", "a.com|sh", "a.com&&x", "a b.com",
		"a.com\nb.com", "../etc/passwd", "a/b.com", "a.-b.com", "a.b-.com", "1.2.3.4", "::1",
		"*.a.com", "a.com'", strings.Repeat("a", 64) + ".com",
	}
	for _, d := range good {
		if !validDomain(d) {
			t.Errorf("validDomain(%q) = false, want true", d)
		}
	}
	for _, d := range bad {
		if validDomain(d) {
			t.Errorf("validDomain(%q) = true, want false", d)
		}
	}
	if !validCertDomain("*.a.com") || validCertDomain("*.-a.com") || validCertDomain("**.a.com") {
		t.Error("validCertDomain wildcard handling wrong")
	}
}

func TestValidEmail(t *testing.T) {
	for _, e := range []string{"", "me@a.com", "first.last+tag@sub.example.org"} {
		if !validEmail(e) {
			t.Errorf("validEmail(%q) = false", e)
		}
	}
	for _, e := range []string{"-x@a.com", "me@a.com --force", "Me <me@a.com>", "me@localhost", "me@a.com;id", "noat", "me@$(id).com"} {
		if validEmail(e) {
			t.Errorf("validEmail(%q) = true", e)
		}
	}
}

func TestIssueRejectsBadInputBeforeRunningAnything(t *testing.T) {
	env := &hostEnv{port80Free: true}
	r := withFakeHost(t, env, nil)
	fakeAcmeSh(t)
	var a AcmeService
	for _, d := range []string{"a.com;reboot", "-d", "$(touch /tmp/x).com", "a.com --force"} {
		if _, err := a.IssueWeb(d, "", "auto", false, false); err == nil {
			t.Errorf("IssueWeb(%q) succeeded", d)
		}
	}
	if _, err := a.IssueWeb("a.example.com", "x@a.com --server evil", "auto", false, false); err == nil {
		t.Error("IssueWeb accepted a bad email")
	}
	if len(r.calls) != 0 {
		t.Fatalf("commands ran for rejected input: %+v", r.calls)
	}
}

func TestLinuxOnlyGuard(t *testing.T) {
	withFakeHost(t, &hostEnv{port80Free: true}, nil)
	hostGOOS = "darwin"
	var a AcmeService
	if _, err := a.IssueWeb("a.example.com", "", "auto", false, false); err == nil {
		t.Error("IssueWeb ran on darwin")
	}
	if _, err := a.RenewCert("a.example.com", false); err == nil {
		t.Error("RenewCert ran on darwin")
	}
	if st := a.Status(); st.Supported {
		t.Error("Status reports supported on darwin")
	}
	if _, err := a.SyncVhosts([]VhostOptions{{Domain: "a.example.com"}}); err == nil {
		t.Error("SyncVhosts ran on darwin")
	}
}

// ---- mode selection --------------------------------------------------------

func TestResolveMethod(t *testing.T) {
	env := &hostEnv{}
	nginxActive := false
	withFakeHost(t, env, func(name string, args []string) (string, error) {
		if name == "systemctl" && len(args) == 2 && args[0] == "is-active" {
			if nginxActive {
				return "active", nil
			}
			return "inactive", errors.New("exit 3")
		}
		return "", nil
	})
	var a AcmeService
	cases := []struct {
		method       string
		free, active bool
		want         string // "" = error
	}{
		{"auto", true, false, methodStandalone},
		{"", true, true, methodStandalone}, // 80 free wins, as in 2s-ui
		{"auto", false, true, methodNginx},  // nginx owns :80
		{"auto", false, false, ""},          // something else owns :80
		{"standalone", true, false, methodStandalone},
		{"standalone", false, true, ""},
		{"nginx", false, true, methodNginx},
		{"nginx", true, false, ""},
		{"dns", true, false, ""},
	}
	for _, c := range cases {
		env.port80Free, nginxActive = c.free, c.active
		got, err := a.resolveMethod(c.method)
		if c.want == "" {
			if err == nil {
				t.Errorf("resolveMethod(%q, free=%v, nginx=%v) = %q, want error", c.method, c.free, c.active, got)
			}
			continue
		}
		if err != nil || got != c.want {
			t.Errorf("resolveMethod(%q, free=%v, nginx=%v) = %q, %v; want %q", c.method, c.free, c.active, got, err, c.want)
		}
	}
	env.port80Free = false
	if _, err := a.resolveMethod("standalone"); err == nil || !strings.Contains(err.Error(), "80") {
		t.Errorf("busy-port error should mention port 80, got %v", err)
	}
}

func TestIssueStandaloneArgv(t *testing.T) {
	env := &hostEnv{port80Free: true}
	r := withFakeHost(t, env, nil)
	bin := fakeAcmeSh(t)
	var a AcmeService
	res, err := a.IssueWeb("A.Example.com", "me@example.com", "auto", true, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Method != methodStandalone || res.ReloadCmd != "" {
		t.Fatalf("result = %+v", res)
	}
	if want := filepath.Join(certBaseDir(), "A.Example.com", "fullchain.pem"); res.CertFile != want {
		t.Fatalf("CertFile = %q, want %q", res.CertFile, want)
	}
	if !r.has(bin, "--issue", "-d", "A.Example.com", "--accountemail", "me@example.com", "--standalone", "--httpport", "80", "--force") {
		t.Fatalf("issue argv not found in %+v", r.calls)
	}
	if !r.has(bin, "--installcert", "-d", "A.Example.com", "--key-file", res.KeyFile, "--fullchain-file", res.CertFile) {
		t.Fatalf("installcert argv not found in %+v", r.calls)
	}
	for _, c := range r.calls {
		if c.name == "sh" || c.name == "bash" {
			t.Fatalf("a shell was invoked: %+v", c)
		}
	}
}

func TestIssueNginxModeUsesExistingServerBlock(t *testing.T) {
	env := &hostEnv{port80Free: false}
	dump := "# configuration file /etc/nginx/sites-enabled/a:\nserver {\n listen 80;\n server_name a.example.com;\n}\n"
	r := withFakeHost(t, env, func(name string, args []string) (string, error) {
		switch {
		case name == "systemctl" && args[0] == "is-active":
			return "active", nil
		case name == "nginx" && len(args) == 1 && args[0] == "-T":
			return dump, nil
		}
		return "", nil
	})
	bin := fakeAcmeSh(t)
	var a AcmeService
	res, err := a.IssueWeb("a.example.com", "", "auto", false, false)
	if err != nil {
		t.Fatal(err)
	}
	if res.Method != methodNginx {
		t.Fatalf("method = %q, want nginx", res.Method)
	}
	if !r.has(bin, "--issue", "-d", "a.example.com", "--nginx") {
		t.Fatalf("nginx issue argv missing: %+v", r.calls)
	}
	// systemctl cat nginx succeeded -> nginx consumes the cert -> reloadcmd set
	if res.ReloadCmd == "" {
		t.Fatal("nginx mode should configure a reloadcmd")
	}
	// existing :80 block found, so no validation block was generated
	if m, _ := filepath.Glob(filepath.Join(nginxConfDir(), "s-ui-acme-*.conf")); len(m) != 0 {
		t.Fatalf("unexpected validation block %v", m)
	}
}

func TestIssueConcurrentRefused(t *testing.T) {
	withFakeHost(t, &hostEnv{port80Free: true}, nil)
	fakeAcmeSh(t)
	acmeIssuing.Lock()
	defer acmeIssuing.Unlock()
	var a AcmeService
	if _, err := a.IssueWeb("a.example.com", "", "auto", false, false); err == nil {
		t.Fatal("second concurrent issuance was not refused")
	}
}

// ---- nginx: write, nginx -t, roll back -------------------------------------

func nginxVhostFixture(t *testing.T, nginxT func() (string, error), reloads *int) (*fakeRunner, VhostOptions) {
	t.Helper()
	env := &hostEnv{port80Free: false}
	var r *fakeRunner
	r = withFakeHost(t, env, func(name string, args []string) (string, error) {
		switch {
		case name == "systemctl" && args[0] == "is-active":
			return "active", nil
		case name == "systemctl" && args[0] == "reload":
			*reloads++
			return "", nil
		case name == "nginx" && args[0] == "-v":
			return "nginx version: nginx/1.24.0", nil
		case name == "nginx" && args[0] == "-t":
			return nginxT()
		case name == "nginx" && args[0] == "-T":
			p := proxyConfPath("a.example.com")
			raw, _ := os.ReadFile(p)
			return "# configuration file " + p + ":\n" + string(raw), nil
		}
		return "", nil
	})
	writeCertPair(t, filepath.Join(certBaseDir(), "a.example.com"), "a.example.com", time.Now().Add(90*24*time.Hour))
	opt := VhostOptions{Domain: "a.example.com", Endpoints: []ProxyEndpoint{{Name: "panel", Path: "/app/", Port: 2095}}}
	return r, opt
}

func TestEnsureVhostRollbackDeletesNewFile(t *testing.T) {
	reloads := 0
	_, opt := nginxVhostFixture(t, func() (string, error) {
		return "nginx: [emerg] unknown directive", errors.New("exit 1")
	}, &reloads)
	var a AcmeService
	if _, err := a.EnsureVhost(opt); err == nil || !strings.Contains(err.Error(), "nginx -t") {
		t.Fatalf("EnsureVhost err = %v, want nginx -t failure", err)
	}
	if _, err := os.Stat(proxyConfPath("a.example.com")); !os.IsNotExist(err) {
		t.Fatalf("new vhost not rolled back: %v", err)
	}
	if reloads == 0 {
		t.Fatal("rollback did not reload nginx")
	}
}

func TestEnsureVhostRollbackRestoresPrevious(t *testing.T) {
	reloads := 0
	_, opt := nginxVhostFixture(t, func() (string, error) {
		return "nginx: [emerg] bind() failed", errors.New("exit 1")
	}, &reloads)
	prev := []byte("# previous working config\n")
	path := proxyConfPath("a.example.com")
	if err := os.WriteFile(path, prev, 0644); err != nil {
		t.Fatal(err)
	}
	var a AcmeService
	if _, err := a.EnsureVhost(opt); err == nil {
		t.Fatal("EnsureVhost succeeded despite nginx -t failure")
	}
	got, err := os.ReadFile(path)
	if err != nil || string(got) != string(prev) {
		t.Fatalf("previous config not restored: %q, %v", got, err)
	}
}

func TestEnsureVhostSuccessAndShortCircuit(t *testing.T) {
	reloads := 0
	r, opt := nginxVhostFixture(t, func() (string, error) { return "syntax is ok", nil }, &reloads)
	var a AcmeService
	res, err := a.EnsureVhost(opt)
	if err != nil {
		t.Fatal(err)
	}
	if res.ConfFile != proxyConfPath("a.example.com") || len(res.URLs) != 1 || res.URLs[0] != "https://a.example.com/app/" {
		t.Fatalf("result = %+v", res)
	}
	raw, _ := os.ReadFile(res.ConfFile)
	if !strings.Contains(string(raw), "proxy_pass http://127.0.0.1:2095;") ||
		!strings.Contains(string(raw), filepath.Join(certBaseDir(), "a.example.com", "fullchain.pem")) {
		t.Fatalf("unexpected vhost:\n%s", raw)
	}
	if reloads != 1 {
		t.Fatalf("reloads = %d, want 1", reloads)
	}
	// Same content again: no rewrite, no reload.
	if _, err := a.EnsureVhost(opt); err != nil {
		t.Fatal(err)
	}
	if reloads != 1 {
		t.Fatalf("identical config reloaded nginx again (reloads=%d)", reloads)
	}
	_ = r
}

func TestReloadNginxWithoutSystemd(t *testing.T) {
	r := withFakeHost(t, &hostEnv{}, func(name string, args []string) (string, error) {
		if name == "systemctl" {
			return "", errors.New("systemctl: not found")
		}
		return "", nil
	})
	if _, err := reloadNginx(); err != nil {
		t.Fatal(err)
	}
	if !r.has("nginx", "-s", "reload") {
		t.Fatalf("expected nginx -s reload, got %+v", r.calls)
	}
}

// ---- certificates: expiry parsing, listing, renewal -------------------------

func TestParseLeafPEMSkipsNonCertificateBlocks(t *testing.T) {
	notAfter := time.Now().Add(10 * 24 * time.Hour).Truncate(time.Second)
	c, k := genCertPEM(t, "a.example.com", notAfter)
	bundle := append(append([]byte{}, k...), c...) // key first, as some exports do
	leaf := parseLeafPEM(bundle)
	if leaf == nil {
		t.Fatal("certificate after a key block not found")
	}
	if !leaf.NotAfter.Equal(notAfter.UTC()) {
		t.Fatalf("NotAfter = %v, want %v", leaf.NotAfter, notAfter)
	}
	if parseLeafPEM([]byte("garbage")) != nil || parseLeafPEM(k) != nil {
		t.Fatal("non-certificate input parsed as a certificate")
	}
	dir := t.TempDir()
	cp, _ := writeCertPair(t, dir, "a.example.com", notAfter)
	if got := certNotAfter(cp); got != notAfter.Unix() {
		t.Fatalf("certNotAfter = %d, want %d", got, notAfter.Unix())
	}
	if certNotAfter(filepath.Join(dir, "missing.pem")) != 0 {
		t.Fatal("missing file should give 0")
	}
}

func TestCertStatus(t *testing.T) {
	now := time.Now()
	for _, c := range []struct {
		notAfter int64
		want     string
	}{
		{0, "missing"},
		{now.Add(-time.Minute).Unix(), "expired"},
		{now.Add(5 * 24 * time.Hour).Unix(), "expiring"},
		{now.Add(60 * 24 * time.Hour).Unix(), "valid"},
	} {
		if got := certStatus(c.notAfter, now); got != c.want {
			t.Errorf("certStatus(%d) = %q, want %q", c.notAfter, got, c.want)
		}
	}
}

func TestListCertsFromAcmeHome(t *testing.T) {
	withFakeHost(t, &hostEnv{}, nil)
	bin := fakeAcmeSh(t)
	home := filepath.Dir(bin)
	installed := filepath.Join(certBaseDir(), "a.example.com")
	cp, kp := writeCertPair(t, installed, "a.example.com", time.Now().Add(80*24*time.Hour))
	ecc := filepath.Join(home, "a.example.com_ecc")
	_ = os.MkdirAll(ecc, 0755)
	conf := "Le_Domain='a.example.com'\nLe_Keylength='ec-256'\nLe_API='https://acme-v02.api.letsencrypt.org/directory'\n" +
		"Le_RealFullChainPath='" + cp + "'\nLe_RealKeyPath='" + kp + "'\nLe_NextRenewTime='1900000000'\n"
	if err := os.WriteFile(filepath.Join(ecc, "a.example.com.conf"), []byte(conf), 0644); err != nil {
		t.Fatal(err)
	}
	_ = os.MkdirAll(filepath.Join(home, "dnsapi"), 0755) // internal dir without conf: ignored
	var a AcmeService
	list, err := a.ListCerts()
	if err != nil || len(list) != 1 {
		t.Fatalf("ListCerts = %+v, %v", list, err)
	}
	ci := list[0]
	if ci.Domain != "a.example.com" || !ci.Managed || ci.CA != "Let's Encrypt" || ci.KeyType != "ec-256" ||
		ci.CertFile != cp || ci.NextRenew != 1900000000 || ci.NotAfter == 0 {
		t.Fatalf("unexpected entry %+v", ci)
	}
}

func TestRenewCert(t *testing.T) {
	skip := false
	r := withFakeHost(t, &hostEnv{}, func(name string, args []string) (string, error) {
		if len(args) > 0 && args[0] == "--renew" && skip {
			return "[x] Skipping. Next renewal time is: 2027-01-01", errors.New("exit 2")
		}
		return "Cert success.", nil
	})
	bin := fakeAcmeSh(t)
	_ = os.MkdirAll(filepath.Join(filepath.Dir(bin), "a.example.com_ecc"), 0755)
	var a AcmeService
	res, err := a.RenewCert("A.example.com", true)
	if err != nil || !res.Renewed {
		t.Fatalf("RenewCert = %+v, %v", res, err)
	}
	if !r.has(bin, "--renew", "-d", "a.example.com", "--ecc", "--force") {
		t.Fatalf("renew argv missing: %+v", r.calls)
	}
	skip = true
	res, err = a.RenewCert("a.example.com", false)
	if err != nil || res.Renewed {
		t.Fatalf("skipped renewal should be success with renewed=false: %+v, %v", res, err)
	}
	if _, err := a.RenewCert("a.example.com;id", false); err == nil {
		t.Fatal("bad domain accepted")
	}
}

func TestRenewDueRunsAcmeCron(t *testing.T) {
	r := withFakeHost(t, &hostEnv{}, nil)
	var a AcmeService
	if out, err := a.RenewDue(); err != nil || out != "" || len(r.calls) != 0 {
		t.Fatalf("RenewDue without acme.sh should be a no-op: %q %v %+v", out, err, r.calls)
	}
	bin := fakeAcmeSh(t)
	if _, err := a.RenewDue(); err != nil {
		t.Fatal(err)
	}
	if !r.has(bin, "--cron", "--home", filepath.Dir(bin)) {
		t.Fatalf("acme.sh --cron not run: %+v", r.calls)
	}
}

func TestAcmeStatusVersion(t *testing.T) {
	withFakeHost(t, &hostEnv{}, func(name string, args []string) (string, error) {
		if len(args) == 1 && args[0] == "--version" {
			return "https://github.com/acmesh-official/acme.sh\nv3.0.7", nil
		}
		return "", nil
	})
	var a AcmeService
	if st := a.Status(); !st.Supported || st.Installed {
		t.Fatalf("status before install = %+v", st)
	}
	fakeAcmeSh(t)
	st := a.Status()
	if !st.Installed || st.Version != "v3.0.7" || st.CertDir != certBaseDir() {
		t.Fatalf("status = %+v", st)
	}
}

// ---- CertService: register, list, apply (hot reload), delete guard ---------

func TestCertServiceApplyAndDelete(t *testing.T) {
	withFakeHost(t, &hostEnv{}, nil)
	if err := database.InitDB(filepath.Join(t.TempDir(), "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	var settings SettingService
	if _, err := settings.GetAllSetting(); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	cp, kp := writeCertPair(t, filepath.Join(dir, "panel"), "panel.example.com", time.Now().Add(60*24*time.Hour))
	var cs CertService
	if err := cs.SaveManual("panel.example.com", cp, kp); err != nil {
		t.Fatal(err)
	}
	// Registering a pair that does not cover the domain is refused.
	op, ok := writeCertPair(t, filepath.Join(dir, "other"), "other.example.com", time.Now().Add(60*24*time.Hour))
	if err := cs.SaveManual("panel.example.com", op, ok); err == nil {
		t.Fatal("certificate for another name accepted")
	}

	// Panel runs plaintext: apply writes the settings and asks for a restart.
	network.ReleaseTLS("web")
	res, err := cs.Apply("panel.example.com", []string{"web"}, false, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.RestartRequired) != 1 || len(res.HotReloaded) != 0 || len(res.Warnings) == 0 {
		t.Fatalf("apply (plaintext) = %+v", res)
	}
	if v, _ := settings.GetCertFile(); v != cp {
		t.Fatalf("webCertFile = %q, want %q", v, cp)
	}

	// Panel serves TLS: apply hot-swaps.
	if _, err := network.NewTLSConfig("web", op, ok); err != nil {
		t.Fatal(err)
	}
	defer network.ReleaseTLS("web")
	res, err = cs.Apply("panel.example.com", []string{"web"}, false, false)
	if err != nil || len(res.HotReloaded) != 1 || len(res.RestartRequired) != 0 {
		t.Fatalf("apply (tls) = %+v, %v", res, err)
	}

	// The domain setting must be covered by the certificate unless forced.
	if err := settings.saveSetting("subDomain", "elsewhere.example.com"); err != nil {
		t.Fatal(err)
	}
	if _, err := cs.Apply("panel.example.com", []string{"sub"}, false, false); err == nil {
		t.Fatal("certificate applied to a service whose domain it does not cover")
	}
	if _, err := cs.Apply("panel.example.com", []string{"sub"}, false, true); err != nil {
		t.Fatalf("forced apply failed: %v", err)
	}
	if _, err := cs.Apply("panel.example.com", []string{"nope"}, false, false); err == nil {
		t.Fatal("unknown target accepted")
	}
	res, err = cs.Apply("panel.example.com", []string{"web"}, true, false)
	if err != nil || len(res.RestartRequired) != 1 {
		t.Fatalf("setDomain should require a restart: %+v, %v", res, err)
	}
	if v, _ := settings.GetWebDomain(); v != "panel.example.com" {
		t.Fatalf("webDomain = %q", v)
	}

	list, err := cs.List()
	if err != nil || len(list) != 1 {
		t.Fatalf("List = %+v, %v", list, err)
	}
	if list[0].Status != "valid" || strings.Join(list[0].UsedBy, ",") != "web,sub" {
		t.Fatalf("decorated entry = %+v", list[0])
	}

	// In use -> delete refused; after moving the settings away -> deleted.
	if err := cs.Delete("panel.example.com"); err == nil {
		t.Fatal("deleted a certificate the panel uses")
	}
	for _, k := range []string{"webDomain", "webCertFile", "webKeyFile", "subDomain", "subCertFile", "subKeyFile"} {
		_ = settings.saveSetting(k, "")
	}
	if err := cs.Delete("panel.example.com"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cp); err != nil {
		t.Fatal("deleting a manual registration must not touch the user's files")
	}
	if list, _ := cs.List(); len(list) != 0 {
		t.Fatalf("still listed after delete: %+v", list)
	}
}

func TestProxyVhostSpecsAndSubURI(t *testing.T) {
	withFakeHost(t, &hostEnv{}, nil)
	if err := database.InitDB(filepath.Join(t.TempDir(), "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	var s SettingService
	if _, err := s.GetAllSetting(); err != nil {
		t.Fatal(err)
	}
	_ = s.saveSetting("subDomain", "sub.example.com")
	uri, _ := s.GetFinalSubURI("1.2.3.4")
	if uri != "http://sub.example.com:2096/sub/" {
		t.Fatalf("plain sub URI = %q", uri)
	}
	_ = s.saveSetting("subNginx", "true")
	uri, _ = s.GetFinalSubURI("1.2.3.4")
	if uri != "https://sub.example.com/sub/" {
		t.Fatalf("behind-proxy sub URI = %q", uri)
	}
	sides, err := s.ProxyVhostSpecs()
	if err != nil {
		t.Fatal(err)
	}
	specs, err := BuildVhostSpecs(sides...)
	if err != nil || len(specs) != 1 || specs[0].Domain != "sub.example.com" || specs[0].Endpoints[0].Port != 2096 {
		t.Fatalf("specs = %+v, %v", specs, err)
	}
	if !s.SubBehindProxy() || s.WebBehindProxy() {
		t.Fatal("behind-proxy flags wrong")
	}
}
