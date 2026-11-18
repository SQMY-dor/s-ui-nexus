package util

import (
	"encoding/json"
	"testing"
)

func TestGetOutboundReadsSocksAndHttpLinks(t *testing.T) {
	cases := []struct {
		link string
		want string
	}{
		{"socks5://agg:aggpass123@jp3.example.com:21085#%5Bjp3%5D%20jp3-edge",
			`{"password":"aggpass123","server":"jp3.example.com","server_port":21085,"tag":"[jp3] jp3-edge","type":"socks","username":"agg","version":"5"}`},
		{"http://agg:aggpass123@jp3.example.com:21085#edge",
			`{"password":"aggpass123","server":"jp3.example.com","server_port":21085,"tag":"edge","type":"http","username":"agg"}`},
		{"https://u:p@proxy.example.com:8443#tls",
			`{"password":"p","server":"proxy.example.com","server_port":8443,"tag":"tls","tls":{"enabled":true,"server_name":"proxy.example.com"},"type":"http","username":"u"}`},
		{"socks5://:@10.0.0.1:1080#open",
			`{"server":"10.0.0.1","server_port":1080,"tag":"open","type":"socks","version":"5"}`},
	}
	for _, tc := range cases {
		out, tag, err := GetOutbound(tc.link, 0)
		if err != nil {
			t.Fatalf("%s: %v", tc.link, err)
		}
		got, _ := json.Marshal(out)
		if string(got) != tc.want {
			t.Errorf("%s:\n got %s\nwant %s", tc.link, got, tc.want)
		}
		if tag == "" {
			t.Errorf("%s: empty tag", tc.link)
		}
	}
	if _, _, err := GetOutboundForClash("socks5://a:b@h.example:1080#x", 2); err != nil {
		t.Fatalf("clash path rejects socks5: %v", err)
	}
}
