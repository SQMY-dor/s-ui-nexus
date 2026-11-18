package util

import (
	"crypto/ecdh"
	"encoding/base64"
	"encoding/json"
	"strings"

	"github.com/felixbennettio/s-ui-next/database/model"
)

// InlineTlsRecord turns the sing-box server-side `tls` object an API caller
// put directly on an inbound (instead of a tls_id) into the panel's Tls
// record shape: Server is what the core runs, Client is the half that link,
// JSON and Clash generation layer on top of it (see prepareTls / addTls).
//
// Reality keys stay intact: the server keeps private_key and short_id; the
// client gets public_key -- taken from the payload when the caller sent one
// (a non-sing-box field, so it is moved off the server half) or derived from
// private_key -- plus a uTLS fingerprint, which a Reality client requires.
//
// It returns nil when raw is empty, null, not an object, or explicitly
// disabled.
func InlineTlsRecord(name string, raw json.RawMessage) *model.Tls {
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" || trimmed == "null" {
		return nil
	}
	var server map[string]interface{}
	if err := json.Unmarshal(raw, &server); err != nil || len(server) == 0 {
		return nil
	}
	if enabled, ok := server["enabled"].(bool); ok && !enabled {
		return nil
	}
	// A panel Tls record ({id,name,server,client}) is not a sing-box tls
	// object; tls_id is the way to reference one.
	if _, isRecord := server["server"].(map[string]interface{}); isRecord {
		return nil
	}
	server["enabled"] = true

	client := map[string]interface{}{}
	if reality, ok := server["reality"].(map[string]interface{}); ok && AsBool(reality["enabled"]) {
		clientReality := map[string]interface{}{}
		publicKey, _ := reality["public_key"].(string)
		delete(reality, "public_key")
		if publicKey == "" {
			if privateKey, _ := reality["private_key"].(string); privateKey != "" {
				publicKey = realityPublicKey(privateKey)
			}
		}
		if publicKey != "" {
			clientReality["public_key"] = publicKey
		}
		// The fingerprint, when the caller put one on the server half, is a
		// client setting sing-box's server tls does not accept.
		fingerprint := "chrome"
		if utls, ok := server["utls"].(map[string]interface{}); ok {
			if fp, _ := utls["fingerprint"].(string); fp != "" {
				fingerprint = fp
			}
			delete(server, "utls")
		}
		client["reality"] = clientReality
		client["utls"] = map[string]interface{}{"enabled": true, "fingerprint": fingerprint}
	} else if utls, ok := server["utls"].(map[string]interface{}); ok {
		client["utls"] = utls
		delete(server, "utls")
	}
	if insecure, ok := server["insecure"]; ok {
		client["insecure"] = insecure
		delete(server, "insecure")
	}

	serverJson, err := json.MarshalIndent(server, "", "  ")
	if err != nil {
		return nil
	}
	clientJson, err := json.MarshalIndent(client, "", "  ")
	if err != nil {
		return nil
	}
	return &model.Tls{Name: name, Server: serverJson, Client: clientJson}
}

// realityPublicKey derives the X25519 public key sing-box clients expect
// (raw URL-safe base64) from a Reality private key in the same encoding.
func realityPublicKey(privateKey string) string {
	privateKey = strings.TrimSpace(privateKey)
	var keyBytes []byte
	for _, encoding := range []*base64.Encoding{base64.RawURLEncoding, base64.URLEncoding, base64.RawStdEncoding, base64.StdEncoding} {
		if decoded, err := encoding.DecodeString(privateKey); err == nil && len(decoded) == 32 {
			keyBytes = decoded
			break
		}
	}
	if keyBytes == nil {
		return ""
	}
	key, err := ecdh.X25519().NewPrivateKey(keyBytes)
	if err != nil {
		return ""
	}
	return base64.RawURLEncoding.EncodeToString(key.PublicKey().Bytes())
}

// EffectiveTls is the Tls record links and outbound JSON should use for an
// inbound: the stored relation when there is one, else a record derived from
// an inline `tls` object the inbound still carries (one that predates the
// save-time conversion, or a caller that bypassed it). Nil means plain.
func EffectiveTls(i *model.Inbound) *model.Tls {
	if i == nil {
		return nil
	}
	if i.TlsId > 0 && i.Tls != nil {
		return i.Tls
	}
	if len(i.InlineTls) > 0 {
		return InlineTlsRecord(i.Tag, i.InlineTls)
	}
	return nil
}
