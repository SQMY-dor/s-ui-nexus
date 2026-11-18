package sub

// uTLS stays off hysteria/hysteria2/tuic client configs (ported from
// shenaba/2s-ui (GPL-3.0) #310): sing-box refuses uTLS on a QUIC outbound.

import (
	"encoding/json"
	"testing"

	"github.com/felixbennettio/s-ui-next/database/model"

	"gopkg.in/yaml.v3"
)

// out_json written before the fix still holds utls; the JSON subscription
// drops it on the QUIC outbound (with or without address overrides) and keeps
// it on vless, and the Clash export built from the same outbounds carries no
// client-fingerprint for the QUIC one.
func TestSubDropsStoredUtlsOnQuic(t *testing.T) {
	utls := `"tls":{"enabled":true,"server_name":"example.com","utls":{"enabled":true,"fingerprint":"chrome"}}`
	tuic := &model.Inbound{
		Type: "tuic", Tag: "tuic-in",
		Addrs:   json.RawMessage(`[]`),
		OutJson: json.RawMessage(`{"type":"tuic","tag":"tuic-in","server":"example.com","server_port":443,"congestion_control":"bbr",` + utls + `}`),
	}
	hy2 := &model.Inbound{
		Type: "hysteria2", Tag: "hy2-in",
		// An address override carrying its own uTLS.
		Addrs:   json.RawMessage(`[{"server":"a.example.com","server_port":443,"tls":{"utls":{"enabled":true,"fingerprint":"firefox"}}}]`),
		OutJson: json.RawMessage(`{"type":"hysteria2","tag":"hy2-in","server":"example.com","server_port":443,` + utls + `}`),
	}
	vless := &model.Inbound{
		Type: "vless", Tag: "vless-in",
		Addrs:   json.RawMessage(`[]`),
		OutJson: json.RawMessage(`{"type":"vless","tag":"vless-in","server":"example.com","server_port":443,` + utls + `}`),
	}
	cfg := json.RawMessage(`{"tuic":{"uuid":"5bdbac27-7a82-42c5-9ef4-751f64bd15e4","password":"pw"},"hysteria2":{"password":"pw"},"vless":{"uuid":"5bdbac27-7a82-42c5-9ef4-751f64bd15e4"}}`)
	outbounds, _, err := (&JsonService{}).getOutbounds(cfg, []*model.Inbound{tuic, hy2, vless})
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, ob := range *outbounds {
		protocol, _ := ob["type"].(string)
		seen[protocol] = true
		tls, _ := ob["tls"].(map[string]interface{})
		_, has := tls["utls"]
		if want := protocol == "vless"; has != want {
			t.Errorf("%s: utls present = %v, want %v: %v", protocol, has, want, tls)
		}
	}
	if !seen["tuic"] || !seen["hysteria2"] || !seen["vless"] {
		t.Fatalf("outbounds missing: %v", seen)
	}

	raw, err := (&ClashService{}).ConvertToClashMeta(outbounds, basicClashConfig)
	if err != nil {
		t.Fatalf("ConvertToClashMeta: %v", err)
	}
	var parsed struct {
		Proxies []map[string]interface{} `yaml:"proxies"`
	}
	if err := yaml.Unmarshal([]byte(raw), &parsed); err != nil {
		t.Fatal(err)
	}
	for _, p := range parsed.Proxies {
		fp, has := p["client-fingerprint"]
		if p["type"] == "vless" {
			if fp != "chrome" {
				t.Errorf("vless client-fingerprint = %v, want chrome", fp)
			}
		} else if has {
			t.Errorf("%v proxy carries client-fingerprint %v", p["type"], fp)
		}
	}
}

func TestLinkListDropsStoredQuicFingerprint(t *testing.T) {
	links := json.RawMessage(`[
		{"type":"local","remark":"t","uri":"tuic://u:pw@example.com:443?sni=example.com&fp=chrome&congestion_control=bbr#t"},
		{"type":"local","remark":"v","uri":"vless://u@example.com:443?security=tls&fp=chrome#v"}]`)
	got := (&LinkService{}).GetLinks(&links, "all", "")
	if len(got) != 2 {
		t.Fatalf("got %d links: %v", len(got), got)
	}
	if got[0] != "tuic://u:pw@example.com:443?sni=example.com&congestion_control=bbr#t" {
		t.Errorf("tuic link = %s", got[0])
	}
	if got[1] != "vless://u@example.com:443?security=tls&fp=chrome#v" {
		t.Errorf("vless link = %s", got[1])
	}
}
