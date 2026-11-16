// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package api

// Domains & Certificates endpoints (v1, session auth — the router group's
// checkLogin middleware covers every action not in its public list).
// Response envelope is the usual Msg {success, msg, obj}. Action names and
// form fields follow 2s-ui; renewCert / applyCert / acmeStatus / installAcme
// are s-ui-nexus additions. Kept in this file, dispatched from apiHandler.go
// through handleCertPost / handleCertGet, to stay clear of other branches.

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"

	"github.com/gin-gonic/gin"
)

func handleCertGet(c *gin.Context, action string) bool {
	switch action {
	case "certs":
		var certSvc service.CertService
		certs, err := certSvc.List()
		jsonObj(c, certs, err)
	case "detectNginx":
		var acme service.AcmeService
		jsonObj(c, acme.DetectNginx(), nil)
	case "acmeStatus":
		var acme service.AcmeService
		jsonObj(c, acme.Status(), nil)
	default:
		return false
	}
	return true
}

func handleCertPost(c *gin.Context, action string) bool {
	switch action {
	case "issueCert":
		issueCert(c)
	case "renewCert":
		renewCert(c)
	case "deleteCert":
		deleteCert(c)
	case "saveManualCert":
		saveManualCert(c)
	case "applyCert":
		applyCert(c)
	case "syncNginxProxy":
		syncNginxProxy(c)
	case "checkNginxProxy":
		checkNginxProxy(c)
	case "installAcme":
		var acme service.AcmeService
		st, err := acme.Install()
		jsonObj(c, st, err)
	default:
		return false
	}
	return true
}

func formBool(c *gin.Context, key string) bool {
	b, _ := strconv.ParseBool(strings.TrimSpace(c.Request.FormValue(key)))
	return b
}

// issueCert: domain, email, method (auto|standalone|nginx), force, behindProxy.
// Synchronous like 2s-ui (up to a few minutes on first run: acme.sh/socat
// install + issuance); one issuance at a time, a concurrent call is refused.
func issueCert(c *gin.Context) {
	var settings service.SettingService
	domain := strings.ToLower(strings.TrimSpace(c.Request.FormValue("domain")))
	email := strings.TrimSpace(c.Request.FormValue("email"))
	if email == "" {
		email, _ = settings.GetAcmeEmail()
	}
	method := c.Request.FormValue("method")
	force := formBool(c, "force")
	// The proxy is a consumer of the certificate when a side behind nginx uses
	// this domain; the form wins when it says so explicitly (unsaved switch).
	behindProxy := false
	if v := c.Request.FormValue("behindProxy"); v != "" {
		behindProxy = v == "true"
	} else {
		webDomain, _ := settings.GetWebDomain()
		subDomain, _ := settings.GetSubDomain()
		webNginx, _ := settings.GetWebNginx()
		subNginx, _ := settings.GetSubNginx()
		behindProxy = (webNginx && strings.EqualFold(domain, webDomain)) ||
			(subNginx && strings.EqualFold(domain, subDomain))
	}
	var acme service.AcmeService
	res, err := acme.IssueWeb(domain, email, method, force, behindProxy)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "issued certificate for ", domain, " via ", res.Method)
	if email != "" {
		if cur, _ := settings.GetAcmeEmail(); cur == "" {
			_ = settings.SetAcmeEmail(email)
		}
	}
	jsonMsgObj(c, "", res, nil)
}

// renewCert: domain, force.
func renewCert(c *gin.Context) {
	var acme service.AcmeService
	res, err := acme.RenewCert(c.Request.FormValue("domain"), formBool(c, "force"))
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "renew certificate ", res.Domain, " renewed=", res.Renewed)
	jsonMsgObj(c, "", res, nil)
}

// deleteCert: domain. Refused while the panel or subscription uses it.
func deleteCert(c *gin.Context) {
	var certSvc service.CertService
	domain := c.Request.FormValue("domain")
	if err := certSvc.Delete(domain); err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "deleted certificate ", domain)
	pureJsonMsg(c, true, "")
}

// saveManualCert: domain, certFile, keyFile (absolute paths on the server).
func saveManualCert(c *gin.Context) {
	domain := strings.TrimSpace(c.Request.FormValue("domain"))
	certFile := strings.TrimSpace(c.Request.FormValue("certFile"))
	keyFile := strings.TrimSpace(c.Request.FormValue("keyFile"))
	var certSvc service.CertService
	existing, found, err := certSvc.FindByDomain(domain)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	if found && existing.Managed {
		pureJsonMsg(c, false, fmt.Sprintf("%s 的证书已由 acme.sh 自动续期,无需手动登记", domain))
		return
	}
	if err := certSvc.SaveManual(domain, certFile, keyFile); err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "registered certificate ", domain)
	pureJsonMsg(c, true, "")
}

