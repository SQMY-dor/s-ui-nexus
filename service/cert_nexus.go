// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package service

// s-ui-nexus additions to the ported CertService: list decoration (status,
// usedBy), the delete flow (2s-ui keeps it in api/apiService.go; moved here so
// it is testable without gin) and applying a certificate to the panel / the
// subscription server with hot reload.

import (
	"crypto/tls"
	"errors"
	"path/filepath"
	"strings"
	"time"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/network"
	"github.com/felixbennettio/s-ui-next/util/common"
)

const certExpiringWindow = 30 * 24 * time.Hour

// certStatus classifies a certificate by its expiry (unix seconds, 0 = unreadable).
func certStatus(notAfter int64, now time.Time) string {
	switch {
	case notAfter == 0:
		return "missing"
	case now.Unix() >= notAfter:
		return "expired"
	case time.Unix(notAfter, 0).Sub(now) < certExpiringWindow:
		return "expiring"
	default:
		return "valid"
	}
}

func cleanPath(p string) string {
	p = strings.TrimSpace(p)
	if p == "" {
		return ""
	}
	return filepath.Clean(p)
}

func (c *CertService) decorate(list []CertInfo) {
	all, err := c.settings.GetAllSetting()
	var s map[string]string
	if err == nil {
		s = *all
	}
	now := time.Now()
	for i := range list {
		list[i].Status = certStatus(list[i].NotAfter, now)
		cf := cleanPath(list[i].CertFile)
		if cf == "" || s == nil {
			continue
		}
		if cleanPath(s["webCertFile"]) == cf {
			list[i].UsedBy = append(list[i].UsedBy, "web")
		}
		if cleanPath(s["subCertFile"]) == cf {
			list[i].UsedBy = append(list[i].UsedBy, "sub")
		}
	}
}

// InUse names the services (web / sub) that would break if domain's
// certificate went away: their domain setting is that domain, or their
// certificate paths point at its files or into its managed directory.
// Ported from 2s-ui's ApiService.DeleteCert guard.
func (c *CertService) InUse(domain string, info CertInfo, found bool) []string {
	all, err := c.settings.GetAllSetting()
	if err != nil {
		return nil
	}
	s := *all
	managedDir := filepath.Clean(ManagedCertDir(domain)) + string(filepath.Separator)
	usesFiles := func(paths ...string) bool {
		for _, p := range paths {
			p = cleanPath(p)
			if p == "" {
				continue
			}
			if found && (p == cleanPath(info.CertFile) || p == cleanPath(info.KeyFile)) {
				return true
			}
			if strings.HasPrefix(p, managedDir) {
				return true
			}
		}
		return false
	}
	var inUse []string
	if strings.EqualFold(s["webDomain"], domain) || usesFiles(s["webCertFile"], s["webKeyFile"]) {
		inUse = append(inUse, "web")
	}
	if strings.EqualFold(s["subDomain"], domain) || usesFiles(s["subCertFile"], s["subKeyFile"]) {
		inUse = append(inUse, "sub")
	}
	return inUse
}

// Delete removes a certificate: acme.sh-managed ones stop renewing and lose
// their installed copy; manual ones only lose their registration (the files
// are the user's). Refused while the panel or subscription uses it.
func (c *CertService) Delete(domain string) error {
	domain = strings.ToLower(strings.TrimSpace(domain))
	if domain == "" {
		return common.NewError("domain is required")
	}
	info, found, err := c.FindByDomain(domain)
	if err != nil {
		return err
	}
	if inUse := c.InUse(domain, info, found); len(inUse) > 0 {
		names := make([]string, 0, len(inUse))
		for _, n := range inUse {
			names = append(names, map[string]string{"web": "面板", "sub": "订阅"}[n])
		}
		return common.NewErrorf("%s正在使用 %s 的证书;请先在对应设置里换成别的域名/证书,再删除这张证书",
			strings.Join(names, "和"), domain)
	}
	if found && info.Managed {
		if err := c.acme.RemoveCert(domain); err != nil {
			return err
		}
		if err := c.DeleteManual(domain); err != nil {
			logger.Warning("已删除 acme.sh 证书 ", domain, ",但清理登记记录失败: ", err)
		}
		return nil
	}
	return c.DeleteManual(domain)
}

// ApplyResult tells the caller what applying a certificate did.
type ApplyResult struct {
	Domain   string `json:"domain"`
	CertFile string `json:"certFile"`
	KeyFile  string `json:"keyFile"`
	// Targets whose running server picked the new pair up immediately.
	HotReloaded []string `json:"hotReloaded"`
	// Targets that need a panel restart (restartApp) for the change to take
	// effect: the server runs plaintext today (listener changes), or the
	// domain setting changed (DomainValidator is built at router start).
	RestartRequired []string `json:"restartRequired"`
	Warnings        []string `json:"warnings"`
}

