package service

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"

	"github.com/op/go-logging"
)

// A mixed replica's links carry the client's credentials even when the
// client config has only the "mixed" entry the node authenticates with
// (issue #30: they came out as socks5://:@host:port).
func TestGenNodeReplicaLinksCarryMixedCredentials(t *testing.T) {
	logger.InitLogger(logging.CRITICAL)
	nodeId := uint(1)
	replica := &model.Inbound{
		Type: "mixed", Tag: "jp3-edge", NodeId: &nodeId, Enable: true,
		Options: json.RawMessage(`{"listen":"127.0.0.1","listen_port":21085}`),
		OutJson: json.RawMessage(`{"server":"jp3.example.com","server_port":21085}`),
		Addrs:   json.RawMessage(`[]`),
	}
	client := &model.Client{Name: "agg", Config: json.RawMessage(`{"mixed":{"username":"agg","password":"aggpass123"}}`)}
	links := genNodeReplicaLinks(replica, client)
	if len(links) != 2 {
		t.Fatalf("links = %v, want socks5 + http", links)
	}
	for _, link := range links {
		if !strings.Contains(link, "agg:aggpass123@jp3.example.com:21085") {
			t.Errorf("link without credentials: %s", link)
		}
	}
}
