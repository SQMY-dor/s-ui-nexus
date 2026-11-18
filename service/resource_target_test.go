package service

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

func TestResolveDeleteTarget(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "del.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	inbound := model.Inbound{Type: "mixed", Tag: "smoke-mixed", Options: json.RawMessage(`{"listen_port":21080}`)}
	numericTag := model.Inbound{Type: "mixed", Tag: "77", Options: json.RawMessage(`{"listen_port":21081}`)}
	client := model.Client{Enable: true, Name: "alice", Inbounds: json.RawMessage(`[]`)}
	for _, row := range []interface{}{&inbound, &numericTag, &client} {
		if err := db.Create(row).Error; err != nil {
			t.Fatal(err)
		}
	}
	idStr := func(id uint) string { b, _ := json.Marshal(id); return string(b) }

	cases := []struct {
		resource, data, want string
	}{
		{"inbounds", `"smoke-mixed"`, `"smoke-mixed"`},
		{"inbounds", idStr(inbound.Id), `"smoke-mixed"`},
		{"inbounds", `"` + idStr(inbound.Id) + `"`, `"smoke-mixed"`},
		{"inbounds", `{"id":` + idStr(inbound.Id) + `}`, `"smoke-mixed"`},
		{"inbounds", `{"id":"` + idStr(inbound.Id) + `"}`, `"smoke-mixed"`},
		{"inbounds", `{"tag":"smoke-mixed"}`, `"smoke-mixed"`},
		{"inbounds", `"77"`, `"77"`}, // a tag wins over an id
		{"clients", idStr(client.Id), idStr(client.Id)},
		{"clients", `"alice"`, idStr(client.Id)},
		{"clients", `{"name":"alice"}`, idStr(client.Id)},
		{"clients", `{"id":` + idStr(client.Id) + `}`, idStr(client.Id)},
	}
	for _, tc := range cases {
		got, err := ResolveDeleteTarget(db, tc.resource, json.RawMessage(tc.data))
		if err != nil {
			t.Errorf("%s %s: %v", tc.resource, tc.data, err)
			continue
		}
		if string(got) != tc.want {
			t.Errorf("%s %s = %s, want %s", tc.resource, tc.data, got, tc.want)
		}
	}

	for _, tc := range []struct{ resource, data string }{
		{"inbounds", `"missing"`},
		{"inbounds", `999`},
		{"inbounds", `{"id":999}`},
		{"outbounds", `"no-such-outbound"`},
		{"clients", `"bob"`},
		{"tls", `1`},
		{"nodes", `{"id":5}`},
	} {
		if _, err := ResolveDeleteTarget(db, tc.resource, json.RawMessage(tc.data)); !errors.Is(err, ErrResourceNotFound) {
			t.Errorf("%s %s: err = %v, want ErrResourceNotFound", tc.resource, tc.data, err)
		}
	}
	if _, err := ResolveDeleteTarget(db, "inbounds", json.RawMessage(`[1]`)); err == nil || errors.Is(err, ErrResourceNotFound) {
		t.Errorf("array target should be a validation error, got %v", err)
	}
}
