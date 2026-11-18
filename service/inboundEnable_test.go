package service

// Inbound enable switch (upstream s-ui #1327, shenaba/2s-ui #306), adapted for
// s-ui-nexus.

import (
	"encoding/json"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/core"
	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"

	"github.com/op/go-logging"
)

func enableTestService(t *testing.T) *InboundService {
	t.Helper()
	if logger.GetLogger() == nil {
		logger.InitLogger(logging.CRITICAL)
	}
	if err := database.InitDB(filepath.Join(t.TempDir(), "test.db")); err != nil {
		t.Fatal(err)
	}
	prev := corePtr
	corePtr = core.NewCore()
	t.Cleanup(func() { corePtr = prev })
	return &InboundService{}
}

func saveInbound(t *testing.T, s *InboundService, act string, data string) error {
	t.Helper()
	tx := database.GetDB().Begin()
	if err := s.Save(tx, act, json.RawMessage(data), "", "localhost"); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit().Error
}

func mustSaveInbound(t *testing.T, s *InboundService, act string, data string) {
	t.Helper()
	if err := saveInbound(t, s, act, data); err != nil {
		t.Fatalf("save %s %s: %v", act, data, err)
	}
}

func inboundRow(t *testing.T, tag string) model.Inbound {
	t.Helper()
	var inbound model.Inbound
	if err := database.GetDB().Where("tag = ?", tag).First(&inbound).Error; err != nil {
		t.Fatal(err)
	}
	return inbound
}

func idOf(t *testing.T, tag string) string {
	return strconv.FormatUint(uint64(inboundRow(t, tag).Id), 10)
}

// GORM leaves a false bool out of an INSERT when the column has a default, so
// a new inbound saved as disabled came back enabled.
func TestSaveNewInboundKeepsEnableFlag(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"on","listen":"::","listen_port":1080}`)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"off","enable":false,"listen":"::","listen_port":1081}`)
	if !inboundRow(t, "on").Enable {
		t.Fatal("an inbound saved without enable must default to enabled")
	}
	if inboundRow(t, "off").Enable {
		t.Fatal("an inbound saved with enable=false was stored enabled")
	}
}

func TestDisabledInboundIsLeftOutOfConfig(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"on","listen":"::","listen_port":1080}`)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"off","listen":"::","listen_port":1081}`)
	mustSaveInbound(t, s, "edit", `{"id":`+idOf(t, "off")+`,"type":"mixed","tag":"off","enable":false,"listen":"::","listen_port":1081}`)

	configs, err := s.GetAllConfig(database.GetDB())
	if err != nil {
		t.Fatal(err)
	}
	var parts []string
	for _, c := range configs {
		parts = append(parts, string(c))
	}
	joined := strings.Join(parts, ",")
	if !strings.Contains(joined, `"tag":"on"`) {
		t.Fatalf("enabled inbound missing from config: %s", joined)
	}
	if strings.Contains(joined, `"tag":"off"`) {
		t.Fatalf("disabled inbound present in config: %s", joined)
	}
	if strings.Contains(joined, `"enable"`) {
		t.Fatalf("enable is a panel field and must not reach sing-box: %s", joined)
	}
	all, err := s.GetAll()
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range *all {
		if row["tag"] == "off" && row["enable"] != false {
			t.Errorf("inbounds list reports off as enable=%v", row["enable"])
		}
	}
}

