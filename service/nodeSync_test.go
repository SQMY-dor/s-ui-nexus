// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package service

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"

	"github.com/op/go-logging"
)

func TestIsNodeOwnedRemark(t *testing.T) {
	nodes := []string{"eu", "eu-2", "us west"}
	tests := []struct {
		name   string
		remark string
		want   bool
	}{
		{"exact node link", "[eu] vless-in", true},
		{"node name with a space", "[us west] tcp", true},
		// The "] " terminator is what keeps one node's prefix from swallowing
		// another whose name starts with it; node names may not contain
		// brackets precisely so this holds.
		{"longer node name is not a prefix match", "[eu-2] vless-in", true},
		{"user link that only looks the part", "[backup] vless-in", false},
		{"bare tag", "vless-in", false},
		{"empty", "", false},
		{"prefix without the separator", "[eu]vless-in", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := isNodeOwnedRemark(tc.remark, nodes); got != tc.want {
				t.Errorf("isNodeOwnedRemark(%q) = %v, want %v", tc.remark, got, tc.want)
			}
		})
	}
}

// Regression guard: an inbound delete used to strip anything shaped like
// "[...] <tag>", which caught user-authored links too.
func TestIsNodeLinkFor(t *testing.T) {
	nodes := []string{"eu", "us"}
	tests := []struct {
		name   string
		remark string
		tag    string
		want   bool
	}{
		{"node link for this tag", "[eu] vless-in", "vless-in", true},
		{"node link for another tag", "[eu] trojan-in", "vless-in", false},
		{"unknown node", "[backup] vless-in", "vless-in", false},
		{"bare tag is not a node link", "vless-in", "vless-in", false},
		{"tag is a suffix of a longer one", "[eu] my-vless-in", "vless-in", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := isNodeLinkFor(tc.remark, tc.tag, nodes); got != tc.want {
				t.Errorf("isNodeLinkFor(%q, %q) = %v, want %v", tc.remark, tc.tag, got, tc.want)
			}
		})
	}
}

func TestClientDiffers(t *testing.T) {
	cfg := json.RawMessage(`{"vless":{"uuid":"a"}}`)
	cfgReordered := json.RawMessage(`{"vless":{"uuid":"a"} }`)
	cfgOther := json.RawMessage(`{"vless":{"uuid":"b"}}`)
	inbounds := json.RawMessage(`[1,2]`)

	want := func(mods map[string]interface{}) map[string]interface{} {
		m := map[string]interface{}{
			"enable":   true,
			"expiry":   int64(100),
			"config":   cfg,
			"inbounds": inbounds,
		}
		for k, v := range mods {
			m[k] = v
		}
		return m
	}
	cur := nodeClientState{Enable: true, Expiry: 100, Config: cfg, Inbounds: inbounds}

	t.Run("identical", func(t *testing.T) {
		if clientDiffers(want(nil), cur) {
			t.Error("identical state reported as differing")
		}
	})
	t.Run("config compared structurally, not byte-wise", func(t *testing.T) {
		c := cur
		c.Config = cfgReordered
		if clientDiffers(want(nil), c) {
			t.Error("whitespace-only config difference reported as differing")
		}
	})
	t.Run("real config change", func(t *testing.T) {
		c := cur
		c.Config = cfgOther
		if !clientDiffers(want(nil), c) {
			t.Error("changed config not detected")
		}
	})
	t.Run("enable and expiry", func(t *testing.T) {
		if !clientDiffers(want(map[string]interface{}{"enable": false}), cur) {
			t.Error("changed enable not detected")
		}
		if !clientDiffers(want(map[string]interface{}{"expiry": int64(200)}), cur) {
			t.Error("changed expiry not detected")
		}
	})
	t.Run("inbounds", func(t *testing.T) {
		c := cur
		c.Inbounds = json.RawMessage(`[1]`)
		if !clientDiffers(want(nil), c) {
			t.Error("changed inbounds not detected")
		}
	})

	// The reason the both-sides-present guard exists: an older node's clients
	// projection has no config column, and treating that as "differs" re-pushed
	// every client every round, each push appending a credential-bearing
	// changes row that nothing prunes.
	//
	// The same assertion pins the accepted cost of that guard: a node-side
	// config someone CLEARED is indistinguishable from an old node's missing
	// column, so the safety net will not repair it either.
	t.Run("absent node config is not a difference", func(t *testing.T) {
		c := cur
		c.Config = nil
		if clientDiffers(want(nil), c) {
			t.Error("node without a config column reported as differing")
		}
		c.Config = json.RawMessage("")
		if clientDiffers(want(nil), c) {
			t.Error("node with an empty config reported as differing")
		}
	})
	t.Run("absent master config is not a difference", func(t *testing.T) {
		if clientDiffers(want(map[string]interface{}{"config": json.RawMessage(nil)}), cur) {
			t.Error("configless master client reported as differing")
		}
	})
	// Other fields must still be compared when config cannot be.
	t.Run("other fields still compared without config", func(t *testing.T) {
		c := cur
		c.Config = nil
		c.Enable = false
		if !clientDiffers(want(nil), c) {
			t.Error("enable change missed when config was absent")
		}
	})

}

