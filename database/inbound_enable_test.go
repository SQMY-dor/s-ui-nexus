package database

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database/model"

	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
)

// Inbounds stored before the enable column existed must come out enabled, or
// an upgrade would take every inbound offline (upstream s-ui #1327).
func TestEnableColumnDefaultsExistingInboundsOn(t *testing.T) {
	path := filepath.Join(t.TempDir(), "test.db")
	if err := InitDB(path); err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("ALTER TABLE inbounds DROP COLUMN enable").Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("INSERT INTO inbounds (type, tag) VALUES ('mixed', 'old')").Error; err != nil {
		t.Fatal(err)
	}
	if err := InitDB(path); err != nil {
		t.Fatal(err)
	}
	var inbound model.Inbound
	if err := db.Where("tag = ?", "old").First(&inbound).Error; err != nil {
		t.Fatal(err)
	}
	if !inbound.Enable {
		t.Fatal("existing inbound came out disabled after the enable column was added")
	}
}

// A column with a default of true is left out of an insert when it is false,
// so a switched-off inbound or node came back switched on from a backup.
func TestBackupKeepsDisabledInboundsAndNodes(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("SUI_DB_FOLDER", dir)
	if err := InitDB(filepath.Join(dir, "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&model.Inbound{Type: "mixed", Tag: "off", Enable: false, EnableSet: true}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&model.Inbound{Type: "mixed", Tag: "on"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Create(&model.Node{Name: "n1", BaseUrl: "https://n1.example.com", Token: "t"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&model.Node{}).Where("name = ?", "n1").Update("enable", false).Error; err != nil {
		t.Fatal(err)
	}

	raw, err := GetDb("")
	if err != nil {
		t.Fatalf("GetDb: %v", err)
	}
	backupPath := filepath.Join(t.TempDir(), "backup.db")
	if err := os.WriteFile(backupPath, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	bdb, err := gorm.Open(sqlite.Open(backupPath), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if sqlDB, e := bdb.DB(); e == nil {
			_ = sqlDB.Close()
		}
	})
	for tag, want := range map[string]bool{"off": false, "on": true} {
		var enable bool
		if err := bdb.Raw("SELECT enable FROM inbounds WHERE tag = ?", tag).Scan(&enable).Error; err != nil {
			t.Fatal(err)
		}
		if enable != want {
			t.Errorf("inbound %s: enable=%v in the backup, want %v", tag, enable, want)
		}
	}
	var nodeEnable bool
	if err := bdb.Raw("SELECT enable FROM nodes WHERE name = ?", "n1").Scan(&nodeEnable).Error; err != nil {
		t.Fatal(err)
	}
	if nodeEnable {
		t.Error("a disabled node came back enabled from the backup")
	}
}