var applyTargets = map[string]struct{ domainKey, certKey, keyKey, nginxKey string }{
	"web": {"webDomain", "webCertFile", "webKeyFile", "webNginx"},
	"sub": {"subDomain", "subCertFile", "subKeyFile", "subNginx"},
}

// Apply points the panel ("web") and/or subscription ("sub") at domain's
// certificate by writing the existing webCertFile/webKeyFile (subCertFile/
// subKeyFile) settings, and hot-swaps it into the running server when that
// server already serves TLS.
//
// setDomain also writes webDomain/subDomain = domain. That turns on
// middleware.DomainValidator, after which the server answers only requests
// whose Host is that domain (access by IP gets 403) — so it is opt-in.
// When the domain setting is non-empty (already, or via setDomain) the
// certificate must cover it unless force is set: a mismatched certificate
// starts fine but every browser rejects it.
func (c *CertService) Apply(domain string, targets []string, setDomain, force bool) (*ApplyResult, error) {
	domain = strings.ToLower(strings.TrimSpace(domain))
	if !validCertDomain(domain) {
		return nil, common.NewErrorf("域名无效: %q", domain)
	}
	if len(targets) == 0 {
		return nil, common.NewError("请选择应用到面板(web)还是订阅(sub)")
	}
	info, found, err := c.FindByDomain(domain)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, common.NewErrorf("找不到 %s 的证书:请先申请或登记", domain)
	}
	if _, err := tls.LoadX509KeyPair(info.CertFile, info.KeyFile); err != nil {
		return nil, common.NewErrorf("证书与私钥无法加载: %v", err)
	}
	leaf := readLeafCert(info.CertFile)
	if leaf == nil {
		return nil, common.NewErrorf("%s 不是有效的 PEM 证书", info.CertFile)
	}
	all, err := c.settings.GetAllSetting()
	if err != nil {
		return nil, err
	}
	s := *all

	res := &ApplyResult{Domain: domain, CertFile: info.CertFile, KeyFile: info.KeyFile,
		HotReloaded: []string{}, RestartRequired: []string{}, Warnings: []string{}}
	seen := map[string]bool{}
	type change struct{ key, value string }
	var changes []change
	var hot []string
	for _, t := range targets {
		t = strings.TrimSpace(t)
		keys, ok := applyTargets[t]
		if !ok {
			return nil, common.NewErrorf("未知的应用目标: %q(只能是 web / sub)", t)
		}
		if seen[t] {
			continue
		}
		seen[t] = true
		effective := strings.TrimSpace(s[keys.domainKey])
		if setDomain {
			effective = domain
		}
		if effective != "" && !force {
			if err := leaf.VerifyHostname(effective); err != nil {
				return nil, common.NewErrorf("%s 的证书不覆盖 %s 设置的域名 %s(可加 force 强制应用): %v", domain, keys.domainKey, effective, err)
			}
		}
		if effective == "" {
			res.Warnings = append(res.Warnings, t+": "+keys.domainKey+" 为空,服务仍接受任意 Host;用 IP 访问时浏览器会报证书名不匹配")
		}
		changes = append(changes, change{keys.certKey, info.CertFile}, change{keys.keyKey, info.KeyFile})
		if setDomain && !strings.EqualFold(s[keys.domainKey], domain) {
			changes = append(changes, change{keys.domainKey, domain})
			res.RestartRequired = append(res.RestartRequired, t)
		}
		if s[keys.nginxKey] == "true" && acmeSupported() {
			// TLS is terminated by nginx: the service itself runs plaintext and the
			// certificate is consumed by the generated vhost.
			res.Warnings = append(res.Warnings, t+": 已开启「由 nginx 反代终结 TLS」,证书由 nginx 使用,请重新同步反代配置(syncNginxProxy)")
			continue
		}
		hot = append(hot, t)
	}

	// Swap into running servers first: if a load fails nothing is saved.
	for _, t := range hot {
		err := network.UpdateCertPaths(t, info.CertFile, info.KeyFile)
		switch {
		case err == nil:
			res.HotReloaded = append(res.HotReloaded, t)
		case errors.Is(err, network.ErrNoSlot):
			if !containsFold(res.RestartRequired, t) {
				res.RestartRequired = append(res.RestartRequired, t)
			}
		default:
			return nil, common.NewErrorf("%s 热加载证书失败,未保存: %v", t, err)
		}
	}
	for _, ch := range changes {
		if err := c.settings.saveSetting(ch.key, ch.value); err != nil {
			return nil, err
		}
	}
	return res, nil
}
