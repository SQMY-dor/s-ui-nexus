// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package service

import (
	"strconv"
	"strings"
)

// Settings used by the automatic HTTPS feature. Registered from here rather
// than in setting.go's defaultValueMap literal to keep the port in its own
// files (fewer merge conflicts with other branches).
//
//   - webNginx / subNginx: "true" = TLS is terminated by the generated nginx
//     reverse proxy; the panel / subscription server itself serves plaintext
//     HTTP (Linux only; ignored elsewhere). Same keys and semantics as 2s-ui.
//   - acmeEmail: default account e-mail offered by the issue dialog.
func init() {
	defaultValueMap["webNginx"] = "false"
	defaultValueMap["subNginx"] = "false"
	defaultValueMap["acmeEmail"] = ""
}

// parseLooseBool treats "" (never set, or a pre-upgrade row) as false instead
// of failing like strconv.ParseBool.
func parseLooseBool(v string) bool {
	b, err := strconv.ParseBool(strings.TrimSpace(v))
	return err == nil && b
}

func (s *SettingService) GetWebNginx() (bool, error) {
	v, err := s.getString("webNginx")
	if err != nil {
		return false, err
	}
	return parseLooseBool(v), nil
}

func (s *SettingService) GetSubNginx() (bool, error) {
	v, err := s.getString("subNginx")
	if err != nil {
		return false, err
	}
	return parseLooseBool(v), nil
}

func (s *SettingService) GetAcmeEmail() (string, error) {
	return s.getString("acmeEmail")
}

// WebBehindProxy / SubBehindProxy: whether the server must run plaintext
// because nginx terminates TLS in front of it.
func (s *SettingService) WebBehindProxy() bool {
	v, err := s.GetWebNginx()
	return err == nil && v && acmeSupported()
}

func (s *SettingService) SubBehindProxy() bool {
	v, err := s.GetSubNginx()
	return err == nil && v && acmeSupported()
}

// ProxyVhostSpecs reads both sides' reverse-proxy inputs from the database,
// for the startup reconciliation (app) — the API path builds the same from the
// form. Both go through BuildVhostSpecs. Read errors are returned, never
// treated as "disabled": a side read as disabled has its vhost swept.
func (s *SettingService) ProxyVhostSpecs() ([]ProxySide, error) {
	all, err := s.GetAllSetting()
	if err != nil {
		return nil, err
	}
	m := *all
	port := func(key string) int {
		n, _ := strconv.Atoi(m[key])
		return n
	}
	return []ProxySide{
		{Name: "panel", Enabled: parseLooseBool(m["webNginx"]), Domain: m["webDomain"], Path: m["webPath"],
			Listen: m["webListen"], Port: port("webPort"), CertFile: m["webCertFile"], KeyFile: m["webKeyFile"]},
		{Name: "subscription", Enabled: parseLooseBool(m["subNginx"]), Domain: m["subDomain"], Path: m["subPath"],
			Listen: m["subListen"], Port: port("subPort"), CertFile: m["subCertFile"], KeyFile: m["subKeyFile"]},
	}, nil
}

func (s *SettingService) SetAcmeEmail(email string) error {
	if !validEmail(strings.TrimSpace(email)) {
		return nil
	}
	return s.setString("acmeEmail", strings.TrimSpace(email))
}
