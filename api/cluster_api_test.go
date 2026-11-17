// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.
//
// The node-side apiv2 contract a cluster master depends on (service/node.go,
// service/nodeSync.go): save's sync flag, clients?full=1, status.sbd and the
// redacted nodes list.

package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/core"
	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	projectLogger "github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"
	"github.com/gin-gonic/gin"
	"github.com/op/go-logging"
)

func newTestV2Router(t *testing.T) *gin.Engine {
	t.Helper()
	if err := database.InitDB(filepath.Join(t.TempDir(), "panel.db")); err != nil {
		t.Fatal(err)
	}
	projectLogger.InitLogger(logging.ERROR)
	service.NewConfigService(core.NewCore())
	gin.SetMode(gin.TestMode)
	router := gin.New()
	a := &APIv2Handler{tokens: []TokenInMemory{{Token: "node-token", Username: "admin"}}}
	a.initRouter(router.Group("/app/apiv2"))
	return router
}

func v2Do(t *testing.T, router *gin.Engine, method, path string, form url.Values) Msg {
	t.Helper()
	var req *http.Request
	if form != nil {
		req = httptest.NewRequest(method, path, strings.NewReader(form.Encode()))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	} else {
		req = httptest.NewRequest(method, path, nil)
	}
	req.Header.Set("Token", "node-token")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("%s %s: HTTP %d %s", method, path, w.Code, w.Body.String())
	}
	var msg Msg
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
		t.Fatalf("%s %s: %v (%s)", method, path, err, w.Body.String())
	}
	return msg
}

func TestV2SaveRejectsMalformedSync(t *testing.T) {
	router := newTestV2Router(t)
	form := url.Values{
		"object": {"outbounds"}, "action": {"new"}, "apply": {"false"},
		"data": {`{"type":"direct","tag":"synced-out"}`}, "sync": {"maybe"},
	}
	if msg := v2Do(t, router, http.MethodPost, "/app/apiv2/save", form); msg.Success {
		t.Fatal("sync=maybe must be rejected")
	}
	var count int64
	database.GetDB().Model(&model.Outbound{}).Where("tag = ?", "synced-out").Count(&count)
	if count != 0 {
		t.Fatal("a rejected sync value must not save anything")
	}
	form.Set("sync", "true")
	if msg := v2Do(t, router, http.MethodPost, "/app/apiv2/save", form); !msg.Success {
		t.Fatalf("sync=true save failed: %s", msg.Msg)
	}
	database.GetDB().Model(&model.Outbound{}).Where("tag = ?", "synced-out").Count(&count)
	if count != 1 {
		t.Fatal("sync=true save was not committed")
	}
}

func TestV2ClusterReadShapes(t *testing.T) {
	router := newTestV2Router(t)
	db := database.GetDB()
	if err := db.Create(&model.Client{
		Enable: true, Name: "alice", Group: "@cluster", Up: 7, Down: 9, Expiry: 42,
		Config: json.RawMessage(`{"vless":{"uuid":"u"}}`), Inbounds: json.RawMessage(`[1]`), Links: json.RawMessage(`[]`),
	}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&model.Node{Name: "eu", BaseUrl: "https://eu.example", WebPath: "/app/", Token: "secret", Enable: true}).Error; err != nil {
		t.Fatal(err)
	}

	decodeClients := func(msg Msg) []map[string]interface{} {
		t.Helper()
		raw, _ := json.Marshal(msg.Obj)
		var obj struct {
			Clients []map[string]interface{} `json:"clients"`
		}
		if err := json.Unmarshal(raw, &obj); err != nil || len(obj.Clients) != 1 {
			t.Fatalf("clients payload %s: %v", raw, err)
		}
		return obj.Clients
	}
	full := decodeClients(v2Do(t, router, http.MethodGet, "/app/apiv2/clients?full=1", nil))[0]
	for _, k := range []string{"id", "name", "enable", "config", "inbounds", "expiry", "group", "up", "down"} {
		if _, ok := full[k]; !ok {
			t.Errorf("clients?full=1 lacks %q: %v", k, full)
		}
	}
	if _, ok := full["links"]; ok {
		t.Error("clients?full=1 must not carry links")
	}
	lean := decodeClients(v2Do(t, router, http.MethodGet, "/app/apiv2/clients", nil))[0]
	if _, ok := lean["config"]; ok {
		t.Error("the plain clients list must stay lean (no config)")
	}

	status := v2Do(t, router, http.MethodGet, "/app/apiv2/status?r=cpu,mem,sys,sbd", nil)
	raw, _ := json.Marshal(status.Obj)
	var st struct {
		Mem map[string]interface{} `json:"mem"`
		Sys map[string]interface{} `json:"sys"`
		Sbd map[string]interface{} `json:"sbd"`
	}
	if err := json.Unmarshal(raw, &st); err != nil {
		t.Fatal(err)
	}
	if _, ok := st.Sbd["running"]; !ok {
		t.Errorf("status.sbd lacks running: %s", raw)
	}
	if _, ok := st.Sbd["version"]; !ok {
		t.Errorf("status.sbd lacks version: %s", raw)
	}
	if _, ok := st.Sys["appVersion"]; !ok {
		t.Errorf("status.sys lacks appVersion: %s", raw)
	}

	nodes := v2Do(t, router, http.MethodGet, "/app/apiv2/nodes", nil)
	raw, _ = json.Marshal(nodes.Obj)
	if strings.Contains(string(raw), "secret") || !strings.Contains(string(raw), `"tokenSet":true`) {
		t.Fatalf("nodes list must redact the token: %s", raw)
	}
}
