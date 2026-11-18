// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.
//
// End-to-end cluster test for s-ui-nexus: a fake node panel speaking the apiv2
// shapes node.go / nodeSync.go parse, driven through heartbeat, adoption,
// reconcile, traffic collection, rename and delete.

package service

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"

	"github.com/op/go-logging"
	"gorm.io/gorm"
)

type fakeNodeClient struct {
	Id       uint            `json:"id"`
	Name     string          `json:"name"`
	Enable   bool            `json:"enable"`
	Config   json.RawMessage `json:"config"`
	Inbounds json.RawMessage `json:"inbounds"`
	Expiry   int64           `json:"expiry"`
	Volume   int64           `json:"volume"`
	Group    string          `json:"group"`
	Up       int64           `json:"up"`
	Down     int64           `json:"down"`
}

type fakeNode struct {
	mu      sync.Mutex
	t       *testing.T
	running bool
	clients map[string]*fakeNodeClient
	nextId  uint
	saves   []string // "action:name"
	lastRaw map[string]map[string]interface{}
	token   string
}

func newFakeNode(t *testing.T) (*fakeNode, *httptest.Server) {
	fn := &fakeNode{t: t, running: true, clients: map[string]*fakeNodeClient{}, nextId: 100,
		lastRaw: map[string]map[string]interface{}{}, token: "node-token"}
	srv := httptest.NewServer(http.HandlerFunc(fn.serve))
	t.Cleanup(srv.Close)
	return fn, srv
}

func (fn *fakeNode) ok(w http.ResponseWriter, obj interface{}) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"success": true, "msg": "", "obj": obj})
}

func (fn *fakeNode) serve(w http.ResponseWriter, r *http.Request) {
	fn.mu.Lock()
	defer fn.mu.Unlock()
	if r.Header.Get("Token") != fn.token {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"success": false, "msg": "invalid token"})
		return
	}
	switch r.URL.Path {
	case "/app/apiv2/status":
		fn.ok(w, map[string]interface{}{
			"cpu": 1.5, "mem": map[string]interface{}{"current": 10, "total": 100},
			"sys": map[string]interface{}{"appVersion": "nexus-test"},
			"sbd": map[string]interface{}{"running": fn.running, "version": "v1.13.14"},
		})
	case "/app/apiv2/onlines":
		fn.ok(w, map[string]interface{}{"user": []string{"alice"}})
	case "/app/apiv2/inbounds":
		if id := r.URL.Query().Get("id"); id != "" {
			if id != "5" {
				fn.ok(w, map[string]interface{}{"inbounds": []interface{}{}})
				return
			}
			fn.ok(w, map[string]interface{}{"inbounds": []interface{}{map[string]interface{}{
				"id": 5, "type": "vless", "tag": "vless-node", "tls_id": 2,
				"addrs":    []interface{}{},
				"out_json": map[string]interface{}{"type": "vless", "tag": "vless-node", "server": "node.example", "server_port": 443},
				"listen":   "::", "listen_port": 443,
			}}})
			return
		}
		fn.ok(w, map[string]interface{}{"inbounds": []interface{}{
			map[string]interface{}{"id": 5, "type": "vless", "tag": "vless-node", "tls_id": 2, "listen": "::", "listen_port": 443},
		}})
	case "/app/apiv2/clients":
		if r.URL.Query().Get("full") != "1" {
			fn.t.Errorf("clients read without full=1")
		}
		list := []*fakeNodeClient{}
		for _, c := range fn.clients {
			list = append(list, c)
		}
		fn.ok(w, map[string]interface{}{"clients": list})
	case "/app/apiv2/save":
		if r.FormValue("object") != "clients" {
			fn.t.Errorf("unexpected save object %q", r.FormValue("object"))
		}
		act := r.FormValue("action")
		data := r.FormValue("data")
		switch act {
		case "new", "edit":
			var raw map[string]interface{}
			_ = json.Unmarshal([]byte(data), &raw)
			var c fakeNodeClient
			if err := json.Unmarshal([]byte(data), &c); err != nil {
				fn.t.Errorf("bad client payload: %v", err)
			}
			if act == "new" {
				fn.nextId++
				c.Id = fn.nextId
			}
			fn.clients[c.Name] = &c
			fn.lastRaw[act+":"+c.Name] = raw
			fn.saves = append(fn.saves, act+":"+c.Name)
		case "del":
			id, _ := strconv.Atoi(data)
			for name, c := range fn.clients {
				if c.Id == uint(id) {
					delete(fn.clients, name)
					fn.saves = append(fn.saves, "del:"+name)
				}
			}
		}
		fn.ok(w, map[string]interface{}{})
	default:
		fn.t.Errorf("unexpected node request: %s", r.URL.Path)
		w.WriteHeader(http.StatusNotFound)
	}
}

