package service

import (
	"encoding/json"
	"os"
	"time"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/util/common"

	"gorm.io/gorm"
)

// Per-inbound traffic caps. Ported from shenaba/2s-ui (GPL-3.0) #289/#308,
// adapted for s-ui-nexus (no notify/hub/bot).
//
// The counters are the row's own up/down, which the stats flush advances; the
// cap is judged by DepleteJob once a minute with the same comparison as a
// client's quota (up+down > volume). A spent inbound is switched off with the
// inbound's own switch (Enable), so it ends up exactly where switching it off
// by hand leaves it: out of the core, its connections closed, its links out of
// every subscription. Unlike a save, the cap does not refuse to switch off a
// detour target -- the traffic is spent -- so a chain behind it stops too.

// depletedInboundsWhere selects the local, enabled inbounds over their cap.
const depletedInboundsWhere = "enable = ? AND node_id IS NULL AND volume > 0 AND up + down > volume"

// DepleteInbounds switches off every local inbound over its cap and takes it
// out of the running core. Returns the tags it switched off.
func (s *InboundService) DepleteInbounds() ([]string, error) {
	db := database.GetDB()
	dt := time.Now().Unix()

	var tags []string
	err := db.Transaction(func(tx *gorm.DB) error {
		var ids []uint
		if err := tx.Model(model.Inbound{}).Where(depletedInboundsWhere, true).Pluck("id", &ids).Error; err != nil {
			return err
		}
		if len(ids) == 0 {
			return nil
		}
		if err := tx.Model(model.Inbound{}).Where("id IN ?", ids).Update("enable", false).Error; err != nil {
			return err
		}
		var spent []model.Inbound
		if err := tx.Model(model.Inbound{}).Preload("Tls").Where("id IN ?", ids).Find(&spent).Error; err != nil {
			return err
		}
		// Off, an inbound has no links (inboundLinks), so this only takes
		// them out; no hostname is needed to build none.
		if err := s.ClientService.UpdateLinksByInboundChange(tx, &spent, "", ""); err != nil {
			return err
		}
		changes := make([]model.Changes, 0, len(spent))
		for _, inb := range spent {
			tags = append(tags, inb.Tag)
			changes = append(changes, model.Changes{
				DateTime: dt,
				Actor:    "DepleteJob",
				Key:      "inbounds",
				Action:   "disable",
				Obj:      json.RawMessage(mustJSON(inb.Tag)),
			})
		}
		return tx.Create(&changes).Error
	})
	if err != nil {
		return nil, err
	}
	if len(tags) == 0 {
		return nil, nil
	}
	// After the commit: the row already says off, so a removal that fails only
	// leaves it running until the next restart builds the config without it.
	if corePtr != nil && corePtr.IsRunning() {
		for _, tag := range tags {
			if err := corePtr.RemoveInbound(tag); err != nil && err != os.ErrInvalid {
				logger.Warning("inbound ", tag, ": could not take it out of the core: ", err)
			}
			if box := corePtr.GetInstance(); box != nil {
				box.ConnTracker().CloseConnByInbound(tag)
			}
		}
	}
	LastUpdate.Store(dt)
	logger.Info("switched off inbounds out of traffic: ", tags)
	return tags, nil
}

// resetTraffic zeroes one local inbound's counters, the "resetTraffic" action
// of an inbounds save. data is the tag, as for "del".
//
// An inbound the cap had switched off -- off and over its cap until now -- is
// switched back on, as a save switching it on would: its detour checked,
// started in the core, its links back. One the operator switched off with
// traffic to spare stays off.
func (s *InboundService) resetTraffic(tx *gorm.DB, data json.RawMessage, hostname string) error {
	var tag string
	if err := json.Unmarshal(data, &tag); err != nil {
		return err
	}
	var inbound model.Inbound
	err := tx.Model(model.Inbound{}).Preload("Tls").Where("tag = ?", tag).Limit(1).Find(&inbound).Error
	if err != nil {
		return err
	}
	if inbound.Id == 0 {
		return common.NewErrorf("inbound %s not found", tag)
	}
	if inbound.NodeId != nil {
		return common.NewError("inbound belongs to a cluster node: manage it on the node panel")
	}
	reenable := !inbound.Enable && inbound.Depleted()
	if reenable {
		inbound.Enable = true
		if err = checkInboundDetour(tx, &inbound, inbound.Tag); err != nil {
			return err
		}
	}
	update := map[string]interface{}{"up": 0, "down": 0}
	if reenable {
		update["enable"] = true
	}
	err = tx.Model(model.Inbound{}).Where("id = ?", inbound.Id).Updates(update).Error
	if err != nil {
		return err
	}
	if !reenable {
		return nil
	}
	// Reads the row inside this transaction, where it is on now. Inside, as a
	// save adds one: a listener that cannot start fails the reset.
	if err = s.RestartInbounds(tx, []uint{inbound.Id}); err != nil {
		return err
	}
	return s.ClientService.UpdateLinksByInboundChange(tx, &[]model.Inbound{inbound}, hostname, inbound.Tag)
}

// keepInboundCap settles an edit's traffic cap against the stored row.
//
// A volume the request did not carry keeps the stored one, as Enable does: a
// caller written before the cap would otherwise lift it with an unrelated
// edit. The counters are carried over only so the check below sees them -- the
// save never writes them. Switching a spent inbound back on is refused:
// DepleteJob would take it out again within the minute. Resetting its traffic,
// or raising the cap in the same save, is what lets it back.
func keepInboundCap(inbound, old *model.Inbound) error {
	if !inbound.VolumeSet {
		inbound.Volume = old.Volume
	}
	inbound.Up, inbound.Down = old.Up, old.Down
	if inbound.Enable && !old.Enable && inbound.Depleted() {
		return common.NewErrorf("inbound %s has used up its traffic limit: reset its traffic or raise the limit first", inbound.Tag)
	}
	return nil
}
