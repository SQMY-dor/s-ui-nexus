package service

import (
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

func TestGetEnabledBySubscriptionKeyKeepsLegacyNameLinks(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	client := model.Client{
		Enable: true,
		Name:   "alice",
	}
	if err := db.Create(&client).Error; err != nil {
		t.Fatal(err)
	}

	service := ClientService{}
	legacy, err := service.GetEnabledBySubscriptionKey(db, "alice")
	if err != nil {
		t.Fatal(err)
	}
	if legacy.Name != "alice" {
		t.Fatalf("legacy lookup returned %q", legacy.Name)
	}
	if legacy.SubId == "" {
		t.Fatal("legacy lookup should backfill a subscription id")
	}

	current, err := service.GetEnabledBySubscriptionKey(db, legacy.SubId)
	if err != nil {
		t.Fatal(err)
	}
	if current.Id != legacy.Id {
		t.Fatalf("sub id lookup returned client %d, want %d", current.Id, legacy.Id)
	}
}

func TestClientSaveTreatsMissingLinksAsEmpty(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	service := ClientService{}
	payload := []byte(`{"name":"smoke-client","enable":true,"inbounds":[],"config":{"mixed":{"username":"smoke-client","password":"smoke1234"}},"volume":0,"expiry":0}`)
	if _, err := service.Save(db, "new", payload, "example.com"); err != nil {
		t.Fatalf("new without links failed: %v", err)
	}
	var stored model.Client
	if err := db.Where("name = ?", "smoke-client").First(&stored).Error; err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(strings.TrimSpace(string(stored.Links)), "[") {
		t.Fatalf("links = %q, want a JSON array", stored.Links)
	}

	// Missing config on new is stored as {} instead of crashing.
	if _, err := service.Save(db, "new", []byte(`{"name":"bare","enable":true,"inbounds":[]}`), "example.com"); err != nil {
		t.Fatalf("new without config failed: %v", err)
	}

	// An edit that omits config keeps the stored credentials.
	edit := []byte(`{"id":` + strconv.Itoa(int(stored.Id)) + `,"name":"smoke-client","enable":true,"inbounds":[]}`)
	if _, err := service.Save(db, "edit", edit, "example.com"); err != nil {
		t.Fatalf("edit without config/links failed: %v", err)
	}
	var edited model.Client
	db.First(&edited, stored.Id)
	if !strings.Contains(string(edited.Config), "smoke1234") {
		t.Fatalf("edit dropped stored config: %s", edited.Config)
	}

	// Wrong shapes name the field.
	_, err := service.Save(db, "new", []byte(`{"name":"bad","links":{"a":1},"inbounds":[]}`), "example.com")
	if err == nil || !strings.Contains(err.Error(), "links") {
		t.Fatalf("expected a links validation error, got %v", err)
	}
	_, err = service.Save(db, "new", []byte(`{"name":"bad2","config":[1],"inbounds":[]}`), "example.com")
	if err == nil || !strings.Contains(err.Error(), "config") {
		t.Fatalf("expected a config validation error, got %v", err)
	}
}
