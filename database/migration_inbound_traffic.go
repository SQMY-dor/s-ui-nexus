package database

import (
	"github.com/felixbennettio/s-ui-next/database/model"

	"gorm.io/gorm"
)

// Ported from shenaba/2s-ui (GPL-3.0) #289, adapted for s-ui-nexus.
//
// Upstream marks the seeding done with a row in the settings table; here the
// schema itself is the marker, so no internal flag can leak into the settings
// API (GetAllSetting returns every row) or be lost to a settings reset, which
// would re-seed and overwrite counters the cap had been judging.

// needsInboundTrafficSeed reports, before AutoMigrate, whether the inbounds
// table exists without the traffic counters -- an upgrade (or a restored
// pre-cap backup) whose counters must be seeded once AutoMigrate adds them.
// A fresh install has no table and no history to seed from.
func needsInboundTrafficSeed(db *gorm.DB) bool {
	m := db.Migrator()
	return m.HasTable(&model.Inbound{}) && !m.HasColumn(&model.Inbound{}, "up")
}

// seedInboundTraffic seeds the inbounds' up/down counters from the stats
// table. Run after AutoMigrate, which adds the columns (NOT NULL DEFAULT 0).
//
// Seeding keeps an inbound from reading as having moved nothing on the first
// start after the upgrade. It is only as complete as the table was -- traffic
// history off, or past its retention, is not there to count. Local inbounds
// only: a replica's traffic is never counted here.
func seedInboundTraffic(db *gorm.DB) error {
	return db.Exec(`UPDATE inbounds SET
		up = COALESCE((SELECT SUM(traffic) FROM stats
			WHERE stats.resource = 'inbound' AND stats.tag = inbounds.tag AND stats.direction = 1), 0),
		down = COALESCE((SELECT SUM(traffic) FROM stats
			WHERE stats.resource = 'inbound' AND stats.tag = inbounds.tag AND stats.direction = 0), 0)
		WHERE node_id IS NULL`).Error
}
