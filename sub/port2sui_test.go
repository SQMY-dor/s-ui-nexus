package sub

// Tests for the upstream s-ui 1.6.7+ fixes ported via shenaba/2s-ui (GPL-3.0):
// ws/httpupgrade Host defaulting to the SNI (#1334), VLESS encryption in
// Clash (#1337) and ECH in Clash (#1339).

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/util"

	"github.com/sagernet/sing-box/common/tls"
	"gopkg.in/yaml.v3"
)

func firstClashProxy(t *testing.T, raw string) map[string]interface{} {
	t.Helper()
	var parsed struct {
		Proxies []map[string]interface{} `yaml:"proxies"`
	}
	if err := yaml.Unmarshal([]byte(raw), &parsed); err != nil {
		t.Fatalf("parse clash: %v", err)
	}
	if len(parsed.Proxies) == 0 {
		t.Fatalf("no proxies in:\n%s", raw)
	}
	return parsed.Proxies[0]
}

// With no config the client looks ECH up in DNS only under an explicit
// query_server_name; a config that does not convert leaves ech-opts out whole.
func TestClashEchOpts(t *testing.T) {
	configPem, keyPem, err := tls.ECHKeygenDefault("public.example.com")
	if err != nil {
		t.Fatalf("ECHKeygenDefault: %v", err)
	}
	lines := func(s string) []interface{} {
		var out []interface{}
		for _, l := range strings.Split(s, "\n") {
			out = append(out, l)
		}
		return out
	}
	cases := []struct {
		name string
		ech  map[string]interface{}
		want map[string]interface{} // nil = no ech-opts
	}{
		{"config", map[string]interface{}{"enabled": true, "config": lines(configPem), "query_server_name": "q.example.com"},
			map[string]interface{}{"enable": true, "config": util.ECHConfigBase64(lines(configPem))}},
		{"query name only", map[string]interface{}{"enabled": true, "query_server_name": "q.example.com"},
			map[string]interface{}{"enable": true, "query-server-name": "q.example.com"}},
		{"neither", map[string]interface{}{"enabled": true}, nil},
		{"config_path only", map[string]interface{}{"enabled": true, "config_path": "/etc/ech.pem"}, nil},
		{"off", map[string]interface{}{"enabled": false, "config": lines(configPem)}, nil},
	}
	_ = keyPem // the key-pasted case is covered in util (it logs, which needs the logger)
	for _, tc := range cases {
		outbounds := []map[string]interface{}{{
			"type": "trojan", "tag": "tr", "server": "e.com", "server_port": float64(443), "password": "pw",
			"tls": map[string]interface{}{"enabled": true, "server_name": "e.com", "ech": tc.ech},
		}}
		raw, err := (&ClashService{}).ConvertToClashMeta(&outbounds, basicClashConfig)
		if err != nil {
			t.Fatalf("%s: ConvertToClashMeta: %v", tc.name, err)
		}
		got, _ := firstClashProxy(t, raw)["ech-opts"].(map[string]interface{})
		if !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: ech-opts = %#v, want %#v", tc.name, got, tc.want)
		}
	}
	if c := util.ECHConfigBase64(lines(configPem)); c == "" || strings.Contains(c, "-") {
		t.Errorf("config not converted to bare base64: %q", c)
	}
}

func TestClashVlessEncryption(t *testing.T) {
	const enc = "mlkem768x25519plus.native.0rtt.AAAA"
	outbounds := []map[string]interface{}{{
		"type": "vless", "tag": "v", "server": "e.com", "server_port": float64(443),
		"uuid": "00000000-0000-4000-8000-000000000001", "encryption": enc,
	}}
	raw, err := (&ClashService{}).ConvertToClashMeta(&outbounds, basicClashConfig)
	if err != nil {
		t.Fatalf("ConvertToClashMeta: %v", err)
	}
	if got := firstClashProxy(t, raw)["encryption"]; got != enc {
		t.Errorf("encryption = %v, want %q", got, enc)
	}
}

