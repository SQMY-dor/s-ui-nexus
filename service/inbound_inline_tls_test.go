package service

import (
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/util"
)

func loadInboundWithTls(t *testing.T, tag string) model.Inbound {
	t.Helper()
	var inbound model.Inbound
	if err := database.GetDB().Preload("Tls").Where("tag = ?", tag).First(&inbound).Error; err != nil {
		t.Fatal(err)
	}
	return inbound
}

// An inbound saved with an inline tls object and no tls_id gets a Tls record,
// so links and subscriptions carry the same TLS the core runs (issue #29).
func TestInboundInlineTlsBecomesTlsRecord(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"anytls","tag":"anytls","listen":"0.0.0.0","listen_port":24443,
		"tls":{"enabled":true,"server_name":"lax.example.com","certificate_path":"/c.pem","key_path":"/k.pem"},"users":[]}`)
	mustSaveInbound(t, s, "new", `{"type":"hysteria2","tag":"hy2","listen":"0.0.0.0","listen_port":24444,
		"tls":{"enabled":true,"server_name":"lax.example.com","certificate_path":"/c.pem","key_path":"/k.pem"},"users":[]}`)

	priv, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	privB64 := base64.RawURLEncoding.EncodeToString(priv.Bytes())
	wantPub := base64.RawURLEncoding.EncodeToString(priv.PublicKey().Bytes())
	mustSaveInbound(t, s, "new", `{"type":"vless","tag":"reality","listen":"0.0.0.0","listen_port":24445,
		"tls":{"enabled":true,"server_name":"www.example.com","reality":{"enabled":true,
		"handshake":{"server":"www.example.com","server_port":443},"private_key":"`+privB64+`","short_id":["abcd1234"]}},"users":[]}`)

	for _, tag := range []string{"anytls", "hy2", "reality"} {
		inbound := loadInboundWithTls(t, tag)
		if inbound.TlsId == 0 || inbound.Tls == nil {
			t.Fatalf("%s: tls_id not filled from inline tls", tag)
		}
		if strings.Contains(string(inbound.Options), `"tls"`) {
			t.Errorf("%s: inline tls leaked into options: %s", tag, inbound.Options)
		}
		core, err := inbound.MarshalJSON()
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(core), `"tls"`) {
			t.Errorf("%s: core config lost tls: %s", tag, core)
		}
		if !strings.Contains(string(inbound.OutJson), `"tls"`) {
			t.Errorf("%s: out_json has no tls: %s", tag, inbound.OutJson)
		}
	}

	reality := loadInboundWithTls(t, "reality")
	if !strings.Contains(string(reality.Tls.Server), privB64) {
		t.Errorf("reality private key not kept on the server half: %s", reality.Tls.Server)
	}
	if !strings.Contains(string(reality.Tls.Client), wantPub) {
		t.Errorf("reality public key not derived: %s", reality.Tls.Client)
	}
	links := util.LinkGenerator(json.RawMessage(`{"vless":{"name":"a","uuid":"8b5e6d3c-3f3e-4c1a-9b1e-2b8a4f6f0a11","flow":"xtls-rprx-vision"}}`), &reality, "lax.example.com")
	if len(links) == 0 || !strings.Contains(links[0], "pbk="+wantPub) || !strings.Contains(links[0], "sid=abcd1234") {
		t.Errorf("reality link lacks pbk/sid: %v", links)
	}
	anyIn := loadInboundWithTls(t, "anytls")
	links = util.LinkGenerator(json.RawMessage(`{"anytls":{"name":"a","password":"p"}}`), &anyIn, "lax.example.com")
	if len(links) == 0 || !strings.Contains(links[0], "security=tls") {
		t.Errorf("anytls link lacks tls: %v", links)
	}

	// Editing with inline tls again updates the same record.
	firstTls := anyIn.TlsId
	mustSaveInbound(t, s, "edit", `{"id":`+jsonUint(anyIn.Id)+`,"type":"anytls","tag":"anytls","listen":"0.0.0.0","listen_port":24443,
		"tls":{"enabled":true,"server_name":"new.example.com","certificate_path":"/c.pem","key_path":"/k.pem"},"users":[]}`)
	edited := loadInboundWithTls(t, "anytls")
	if edited.TlsId != firstTls || !strings.Contains(string(edited.Tls.Server), "new.example.com") {
		t.Errorf("edit should reuse tls %d, got %d %s", firstTls, edited.TlsId, edited.Tls.Server)
	}
}

func jsonUint(v uint) string {
	b, _ := json.Marshal(v)
	return string(b)
}

// Link generation falls back to an inline tls the inbound still carries.
func TestLinkGeneratorFallsBackToInlineTls(t *testing.T) {
	inbound := model.Inbound{Type: "anytls", Tag: "x", Enable: true,
		Options:   json.RawMessage(`{"listen_port":443}`),
		Addrs:     json.RawMessage(`[]`),
		InlineTls: json.RawMessage(`{"enabled":true,"server_name":"a.example.com"}`)}
	links := util.LinkGenerator(json.RawMessage(`{"anytls":{"name":"a","password":"p"}}`), &inbound, "a.example.com")
	if len(links) == 0 || !strings.Contains(links[0], "security=tls") {
		t.Fatalf("links = %v", links)
	}
}
