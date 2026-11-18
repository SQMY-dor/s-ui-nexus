package database

import (
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database/model"
)

// Ported from shenaba/2s-ui (GPL-3.0) #289, adapted: the marker is the schema
// (no settings flag). The counters start from the stats sum per direction so
// the figure does not drop to zero on upgrade, a replica is left alone, and a
// second start does not re-seed over counters the flush has moved.
func TestMigrateInboundTraffic(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inboundtraffic.db")
	if err := InitDB(path); err != nil {
		t.Fatalf("init db: %v", err)
	}
	// Back to the pre-cap schema.
	for _, col := range []string{"volume", "up", "down"} {
		if err := db.Exec("ALTER TABLE inbounds DROP COLUMN " + col).Error; err != nil {
			t.Fatalf("drop %s: %v", col, err)
		}
	}
	for _, q := range []string{
		"INSERT INTO inbounds (type, tag) VALUES ('mixed', 'busy')",
		"INSERT INTO inbounds (type, tag) VALUES ('mixed', 'idle')",
		"INSERT INTO inbounds (type, tag, node_id) VALUES ('mixed', 'replica', 7)",
	} {
		if err := db.Exec(q).Error; err != nil {
			t.Fatalf("seed inbound: %v", err)
		}
	}
	for _, row := range []model.Stats{
		{Resource: "inbound", Tag: "busy", DateTime: 100, Direction: true, Traffic: 300},
		{Resource: "inbound", Tag: "busy", DateTime: 200, Direction: true, Traffic: 1},
		{Resource: "inbound", Tag: "busy", DateTime: 100, Direction: false, Traffic: 200},
		// Another resource under the same tag is not the inbound's.
		{Resource: "user", Tag: "busy", DateTime: 100, Direction: true, Traffic: 9000},
		{Resource: "inbound", Tag: "replica", DateTime: 100, Direction: true, Traffic: 50},
	} {
		if err := db.Create(&row).Error; err != nil {
			t.Fatalf("seed stats: %v", err)
		}
	}

	if err := InitDB(path); err != nil {
		t.Fatalf("upgrade: %v", err)
	}

	want := map[string][2]int64{"busy": {301, 200}, "idle": {0, 0}, "replica": {0, 0}}
	var got []model.Inbound
	if err := db.Find(&got).Error; err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("got %d inbounds, want 3", len(got))
	}
	for _, inb := range got {
		if w := want[inb.Tag]; inb.Up != w[0] || inb.Down != w[1] {
			t.Errorf("%s: up/down = %d/%d, want %d/%d", inb.Tag, inb.Up, inb.Down, w[0], w[1])
		}
		if inb.Volume != 0 {
			t.Errorf("%s: volume %d after upgrade, want 0 (unlimited)", inb.Tag, inb.Volume)
		}
		if !inb.Enable {
			t.Errorf("%s: an inbound written without the column must read as enabled", inb.Tag)
		}
	}

	// Once: a second start must not re-seed over counters the flush has moved.
	if err := db.Model(model.Inbound{}).Where("tag = ?", "busy").Update("up", 5).Error; err != nil {
		t.Fatal(err)
	}
	if err := InitDB(path); err != nil {
		t.Fatalf("second start: %v", err)
	}
	var busy model.Inbound
	db.Where("tag = ?", "busy").First(&busy)
	if busy.Up != 5 {
		t.Errorf("second start rewrote up to %d", busy.Up)
	}
	// And no internal marker lands in the settings table.
	var n int64
	db.Model(model.Setting{}).Where("key LIKE ?", "migrated%").Count(&n)
	if n != 0 {
		t.Errorf("%d migration marker rows in settings", n)
	}
}