// Clash reads the Host through the same helper as links and the JSON
// subscription: the transport's own, else the SNI under TLS, never an empty one.
func TestClashWsHostFollowsSharedRule(t *testing.T) {
	tlsOn := map[string]interface{}{"enabled": true, "server_name": "cdn.example.com"}
	for _, tc := range []struct {
		name      string
		transport map[string]interface{}
		tls       map[string]interface{}
		want      interface{}
	}{
		{"own header", map[string]interface{}{"type": "ws", "headers": map[string]interface{}{"Host": "own.example.com"}}, tlsOn, "own.example.com"},
		{"header list", map[string]interface{}{"type": "ws", "headers": map[string]interface{}{"Host": []interface{}{"list.example.com"}}}, tlsOn, "list.example.com"},
		{"empty header", map[string]interface{}{"type": "ws", "headers": map[string]interface{}{"Host": ""}}, tlsOn, "cdn.example.com"},
		{"httpupgrade host", map[string]interface{}{"type": "httpupgrade", "host": "hu.example.com"}, tlsOn, "hu.example.com"},
		{"sni fallback", map[string]interface{}{"type": "ws"}, tlsOn, "cdn.example.com"},
		{"no tls", map[string]interface{}{"type": "ws"}, nil, nil},
	} {
		ob := map[string]interface{}{
			"type": "vless", "tag": "v", "server": "1.2.3.4", "server_port": float64(443),
			"uuid": "00000000-0000-4000-8000-000000000001", "transport": tc.transport,
		}
		if tc.tls != nil {
			ob["tls"] = tc.tls
		}
		outbounds := []map[string]interface{}{ob}
		raw, err := (&ClashService{}).ConvertToClashMeta(&outbounds, basicClashConfig)
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		wsOpts, _ := firstClashProxy(t, raw)["ws-opts"].(map[string]interface{})
		headers, _ := wsOpts["headers"].(map[string]interface{})
		if got := headers["Host"]; got != tc.want {
			t.Errorf("%s: Host = %#v, want %#v", tc.name, got, tc.want)
		}
	}
}

// The JSON subscription gives a ws outbound with no Host the SNI of the
// address it is built for (upstream s-ui #1334), and none without TLS.
func TestJsonOutboundsFallBackToSniForWsHost(t *testing.T) {
	inbound := &model.Inbound{
		Type:    "vless",
		Tag:     "vless",
		OutJson: json.RawMessage(`{"type": "vless", "tag": "vless", "tls": {"enabled": true, "server_name": "origin.example.com"}, "transport": {"type": "ws", "path": "/ws"}}`),
		Addrs:   json.RawMessage(`[{"server": "a.example.com", "server_port": 443, "tls": {"server_name": "one.example.com"}}, {"server": "b.example.com", "server_port": 443, "tls": {"server_name": "two.example.com"}}]`),
	}
	noAddrs := &model.Inbound{
		Type:    "vless",
		Tag:     "plain",
		OutJson: json.RawMessage(`{"type": "vless", "tag": "plain", "server": "c.example.com", "server_port": 80, "transport": {"type": "ws", "path": "/ws"}}`),
		Addrs:   json.RawMessage(`[]`),
	}
	clientConfig := json.RawMessage(`{"vless": {"name": "user", "uuid": "00000000-0000-0000-0000-000000000000"}}`)

	outbounds, _, err := (&JsonService{}).getOutbounds(clientConfig, []*model.Inbound{inbound, noAddrs})
	if err != nil {
		t.Fatal(err)
	}
	if len(*outbounds) != 3 {
		t.Fatalf("got %d outbounds, want 3", len(*outbounds))
	}
	for i, want := range []string{"one.example.com", "two.example.com"} {
		transport, _ := (*outbounds)[i]["transport"].(map[string]interface{})
		headers, _ := transport["headers"].(map[string]interface{})
		if headers["Host"] != want {
			t.Errorf("outbound %d Host = %v, want %s", i, headers["Host"], want)
		}
	}
	transport, _ := (*outbounds)[2]["transport"].(map[string]interface{})
	if _, has := transport["headers"]; has {
		t.Errorf("no-TLS outbound got a Host: %v", transport)
	}
	// The shared transport of the inbound was not written into.
	if strings.Contains(string(inbound.OutJson), "Host") {
		t.Errorf("stored out_json changed: %s", inbound.OutJson)
	}
}