// applyCert: domain, targets ("web", "sub" or "web,sub"), setDomain, force.
func applyCert(c *gin.Context) {
	var targets []string
	for _, t := range strings.Split(c.Request.FormValue("targets"), ",") {
		if t = strings.TrimSpace(t); t != "" {
			targets = append(targets, t)
		}
	}
	var certSvc service.CertService
	res, err := certSvc.Apply(c.Request.FormValue("domain"), targets, formBool(c, "setDomain"), formBool(c, "force"))
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "applied certificate ", res.Domain, " to ", strings.Join(targets, ","))
	jsonMsgObj(c, "", res, nil)
}

// proxyForm is one side (panel or subscription) of the reverse-proxy form.
// Fields absent from the form fall back to the saved settings; the *Set flags
// distinguish "sent empty" from "not sent" (see 2s-ui for why each matters).
type proxyForm struct {
	enabled bool
	domain  string
	path    string
	listen  string
	port    int
	cert    string
	key     string
}

func readProxyForm(c *gin.Context, prefix string) proxyForm {
	var s service.SettingService
	web := prefix == "web"
	get := func(name string) string { return c.Request.FormValue(prefix + name) }
	pick := func(webFn, subFn func() (string, error)) string {
		var v string
		if web {
			v, _ = webFn()
		} else {
			v, _ = subFn()
		}
		return v
	}

	f := proxyForm{enabled: get("Nginx") == "true"}
	if get("DomainSet") == "true" {
		f.domain = get("Domain")
	} else if f.domain = get("Domain"); f.domain == "" {
		f.domain = pick(s.GetWebDomain, s.GetSubDomain)
	}
	if v := get("Port"); v != "" {
		f.port, _ = strconv.Atoi(v)
	}
	if f.port <= 0 {
		if web {
			f.port, _ = s.GetPort()
		} else {
			f.port, _ = s.GetSubPort()
		}
	}
	if f.path = get("Path"); f.path == "" {
		f.path = pick(s.GetWebPath, s.GetSubPath)
	}
	if get("ListenSet") == "true" {
		f.listen = get("Listen")
	} else {
		f.listen = pick(s.GetListen, s.GetSubListen)
	}
	if get("CertSet") == "true" {
		f.cert = get("CertFile")
		f.key = get("KeyFile")
	} else {
		if f.cert = get("CertFile"); f.cert == "" {
			f.cert = pick(s.GetCertFile, s.GetSubCertFile)
		}
		if f.key = get("KeyFile"); f.key == "" {
			f.key = pick(s.GetKeyFile, s.GetSubKeyFile)
		}
	}
	return f
}

func proxySides(c *gin.Context) []service.ProxySide {
	web := readProxyForm(c, "web")
	sub := readProxyForm(c, "sub")
	return []service.ProxySide{
		{Name: "panel", Enabled: web.enabled, Domain: web.domain, Path: web.path,
			Listen: web.listen, Port: web.port, CertFile: web.cert, KeyFile: web.key},
		{Name: "subscription", Enabled: sub.enabled, Domain: sub.domain, Path: sub.path,
			Listen: sub.listen, Port: sub.port, CertFile: sub.cert, KeyFile: sub.key},
	}
}

// syncNginxProxy writes/verifies the vhosts for the sides switched on in the
// form (webNginx/subNginx=true) and removes ours that are no longer wanted.
// The frontend calls it BEFORE saving webNginx/subNginx=true and restarting;
// only once it succeeds is it safe to move a side to plaintext. Switching the
// panel side off is finished by the startup reconciliation (app/https.go).
func syncNginxProxy(c *gin.Context) {
	var acme service.AcmeService
	specs, err := service.BuildVhostSpecs(proxySides(c)...)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	res, err := acme.SyncVhosts(specs)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	logger.Audit(GetLoginUser(c), "synced nginx reverse proxy (", len(res), " vhost(s))")
	jsonMsgObj(c, "", res, nil)
}

// checkNginxProxy is the read-only twin: same form, no write, no reload.
func checkNginxProxy(c *gin.Context) {
	var acme service.AcmeService
	specs, err := service.BuildVhostSpecs(proxySides(c)...)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	drift, err := acme.CheckVhosts(specs)
	if err != nil {
		pureJsonMsg(c, false, err.Error())
		return
	}
	jsonMsgObj(c, "", map[string]bool{"drift": drift}, nil)
}
