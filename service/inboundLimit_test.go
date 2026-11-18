package service

// Per-inbound traffic caps. Ported from shenaba/2s-ui (GPL-3.0) #289, adapted
// for s-ui-nexus (the test core is not started, so "in the core" is judged
// from the generated config).

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

func setTraffic(t *testing.T, tag string, up, down int64) {
	t.Helper()
	err := database.GetDB().Model(model.Inbound{}).Where("tag = ?", tag).
		Updates(map[string]interface{}{"up": up, "down": down}).Error
	if err != nil {
		t.Fatalf("set traffic: %v", err)
	}
}

func capClient(t *testing.T) model.Client {
	t.Helper()
	client := model.Client{
		Enable: true, Name: "alice",
		Config:   json.RawMessage(`{"socks":{"username":"alice","password":"pw"},"http":{"username":"alice","password":"pw"}}`),
		Inbounds: json.RawMessage(`[]`),
		Links:    json.RawMessage(`[]`),
	}
	if err := database.GetDB().Create(&client).Error; err != nil {
		t.Fatal(err)
	}
	return client
}

func localLinkCount(t *testing.T, clientId uint, tag string) int {
	t.Helper()
	var c model.Client
	database.GetDB().First(&c, clientId)
	var out []map[string]string
	_ = json.Unmarshal(c.Links, &out)
	n := 0
	for _, l := range out {
		if l["type"] == "local" && l["remark"] == tag {
			n++
		}
	}
	return n
}

func TestAnInboundOverItsCapIsSwitchedOffUntilReset(t *testing.T) {
	s := enableTestService(t)
	client := capClient(t)
	cid := strconv.FormatUint(uint64(client.Id), 10)
	tx := database.GetDB().Begin()
	if err := s.Save(tx, "new", json.RawMessage(`{"type":"mixed","tag":"capped","listen":"127.0.0.1","listen_port":1081,"volume":1000}`), cid, "panel.example"); err != nil {
		tx.Rollback()
		t.Fatal(err)
	}
	tx.Commit()
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"free","listen":"127.0.0.1","listen_port":1082}`)
	links := localLinkCount(t, client.Id, "capped")
	if links == 0 {
		t.Fatal("no links for the new inbound")
	}

	// At the cap is not over it, as for a client.
	setTraffic(t, "capped", 600, 400)
	setTraffic(t, "free", 1<<40, 0)
	if tags, err := s.DepleteInbounds(); err != nil || len(tags) != 0 {
		t.Fatalf("at the cap: switched off %v (%v), want nothing", tags, err)
	}

	setTraffic(t, "capped", 600, 401)
	tags, err := s.DepleteInbounds()
	if err != nil {
		t.Fatalf("deplete: %v", err)
	}
	if len(tags) != 1 || tags[0] != "capped" {
		t.Fatalf("switched off %v, want [capped]", tags)
	}
	if inboundRow(t, "capped").Enable {
		t.Error("the spent inbound is still on")
	}
	if n := localLinkCount(t, client.Id, "capped"); n != 0 {
		t.Errorf("the spent inbound still has %d links", n)
	}
	if !inboundRow(t, "free").Enable {
		t.Error("an inbound with no cap was switched off")
	}
	if again, _ := s.DepleteInbounds(); len(again) != 0 {
		t.Errorf("a second pass switched off %v again", again)
	}

	id := idOf(t, "capped")
	err = saveInbound(t, s, "edit", `{"id":`+id+`,"type":"mixed","tag":"capped","listen":"127.0.0.1","listen_port":1081,"enable":true}`)
	if err == nil || !strings.Contains(err.Error(), "traffic limit") {
		t.Fatalf("switching a spent inbound on = %v, want it refused", err)
	}

	if err := saveInboundHost(s, "resetTraffic", `"capped"`); err != nil {
		t.Fatalf("reset: %v", err)
	}
	row := inboundRow(t, "capped")
	if !row.Enable || row.Up != 0 || row.Down != 0 {
		t.Errorf("after reset: enable=%v up=%d down=%d, want on and zeroed", row.Enable, row.Up, row.Down)
	}
	if row.Volume != 1000 {
		t.Errorf("the reset changed the cap to %d", row.Volume)
	}
	if n := localLinkCount(t, client.Id, "capped"); n != links {
		t.Errorf("after reset the client has %d links for it, want %d back", n, links)
	}
}

// An edit that does not carry volume keeps the stored cap, and no edit writes
// the counters: those are the flush's.
func TestAnInboundEditKeepsItsCap(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"in","listen":"127.0.0.1","listen_port":1083,"volume":5000}`)
	setTraffic(t, "in", 30, 40)
	id := idOf(t, "in")

	mustSaveInbound(t, s, "edit", `{"id":`+id+`,"type":"mixed","tag":"in","listen":"127.0.0.1","listen_port":1083,"up":0,"down":0}`)
	row := inboundRow(t, "in")
	if row.Volume != 5000 {
		t.Errorf("an edit without volume left it at %d, want 5000", row.Volume)
	}
	if row.Up != 30 || row.Down != 40 {
		t.Errorf("an edit wrote the counters: %d/%d, want 30/40", row.Up, row.Down)
	}
	raw, err := row.MarshalJSON()
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	for _, k := range []string{`"volume"`, `"up"`, `"down"`} {
		if strings.Contains(string(raw), k) {
			t.Errorf("the sing-box shape carries %s: %s", k, raw)
		}
	}
	mustSaveInbound(t, s, "edit", `{"id":`+id+`,"type":"mixed","tag":"in","listen":"127.0.0.1","listen_port":1083,"volume":0}`)
	if v := inboundRow(t, "in").Volume; v != 0 {
		t.Errorf("volume 0 was not stored, got %d", v)
	}
}