// Adoption stores a snapshot of the node's inbound and, until issue #196,
// nothing ever refreshed it: an edit made on the node itself -- the listen port,
// most visibly -- reached neither this panel's inbounds list nor the "[node] "
// links it regenerates into the subscription, so the master went on handing out
// a dead port. What matters about refreshReplicas is that it rewrites the row
// from the node, keeps the columns adoption deliberately owns, leaves a replica
// the node no longer lists alone, and writes nothing when nothing moved.
func TestRefreshReplicasPullsNodeSideEdits(t *testing.T) {
	logger.InitLogger(logging.CRITICAL)

	// refreshReplicas reaches for database.GetDB() itself.
	dir := t.TempDir()
	if err := database.InitDB(filepath.Join(dir, "replicas.db")); err != nil {
		t.Fatalf("init db: %v", err)
	}
	db := database.GetDB()

	const nodeId = uint(3)
	nid := nodeId
	// The snapshot as adoption left it. Both halves carry the old port: Options
	// is what the inbounds list reads, OutJson is what link generation reads.
	adopted := model.Inbound{
		Type: "vless", Tag: "vless-node", NodeId: &nid,
		Options: json.RawMessage(`{"listen_port":443}`),
		OutJson: json.RawMessage(`{"server":"node.example","server_port":443}`),
		Addrs:   json.RawMessage(`[]`),
	}
	if err := db.Create(&adopted).Error; err != nil {
		t.Fatalf("seed replica: %v", err)
	}
	// A replica the node no longer lists — renamed there, or deleted. Clients
	// point their inbounds array at this row, so it must survive untouched.
	orphan := model.Inbound{
		Type: "trojan", Tag: "gone-from-node", NodeId: &nid,
		Options: json.RawMessage(`{"listen_port":8080}`),
	}
	if err := db.Create(&orphan).Error; err != nil {
		t.Fatalf("seed orphan replica: %v", err)
	}

	var queried []string
	node := &model.Node{Id: nodeId, Name: "tokyo", Enable: true}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/app/apiv2/inbounds" {
			t.Errorf("unexpected node request: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		queried = append(queried, r.URL.Query().Get("id"))
		// The node's panel shape (MarshalFull), with the port now moved and a
		// tls_id of its own that adoption drops on this side.
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"success":true,"obj":{"inbounds":[
			{"id":9,"type":"vless","tag":"vless-node","tls_id":4,"addrs":[],
			 "out_json":{"server":"node.example","server_port":8443},
			 "listen":"::","listen_port":8443}]}}`))
	}))
	defer srv.Close()
	node.BaseUrl = srv.URL

	var svc NodeSyncService
	// "gone-from-node" is absent from the map on purpose — that is what a rename
	// or delete on the node side looks like from here.
	tagToId := map[string]uint{"vless-node": 9}
	// The return value is what tells runReconcile to regenerate the links right
	// away instead of waiting for the refreshNodeLinks past the client push,
	// which every push error returns before reaching. Getting it wrong strands
	// the panel showing the new port while the subscription serves the old one.
	if !svc.refreshReplicas(node, srv.Client(), tagToId) {
		t.Fatal("refreshReplicas reported no change after the node moved the port")
	}

	if len(queried) != 1 || queried[0] != "9" {
		t.Fatalf("node was asked for id=%v, want exactly the one adopted tag", queried)
	}

	var got model.Inbound
	if err := db.Model(model.Inbound{}).Where("id = ?", adopted.Id).Find(&got).Error; err != nil {
		t.Fatalf("reload replica: %v", err)
	}
	var opts map[string]interface{}
	if err := json.Unmarshal(got.Options, &opts); err != nil {
		t.Fatalf("unmarshal refreshed options: %v", err)
	}
	if port, _ := asInt64(opts["listen_port"]); port != 8443 {
		t.Errorf("options listen_port = %v, want the node's 8443", opts["listen_port"])
	}
	// Panel-only keys must stay out of Options — the same contract adoption has.
	for _, k := range []string{"id", "tls_id", "out_json", "addrs", "type", "tag"} {
		if _, leaked := opts[k]; leaked {
			t.Errorf("refreshed options leaked the panel-only key %q", k)
		}
	}
	var out map[string]interface{}
	if err := json.Unmarshal(got.OutJson, &out); err != nil {
		t.Fatalf("unmarshal refreshed out_json: %v", err)
	}
	if port, _ := asInt64(out["server_port"]); port != 8443 {
		t.Errorf("out_json server_port = %v, want the node's 8443", out["server_port"])
	}
	// The columns this panel owns, not the node: TLS terminates on the node so
	// adoption drops its tls_id, and node_id is what marks the row a replica.
	if got.TlsId != 0 {
		t.Errorf("tls_id = %d, want the node's own left behind", got.TlsId)
	}
	if got.NodeId == nil || *got.NodeId != nodeId {
		t.Errorf("node_id = %v, want it preserved as %d", got.NodeId, nodeId)
	}

	// The half users actually feel: the subscription link now names the new port.
	links := genNodeReplicaLinks(&got, &model.Client{
		Name: "u1", Config: json.RawMessage(`{"vless":{"uuid":"uuid-1"}}`),
	})
	if len(links) != 1 || !strings.Contains(links[0], ":8443") {
		t.Errorf("regenerated links = %v, want one naming port 8443", links)
	}

	var stillThere model.Inbound
	if err := db.Model(model.Inbound{}).Where("id = ?", orphan.Id).Find(&stillThere).Error; err != nil {
		t.Fatalf("reload orphan replica: %v", err)
	}
	if stillThere.Tag != "gone-from-node" || !jsonEqual(stillThere.Options, json.RawMessage(`{"listen_port":8080}`)) {
		t.Errorf("replica absent from the node was modified: %+v", stillThere)
	}

	// One audit row for the one row that moved.
	var changes int64
	if err := db.Model(model.Changes{}).Count(&changes).Error; err != nil {
		t.Fatalf("count changes: %v", err)
	}
	if changes != 1 {
		t.Fatalf("changes rows = %d after the first refresh, want 1", changes)
	}

	// Idempotence is load-bearing, not tidiness: this runs on the 5s heartbeat
	// and the hourly sweep, and every write costs an unpruned changes row plus a
	// LastUpdate bump that repaints every open panel.
	if svc.refreshReplicas(node, srv.Client(), tagToId) {
		t.Error("refreshReplicas reported a change on an unchanged round")
	}
	if err := db.Model(model.Changes{}).Count(&changes).Error; err != nil {
		t.Fatalf("count changes: %v", err)
	}
	if changes != 1 {
		t.Errorf("changes rows = %d after an unchanged refresh, want the first 1", changes)
	}
}

// s-ui-nexus: the 30s background backoff follows failures only, so a second
// client saved seconds after a successful push is fanned out immediately.
func TestReconcileBackoffOnlyAfterFailure(t *testing.T) {
	var s NodeSyncService
	const id = uint(9001)
	t.Cleanup(func() {
		reconcileMu.Lock()
		delete(reconcileBusy, id)
		delete(reconcileLast, id)
		reconcileMu.Unlock()
	})

	if _, ok := s.claimReconcile(id, false); !ok {
		t.Fatal("first claim refused")
	}
	if _, ok := s.claimReconcile(id, false); ok {
		t.Fatal("single-flight: a second claim while busy must be refused")
	}
	if _, ok := s.claimReconcile(id, true); ok {
		t.Fatal("single-flight applies to interactive claims too")
	}
	s.releaseReconcile(id, false)
	if _, ok := s.claimReconcile(id, false); !ok {
		t.Fatal("after a successful run the next background claim must not wait for the backoff")
	}
	s.releaseReconcile(id, true)
	if _, ok := s.claimReconcile(id, false); ok {
		t.Fatal("after a failed run background claims must back off")
	}
	if _, ok := s.claimReconcile(id, true); !ok {
		t.Fatal("interactive claims skip the backoff")
	}
	s.releaseReconcileNoBackoff(id)
	reconcileMu.Lock()
	reconcileLast[id] = time.Now().Add(-reconcileBackoff - time.Second)
	reconcileMu.Unlock()
	if _, ok := s.claimReconcile(id, false); !ok {
		t.Fatal("background claims resume once the backoff has elapsed")
	}
}
