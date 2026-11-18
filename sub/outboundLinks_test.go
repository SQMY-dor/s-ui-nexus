package sub

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/util"
)

// Node replica links (external, no fragment) reach the JSON and Clash formats
// as named outbounds, like they reach the link format (issue #30).
func TestGetOutboundLinksNamesNodeLinks(t *testing.T) {
	links := json.RawMessage(`[
		{"remark":"anytls","type":"local","uri":"anytls://p@lax.example.com:24443?security=tls#anytls"},
		{"remark":"[jp3] jp3-edge","type":"external","uri":"socks5://agg:aggpass123@jp3.example.com:21085"},
		{"remark":"[jp3] jp3-edge","type":"external","uri":"http://agg:aggpass123@jp3.example.com:21085"}
	]`)
	got := (&LinkService{}).GetOutboundLinks(&links)
	if len(got) != 2 {
		t.Fatalf("got %v, want the two external links", got)
	}
	tags := map[string]bool{}
	for index, link := range got {
		out, tag, err := util.GetOutbound(link, index+1)
		if err != nil {
			t.Fatalf("%s: %v", link, err)
		}
		if !strings.Contains(tag, "[jp3] jp3-edge") {
			t.Errorf("tag = %q", tag)
		}
		if (*out)["username"] != "agg" {
			t.Errorf("credentials lost: %v", *out)
		}
		tags[tag] = true
	}
	if len(tags) != 2 {
		t.Errorf("tags not unique: %v", tags)
	}

	outbounds := []map[string]interface{}{}
	for index, link := range got {
		out, _, _ := util.GetOutboundForClash(link, index+1)
		outbounds = append(outbounds, *out)
	}
	clash, err := (&ClashService{}).ConvertToClashMeta(&outbounds, basicClashConfig)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(clash, "socks5") || !strings.Contains(clash, "aggpass123") {
		t.Errorf("clash output lacks the node proxies:\n%s", clash)
	}
}