// An edit that does not name enable (an older API caller) keeps the stored
// value instead of switching the inbound back on.
func TestEditWithoutEnableKeepsItOff(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"off","enable":false,"listen":"::","listen_port":1081}`)
	mustSaveInbound(t, s, "edit", `{"id":`+idOf(t, "off")+`,"type":"mixed","tag":"off","listen":"::","listen_port":1082}`)
	if inboundRow(t, "off").Enable {
		t.Fatal("an edit without enable switched the inbound back on")
	}
	mustSaveInbound(t, s, "edit", `{"id":`+idOf(t, "off")+`,"type":"mixed","tag":"off","enable":true,"listen":"::","listen_port":1082}`)
	if !inboundRow(t, "off").Enable {
		t.Fatal("enable=true did not switch it on")
	}
}

// Switching an inbound off takes its stored links out of every subscription,
// and switching it back on restores them.
func TestDisabledInboundHasNoLinks(t *testing.T) {
	s := enableTestService(t)
	db := database.GetDB()
	client := model.Client{
		Enable: true, Name: "alice",
		Config:   json.RawMessage(`{"socks":{"username":"alice","password":"pw"},"http":{"username":"alice","password":"pw"}}`),
		Inbounds: json.RawMessage(`[]`),
		Links:    json.RawMessage(`[{"type":"external","remark":"ext","uri":"socks5://ext"}]`),
	}
	if err := db.Create(&client).Error; err != nil {
		t.Fatal(err)
	}
	tx := db.Begin()
	if err := s.Save(tx, "new", json.RawMessage(`{"type":"mixed","tag":"mx","listen":"::","listen_port":1080}`), strconv.Itoa(int(client.Id)), "panel.example"); err != nil {
		tx.Rollback()
		t.Fatal(err)
	}
	tx.Commit()
	links := func() []map[string]string {
		var c model.Client
		db.First(&c, client.Id)
		var out []map[string]string
		_ = json.Unmarshal(c.Links, &out)
		return out
	}
	countLocal := func() int {
		n := 0
		for _, l := range links() {
			if l["type"] == "local" && l["remark"] == "mx" {
				n++
			}
		}
		return n
	}
	if countLocal() == 0 {
		t.Fatalf("no local links after adding the inbound: %v", links())
	}
	id := idOf(t, "mx")
	if err := saveInboundHost(s, "edit", `{"id":`+id+`,"type":"mixed","tag":"mx","enable":false,"listen":"::","listen_port":1080}`); err != nil {
		t.Fatal(err)
	}
	if n := countLocal(); n != 0 {
		t.Fatalf("%d local links left for a disabled inbound: %v", n, links())
	}
	if !strings.Contains(string(mustJSON(links())), "socks5://ext") {
		t.Fatalf("external link lost: %v", links())
	}
	if err := saveInboundHost(s, "edit", `{"id":`+id+`,"type":"mixed","tag":"mx","enable":true,"listen":"::","listen_port":1080}`); err != nil {
		t.Fatal(err)
	}
	if countLocal() == 0 {
		t.Fatalf("links not restored on switch-on: %v", links())
	}
}

func saveInboundHost(s *InboundService, act, data string) error {
	tx := database.GetDB().Begin()
	if err := s.Save(tx, act, json.RawMessage(data), "", "panel.example"); err != nil {
		tx.Rollback()
		return err
	}
	return tx.Commit().Error
}

// A listener that hands its connections on (detour) breaks silently when its
// target is not running, so the switch refuses to leave it that way.
func TestEnableSwitchRefusesBrokenDetour(t *testing.T) {
	s := enableTestService(t)
	mustSaveInbound(t, s, "new", `{"type":"shadowsocks","tag":"ss","listen":"127.0.0.1","listen_port":1090,"method":"2022-blake3-aes-128-gcm","password":"AAAAAAAAAAAAAAAAAAAAAA=="}`)
	mustSaveInbound(t, s, "new", `{"type":"mixed","tag":"front","listen":"::","listen_port":1091,"detour":"ss"}`)

	err := saveInbound(t, s, "edit", `{"id":`+idOf(t, "ss")+`,"type":"shadowsocks","tag":"ss","enable":false,"listen":"127.0.0.1","listen_port":1090,"method":"2022-blake3-aes-128-gcm","password":"AAAAAAAAAAAAAAAAAAAAAA=="}`)
	if err == nil || !strings.Contains(err.Error(), "front") {
		t.Fatalf("switching off a detour target was not refused: %v", err)
	}
	// Front off first, then the target may go.
	mustSaveInbound(t, s, "edit", `{"id":`+idOf(t, "front")+`,"type":"mixed","tag":"front","enable":false,"listen":"::","listen_port":1091,"detour":"ss"}`)
	mustSaveInbound(t, s, "edit", `{"id":`+idOf(t, "ss")+`,"type":"shadowsocks","tag":"ss","enable":false,"listen":"127.0.0.1","listen_port":1090,"method":"2022-blake3-aes-128-gcm","password":"AAAAAAAAAAAAAAAAAAAAAA=="}`)
	// And the front cannot come back while its target is off.
	err = saveInbound(t, s, "edit", `{"id":`+idOf(t, "front")+`,"type":"mixed","tag":"front","enable":true,"listen":"::","listen_port":1091,"detour":"ss"}`)
	if err == nil || !strings.Contains(err.Error(), "switched off") {
		t.Fatalf("switching on an inbound whose detour is off was not refused: %v", err)
	}
}

// A node replica follows the node's own switch; a node too old to send one
// keeps its replicas on.
func TestReplicaFollowsNodeEnable(t *testing.T) {
	off, err := buildReplicaInbound(json.RawMessage(`{"id":5,"type":"vless","tag":"v","enable":false,"listen_port":443}`), 1)
	if err != nil {
		t.Fatal(err)
	}
	if off.Enable || strings.Contains(string(off.Options), "enable") {
		t.Fatalf("replica enable=%v options=%s", off.Enable, off.Options)
	}
	old, err := buildReplicaInbound(json.RawMessage(`{"id":5,"type":"vless","tag":"v","listen_port":443}`), 1)
	if err != nil {
		t.Fatal(err)
	}
	if !old.Enable {
		t.Fatal("a replica from a node without the switch came out disabled")
	}
}
