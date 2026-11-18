package sub

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

// A disabled inbound is not listening, so the JSON and Clash subscriptions
// must not build an outbound for it (upstream s-ui #1327 / 2s-ui #306). Its
// stored local links are dropped by the link rebuild the switch triggers
// (service.inboundLinks).
func TestJsonSubsSkipDisabledInbounds(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "test.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	on := model.Inbound{Type: "mixed", Tag: "on", OutJson: json.RawMessage(`{"type":"mixed","tag":"on"}`)}
	off := model.Inbound{Type: "mixed", Tag: "off", Enable: false, EnableSet: true, OutJson: json.RawMessage(`{"type":"mixed","tag":"off"}`)}
	for _, in := range []*model.Inbound{&on, &off} {
		if err := db.Create(in).Error; err != nil {
			t.Fatal(err)
		}
	}
	ids, _ := json.Marshal([]uint{on.Id, off.Id})
	client := model.Client{Name: "alice", Enable: true, Inbounds: ids, Links: json.RawMessage(`[]`)}
	if err := db.Create(&client).Error; err != nil {
		t.Fatal(err)
	}
	_, ins, err := (&JsonService{}).getData("alice")
	if err != nil {
		t.Fatal(err)
	}
	if len(ins) != 1 || ins[0].Tag != "on" {
		t.Fatalf("json/clash subs got %d inbounds, want only the enabled one", len(ins))
	}
}