// Switched off by hand with traffic to spare, a reset zeroes it and leaves it off.
func TestAResetLeavesAHandSwitchedOffInboundOff(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"off","listen":"127.0.0.1","listen_port":1084,"volume":1000,"enable":false}`)
	setTraffic(t, "off", 5, 5)
	mustSaveInbound(t, s, "resetTraffic", `"off"`)
	if row := inboundRow(t, "off"); row.Enable || row.Up != 0 || row.Down != 0 {
		t.Errorf("reset: enable=%v up=%d down=%d, want off and zeroed", row.Enable, row.Up, row.Down)
	}
}

// The cap switches off a detour target anyway; a reset makes the checks a
// switch-on makes.
func TestTheCapSwitchesOffADetourTargetAnyway(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"back","listen":"127.0.0.1","listen_port":1085,"volume":100}`)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"front","listen":"127.0.0.1","listen_port":1086,"detour":"back","volume":100}`)
	if err := saveInbound(t, s, "edit", `{"id":`+idOf(t, "back")+`,"type":"mixed","tag":"back","listen":"127.0.0.1","listen_port":1085,"enable":false}`); err == nil {
		t.Fatal("switching off a listener's detour by hand must still be refused")
	}
	setTraffic(t, "back", 101, 0)
	setTraffic(t, "front", 101, 0)
	if tags, err := s.DepleteInbounds(); err != nil || len(tags) != 2 {
		t.Fatalf("deplete = %v (%v), want both switched off", tags, err)
	}
	err := saveInbound(t, s, "resetTraffic", `"front"`)
	if err == nil || !strings.Contains(err.Error(), "back") {
		t.Fatalf("resetting front while its detour is off = %v, want it refused naming back", err)
	}
	if row := inboundRow(t, "front"); row.Enable || row.Up == 0 {
		t.Error("a refused reset was written")
	}
	mustSaveInbound(t, s, "resetTraffic", `"back"`)
	mustSaveInbound(t, s, "resetTraffic", `"front"`)
	if !inboundRow(t, "back").Enable || !inboundRow(t, "front").Enable {
		t.Error("both should be on again")
	}
}

// A replica cannot be reset here: its traffic is counted on its node.
func TestResetTrafficRefusesReplica(t *testing.T) {
	s := enableTestService(t)
	if err := database.GetDB().Exec("INSERT INTO inbounds (type, tag, node_id, enable) VALUES ('mixed', 'rep', 3, 1)").Error; err != nil {
		t.Fatal(err)
	}
	if err := saveInbound(t, s, "resetTraffic", `"rep"`); err == nil {
		t.Fatal("reset of a replica was not refused")
	}
}

// The stats flush advances a local inbound's counters per direction, and
// leaves a replica sharing the tag alone.
func TestStatsFlushAdvancesInboundCounters(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"in","listen":"127.0.0.1","listen_port":1087}`)
	setTraffic(t, "in", 10, 20)
	db := database.GetDB()
	if err := db.Exec("INSERT INTO inbounds (type, tag, node_id) VALUES ('mixed', 'rep', 3)").Error; err != nil {
		t.Fatal(err)
	}
	stats := []model.Stats{
		{Resource: "inbound", Tag: "in", Direction: true, Traffic: 5},
		{Resource: "inbound", Tag: "in", Direction: true, Traffic: 1},
		{Resource: "inbound", Tag: "in", Direction: false, Traffic: 7},
		{Resource: "user", Tag: "in", Direction: true, Traffic: 1000},
		{Resource: "inbound", Tag: "rep", Direction: true, Traffic: 9},
		{Resource: "inbound", Tag: "gone", Direction: true, Traffic: 9},
	}
	if err := addInboundTraffic(db, stats); err != nil {
		t.Fatal(err)
	}
	if row := inboundRow(t, "in"); row.Up != 16 || row.Down != 27 {
		t.Errorf("up/down = %d/%d, want 16/27", row.Up, row.Down)
	}
	if row := inboundRow(t, "rep"); row.Up != 0 || row.Down != 0 {
		t.Errorf("replica counted: %d/%d", row.Up, row.Down)
	}
}
