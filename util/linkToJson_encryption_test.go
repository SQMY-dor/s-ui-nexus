package util

import (
	"net/url"
	"reflect"
	"testing"
)

// Xray's VLESS Encryption string survives for the Clash export of an external
// link, and never reaches the sing-box JSON path, which would reject it.
// Upstream s-ui #1337, adapted (s-ui-nexus has no XHTTP transport).
func TestGetOutboundForClashVLESSEncryption(t *testing.T) {
	const encrypted = "mlkem768x25519plus.native.1rtt.A+B/C=="
	for _, test := range []struct {
		name       string
		present    bool
		encryption string
		want       string
	}{
		{name: "absent"},
		{name: "empty", present: true},
		{name: "none", present: true, encryption: "none"},
		{name: "encrypted", present: true, encryption: encrypted, want: encrypted},
	} {
		t.Run(test.name, func(t *testing.T) {
			query := url.Values{"type": {"ws"}, "security": {"tls"}, "sni": {"test.example"}, "path": {"/upload"}}
			if test.present {
				query.Set("encryption", test.encryption)
			}
			uri := "vless://00000000-0000-4000-8000-000000000001@192.0.2.1:443?" + query.Encode() + "#VLESS"
			outbound, tag, err := GetOutboundForClash(uri, 0)
			if err != nil || outbound == nil {
				t.Fatalf("GetOutboundForClash: %v", err)
			}
			got, present := (*outbound)["encryption"]
			if present != (test.want != "") || present && got != test.want {
				t.Fatalf("encryption = %v (present %v), want %q", got, present, test.want)
			}
			ordinary, ordinaryTag, err := GetOutbound(uri, 0)
			if err != nil || ordinary == nil || ordinaryTag != tag {
				t.Fatalf("GetOutbound: %v", err)
			}
			if _, leaked := (*ordinary)["encryption"]; leaked {
				t.Fatalf("encryption leaked into the sing-box outbound: %v", *ordinary)
			}
			query.Del("encryption")
			want, _, _ := GetOutbound("vless://00000000-0000-4000-8000-000000000001@192.0.2.1:443?"+query.Encode()+"#VLESS", 0)
			if !reflect.DeepEqual(ordinary, want) {
				t.Fatalf("ordinary outbound changed: %v vs %v", *ordinary, *want)
			}
		})
	}
}