func clientLinks(t *testing.T, db *gorm.DB, name string) []map[string]string {
	t.Helper()
	var c model.Client
	if err := db.Model(model.Client{}).Where("name = ?", name).First(&c).Error; err != nil {
		t.Fatalf("load client %s: %v", name, err)
	}
	var links []map[string]string
	_ = json.Unmarshal(c.Links, &links)
	return links
}

func TestClusterNodeLifecycle(t *testing.T) {
	logger.InitLogger(logging.CRITICAL)
	if err := database.InitDB(filepath.Join(t.TempDir(), "cluster.db")); err != nil {
		t.Fatalf("init db: %v", err)
	}
	db := database.GetDB()
	nodeStatusMu.Lock()
	nodeStatuses = map[uint]NodeStatus{}
	nodeStatusMu.Unlock()

	fn, srv := newFakeNode(t)

	// --- create node through the config save path (redacted token on read)
	var nodeSvc NodeService
	nodeJSON, _ := json.Marshal(map[string]interface{}{
		"name": "n1", "enable": true, "baseUrl": srv.URL + "/", "webPath": "app", "token": "node-token",
	})
	if err := db.Transaction(func(tx *gorm.DB) error { return nodeSvc.Save(tx, "new", nodeJSON) }); err != nil {
		t.Fatalf("save node: %v", err)
	}
	var node model.Node
	if err := db.Model(model.Node{}).Where("name = ?", "n1").First(&node).Error; err != nil {
		t.Fatalf("load node: %v", err)
	}
	if node.WebPath != "/app/" || strings.HasSuffix(node.BaseUrl, "/") {
		t.Fatalf("node not normalised: %+v", node)
	}
	all, err := nodeSvc.GetAll()
	if err != nil || len(all) != 1 {
		t.Fatalf("GetAll = %v, %v", all, err)
	}
	if _, leaked := all[0]["token"]; leaked || all[0]["tokenSet"] != true {
		t.Fatalf("GetAll must hide the token and report tokenSet: %v", all[0])
	}

	// --- heartbeat states
	nodeSvc.RefreshAll()
	st := nodeSvc.GetStatuses()[node.Id]
	if st.State != "online" || st.AppVersion != "nexus-test" || st.CoreVersion != "v1.13.14" || st.Mem.Total != 100 {
		t.Fatalf("status after probe = %+v", st)
	}
	fn.mu.Lock()
	fn.running = false
	fn.mu.Unlock()
	nodeSvc.RefreshAll()
	if got := nodeSvc.GetStatuses()[node.Id].State; got != "core-stopped" {
		t.Fatalf("state with core stopped = %q", got)
	}
	fn.mu.Lock()
	fn.token = "rotated"
	fn.mu.Unlock()
	nodeSvc.RefreshAll()
	if got := nodeSvc.GetStatuses()[node.Id]; got.State != "offline" || got.Error == "" || got.LastOnline == 0 {
		t.Fatalf("state with a rejected token = %+v", got)
	}
	fn.mu.Lock()
	fn.token = "node-token"
	fn.running = true
	fn.mu.Unlock()
	nodeSvc.RefreshAll()

	// --- adoption
	var ns NodeSyncService
	list, err := ns.FetchNodeInbounds(node.Id)
	if err != nil || len(list) != 1 || list[0].Adopted {
		t.Fatalf("FetchNodeInbounds = %v, %v", list, err)
	}
	if err := ns.AdoptInbounds(node.Id, []string{"vless-node"}, "", "admin"); err != nil {
		t.Fatalf("adopt: %v", err)
	}
	var replica model.Inbound
	if err := db.Model(model.Inbound{}).Where("tag = ?", "vless-node").First(&replica).Error; err != nil {
		t.Fatalf("load replica: %v", err)
	}
	if replica.NodeId == nil || *replica.NodeId != node.Id || replica.TlsId != 0 {
		t.Fatalf("replica row = %+v", replica)
	}
	if err := ns.AdoptInbounds(node.Id, []string{"vless-node"}, "", "admin"); err == nil {
		t.Fatal("re-adopting an existing tag must fail loudly")
	}

	// A local inbound plus the replica: only the local one reaches sing-box.
	local := model.Inbound{Type: "mixed", Tag: "local-mixed", Options: json.RawMessage(`{"listen":"::","listen_port":1080}`)}
	if err := db.Create(&local).Error; err != nil {
		t.Fatalf("seed local inbound: %v", err)
	}
	var inbSvc InboundService
	cfgs, err := inbSvc.GetAllConfig(db)
	if err != nil || len(cfgs) != 1 || !strings.Contains(string(cfgs[0]), "local-mixed") {
		t.Fatalf("GetAllConfig must exclude replicas: %s, %v", cfgs, err)
	}
	for _, raw := range cfgs {
		if strings.Contains(string(raw), "node_id") {
			t.Fatalf("sing-box config leaked node_id: %s", raw)
		}
	}
	// Replicas are read-only through the normal inbound save.
	editJSON, _ := json.Marshal(map[string]interface{}{"id": replica.Id, "type": "vless", "tag": "vless-node", "listen_port": 1})
	if err := db.Transaction(func(tx *gorm.DB) error { return inbSvc.Save(tx, "edit", editJSON, "", "master.example") }); err == nil {
		t.Fatal("editing a replica must be rejected")
	}
	editJSON, _ = json.Marshal(map[string]interface{}{"type": "vless", "tag": "sneaky", "node_id": node.Id})
	if err := db.Transaction(func(tx *gorm.DB) error { return inbSvc.Save(tx, "new", editJSON, "", "master.example") }); err == nil {
		t.Fatal("creating a replica through the inbound save must be rejected")
	}

	// --- master client referencing the replica, then reconcile
	alice := model.Client{
		Enable: true, Name: "alice", Expiry: 4102444800, Volume: 1 << 30, Group: "vip",
		Config:   json.RawMessage(`{"vless":{"name":"alice","uuid":"11111111-2222-3333-4444-555555555555"}}`),
		Inbounds: json.RawMessage("[" + strconv.Itoa(int(replica.Id)) + "]"),
		Links:    json.RawMessage(`[]`),
	}
	if err := db.Create(&alice).Error; err != nil {
		t.Fatalf("seed client: %v", err)
	}
	if err := ns.ReconcileNow(node.Id); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	fn.mu.Lock()
	pushed := fn.clients["alice"]
	raw := fn.lastRaw["new:alice"]
	fn.mu.Unlock()
	if pushed == nil {
		t.Fatal("alice was not pushed to the node")
	}
	if pushed.Group != clusterGroup || pushed.Volume != 0 || pushed.Expiry != alice.Expiry || string(pushed.Inbounds) != "[5]" || !pushed.Enable {
		t.Fatalf("pushed client = %+v", pushed)
	}
	for _, k := range []string{"limitIp", "tgId", "resetDayOfMonth", "subId", "id"} {
		if _, ok := raw[k]; ok {
			t.Errorf("push payload carries %q, which s-ui-nexus nodes do not take", k)
		}
	}
	links := clientLinks(t, db, "alice")
	if len(links) != 1 || links[0]["remark"] != "[n1] vless-node" || links[0]["type"] != "external" ||
		!strings.Contains(links[0]["uri"], "node.example:443") {
		t.Fatalf("aggregated links = %v", links)
	}
	var dirty bool
	db.Model(model.Node{}).Select("dirty").Where("id = ?", node.Id).Scan(&dirty)
	if dirty {
		t.Fatal("successful reconcile left the node dirty")
	}

	// A second reconcile with nothing changed pushes nothing.
	fn.mu.Lock()
	before := len(fn.saves)
	fn.mu.Unlock()
	if err := ns.ReconcileNow(node.Id); err != nil {
		t.Fatalf("idle reconcile: %v", err)
	}
	fn.mu.Lock()
	if len(fn.saves) != before {
		t.Fatalf("idle reconcile pushed %v", fn.saves[before:])
	}
	fn.mu.Unlock()

	// Client save keeps the node-owned link while the replica stays bound,
	// even when the payload (an older tab) does not carry it.
	var clientSvc ClientService
	edit, _ := json.Marshal(map[string]interface{}{
		"id": alice.Id, "enable": true, "name": "alice", "subId": "", "config": json.RawMessage(alice.Config),
		"inbounds": json.RawMessage(alice.Inbounds), "links": []interface{}{}, "expiry": alice.Expiry, "volume": alice.Volume,
	})
	if err := db.Transaction(func(tx *gorm.DB) error {
		_, err := clientSvc.Save(tx, "edit", edit, "master.example")
		return err
	}); err != nil {
		t.Fatalf("client edit: %v", err)
	}
	links = clientLinks(t, db, "alice")
	if len(links) != 1 || links[0]["remark"] != "[n1] vless-node" {
		t.Fatalf("client save dropped/duplicated the node link: %v", links)
	}

	// --- traffic: node counters fold into the master once, via baselines
	fn.mu.Lock()
	fn.clients["alice"].Up, fn.clients["alice"].Down = 100, 200
	fn.mu.Unlock()
	ns.CollectTraffic()
	ns.CollectTraffic() // no double count
	var got model.Client
	db.Model(model.Client{}).Where("name = ?", "alice").First(&got)
	if got.Up != 100 || got.Down != 200 {
		t.Fatalf("after first collection up/down = %d/%d, want 100/200", got.Up, got.Down)
	}
	fn.mu.Lock()
	fn.clients["alice"].Up, fn.clients["alice"].Down = 150, 260
	fn.mu.Unlock()
	ns.CollectTraffic()
	db.Model(model.Client{}).Where("name = ?", "alice").First(&got)
	if got.Up != 150 || got.Down != 260 {
		t.Fatalf("after second collection up/down = %d/%d, want 150/260", got.Up, got.Down)
	}

	// --- master-side disable is pushed as an edit that keeps node counters
	db.Model(model.Client{}).Where("name = ?", "alice").Update("enable", false)
	ns.MarkAllDirty()
	if err := ns.ReconcileNow(node.Id); err != nil {
		t.Fatalf("reconcile after disable: %v", err)
	}
	fn.mu.Lock()
	c := fn.clients["alice"]
	fn.mu.Unlock()
	if c.Enable || c.Up != 150 || c.Down != 260 {
		t.Fatalf("node client after disable push = %+v (counters must survive the edit)", c)
	}

	// --- rename rewrites the link prefix
	renameJSON, _ := json.Marshal(map[string]interface{}{
		"id": node.Id, "name": "n2", "enable": true, "baseUrl": srv.URL, "webPath": "/app/",
	})
	if err := db.Transaction(func(tx *gorm.DB) error { return nodeSvc.Save(tx, "edit", renameJSON) }); err != nil {
		t.Fatalf("rename node: %v", err)
	}
	var tok string
	db.Model(model.Node{}).Select("token").Where("id = ?", node.Id).Scan(&tok)
	if tok != "node-token" {
		t.Fatalf("edit with empty token must keep the stored one, got %q", tok)
	}
	links = clientLinks(t, db, "alice")
	if len(links) != 1 || links[0]["remark"] != "[n2] vless-node" {
		t.Fatalf("links after rename = %v", links)
	}
	// The edit must not have reset the traffic baselines: an unchanged node
	// counter folds nothing in.
	ns.CollectTraffic()
	db.Model(model.Client{}).Where("name = ?", "alice").First(&got)
	if got.Up != 150 || got.Down != 260 {
		t.Fatalf("node edit reset baselines: up/down = %d/%d, want 150/260", got.Up, got.Down)
	}

	// --- node delete removes replicas and their links
	delJSON, _ := json.Marshal(node.Id)
	if err := db.Transaction(func(tx *gorm.DB) error { return nodeSvc.Save(tx, "del", delJSON) }); err != nil {
		t.Fatalf("delete node: %v", err)
	}
	var replicas int64
	db.Model(model.Inbound{}).Where("node_id IS NOT NULL").Count(&replicas)
	if replicas != 0 {
		t.Fatalf("%d replica(s) left after node delete", replicas)
	}
	links = clientLinks(t, db, "alice")
	if len(links) != 0 {
		t.Fatalf("links after node delete = %v", links)
	}
	db.Model(model.Client{}).Where("name = ?", "alice").First(&got)
	var ids []uint
	_ = json.Unmarshal(got.Inbounds, &ids)
	if len(ids) != 0 {
		t.Fatalf("client still references deleted replicas: %v", ids)
	}
	if _, ok := nodeSvc.GetStatuses()[node.Id]; ok {
		t.Fatal("status snapshot kept a deleted node")
	}
}

