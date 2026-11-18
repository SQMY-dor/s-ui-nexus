package service

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

// A cluster replica must be distinguishable from a local inbound in every
// inbound projection (apiv2 inbounds, apiv3 resources/inbounds, ?id=).
func TestInboundProjectionsCarryNodeId(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "nodeid.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	node := model.Node{Name: "jp3", BaseUrl: "https://jp3.example", WebPath: "/app/", Token: "t", Enable: true}
	if err := db.Create(&node).Error; err != nil {
		t.Fatal(err)
	}
	local := model.Inbound{Type: "mixed", Tag: "local", Options: json.RawMessage(`{"listen":"127.0.0.1","listen_port":21080}`)}
	replica := model.Inbound{Type: "mixed", Tag: "jp3-smoke", NodeId: &node.Id, Options: json.RawMessage(`{"listen":"127.0.0.1","listen_port":21081}`)}
	for _, row := range []*model.Inbound{&local, &replica} {
		if err := db.Create(row).Error; err != nil {
			t.Fatal(err)
		}
	}

	list, err := (&InboundService{}).Get("")
	if err != nil {
		t.Fatal(err)
	}
	seen := 0
	for _, ib := range *list {
		switch ib["tag"] {
		case "local":
			seen++
			if _, has := ib["node_id"]; has {
				t.Errorf("local inbound carries node_id: %v", ib)
			}
		case "jp3-smoke":
			seen++
			if fmt.Sprint(ib["node_id"]) != fmt.Sprint(node.Id) {
				t.Errorf("replica node_id = %v, want %d (%v)", ib["node_id"], node.Id, ib)
			}
			if _, has := ib["volume"]; has {
				t.Errorf("replica should not report local traffic counters: %v", ib)
			}
		}
	}
	if seen != 2 {
		t.Fatalf("list = %v", *list)
	}

	one, err := (&InboundService{}).Get(fmt.Sprint(replica.Id))
	if err != nil {
		t.Fatal(err)
	}
	if len(*one) != 1 || fmt.Sprint((*one)[0]["node_id"]) != fmt.Sprint(node.Id) {
		t.Fatalf("Get(id) = %v, want node_id %d", *one, node.Id)
	}
}
