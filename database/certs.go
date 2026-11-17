// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package database

import (
	"github.com/felixbennettio/s-ui-next/database/model"

	"gorm.io/gorm"
)

// Tables of the automatic HTTPS feature, migrated and backed up from here so
// the port stays out of the shared AutoMigrate / backup lists.

func migrateCertTables(db *gorm.DB) error {
	return db.AutoMigrate(&model.Cert{})
}

// backupCertTables copies the manual certificate registrations into a backup
// database (2s-ui includes the certs table in its backup too).
func backupCertTables(src, dst *gorm.DB) error {
	if err := dst.AutoMigrate(&model.Cert{}); err != nil {
		return err
	}
	var certs []model.Cert
	if err := src.Model(&model.Cert{}).Scan(&certs).Error; err != nil {
		return err
	}
	if len(certs) == 0 {
		return nil
	}
	return dst.CreateInBatches(certs, 100).Error
}