func TestDeletingAReplicaIsUnadopt(t *testing.T) {
	logger.InitLogger(logging.CRITICAL)
	if err := database.InitDB(filepath.Join(t.TempDir(), "unadopt.db")); err != nil {
		t.Fatalf("init db: %v", err)
	}
	db := database.GetDB()
	node := model.Node{Name: "eu", BaseUrl: "http://eu.example", WebPath: "/app/", Token: "t", Enable: true}
	if err := db.Create(&node).Error; err != nil {
		t.Fatal(err)
	}
	nid := node.Id
	replica := model.Inbound{Type: "vless", Tag: "eu-vless", NodeId: &nid, Options: json.RawMessage(`{"listen_port":443}`)}
	if err := db.Create(&replica).Error; err != nil {
		t.Fatal(err)
	}
	client := model.Client{
		Enable: true, Name: "bob", Config: json.RawMessage(`{}`),
		Inbounds: json.RawMessage("[" + strconv.Itoa(int(replica.Id)) + "]"),
		Links:    json.RawMessage(`[{"remark":"[eu] eu-vless","type":"external","uri":"vless://x"},{"remark":"[backup] eu-vless","type":"external","uri":"vless://user"}]`),
	}
	if err := db.Create(&client).Error; err != nil {
		t.Fatal(err)
	}
	var inbSvc InboundService
	tag, _ := json.Marshal("eu-vless")
	if err := db.Transaction(func(tx *gorm.DB) error { return inbSvc.Save(tx, "del", tag, "", "master.example") }); err != nil {
		t.Fatalf("un-adopt: %v", err)
	}
	links := clientLinks(t, db, "bob")
	if len(links) != 1 || links[0]["remark"] != "[backup] eu-vless" {
		t.Fatalf("un-adopt must strip exactly the node link, got %v", links)
	}
}
