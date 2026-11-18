package service

import (
	"bytes"
	"encoding/json"
	"strings"
	"time"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/util"
	"github.com/felixbennettio/s-ui-next/util/common"

	"gorm.io/gorm"
)

type ClientService struct{}

func (s *ClientService) Get(id string) (*[]model.Client, error) {
	if id == "" {
		return s.GetAll()
	}
	return s.getById(id)
}

func (s *ClientService) getById(id string) (*[]model.Client, error) {
	db := database.GetDB()
	if err := s.EnsureClientSubIds(db); err != nil {
		return nil, err
	}
	var client []model.Client
	err := db.Model(model.Client{}).Where("id in ?", strings.Split(id, ",")).Scan(&client).Error
	if err != nil {
		return nil, err
	}

	return &client, nil
}

func (s *ClientService) GetAll() (*[]model.Client, error) {
	db := database.GetDB()
	if err := s.EnsureClientSubIds(db); err != nil {
		return nil, err
	}
	var clients []model.Client
	err := db.Model(model.Client{}).
		Select("`id`, `enable`, `name`, `sub_id`, `desc`, `group`, `inbounds`, `up`, `down`, `volume`, `expiry`").
		Scan(&clients).Error
	if err != nil {
		return nil, err
	}
	return &clients, nil
}

// GetAllWithConfig is the cluster reconcile read (apiv2 GET clients?full=1):
// the list projection plus config, which a master needs to diff its desired
// @cluster clients against this node's copy. Links stay out.
func (s *ClientService) GetAllWithConfig() (*[]model.Client, error) {
	db := database.GetDB()
	if err := s.EnsureClientSubIds(db); err != nil {
		return nil, err
	}
	var clients []model.Client
	err := db.Model(model.Client{}).
		Select("`id`, `enable`, `name`, `sub_id`, `desc`, `group`, `inbounds`, `up`, `down`, `volume`, `expiry`, `config`").
		Scan(&clients).Error
	if err != nil {
		return nil, err
	}
	return &clients, nil
}

func (s *ClientService) EnsureClientSubIds(db *gorm.DB) error {
	var clients []model.Client
	if err := db.Model(model.Client{}).Select("id, sub_id").Where("sub_id = '' OR sub_id IS NULL").Find(&clients).Error; err != nil {
		return err
	}
	for _, client := range clients {
		subId, err := s.newClientSubId(db)
		if err != nil {
			return err
		}
		if err := db.Model(model.Client{}).Where("id = ?", client.Id).Update("sub_id", subId).Error; err != nil {
			return err
		}
	}
	return nil
}

func (s *ClientService) ensureClientSubId(tx *gorm.DB, client *model.Client) error {
	client.SubId = strings.TrimSpace(client.SubId)
	if client.SubId != "" {
		return nil
	}
	if client.Id > 0 {
		var existing string
		if err := tx.Model(model.Client{}).Select("sub_id").Where("id = ?", client.Id).Scan(&existing).Error; err != nil {
			return err
		}
		if strings.TrimSpace(existing) != "" {
			client.SubId = strings.TrimSpace(existing)
			return nil
		}
	}
	subId, err := s.newClientSubId(tx)
	if err != nil {
		return err
	}
	client.SubId = subId
	return nil
}

func (s *ClientService) newClientSubId(tx *gorm.DB) (string, error) {
	for i := 0; i < 10; i++ {
		subId := common.Random(24)
		var count int64
		if err := tx.Model(model.Client{}).Where("sub_id = ?", subId).Count(&count).Error; err != nil {
			return "", err
		}
		if count == 0 {
			return subId, nil
		}
	}
	return "", common.NewError("unable to generate a unique subscription id")
}

func (s *ClientService) GetEnabledBySubscriptionKey(db *gorm.DB, key string) (*model.Client, error) {
	key = strings.TrimSpace(key)
	client := &model.Client{}
	if err := s.EnsureClientSubIds(db); err != nil {
		return nil, err
	}
	err := db.Model(model.Client{}).Where("enable = true and sub_id = ?", key).First(client).Error
	if err == nil {
		return client, nil
	}
	if !database.IsNotFound(err) {
		return nil, err
	}

	// Keep existing subscriptions usable after upgrading from name-based URLs.
	err = db.Model(model.Client{}).Where("enable = true and name = ?", key).First(client).Error
	if err != nil {
		return nil, err
	}
	return client, nil
}

func (s *ClientService) Save(tx *gorm.DB, act string, data json.RawMessage, hostname string) ([]uint, error) {
	var err error
	var inboundIds []uint

	switch act {
	case "new", "edit":
		var client model.Client
		err = json.Unmarshal(data, &client)
		if err != nil {
			return nil, err
		}
		err = s.ensureClientSubId(tx, &client)
		if err != nil {
			return nil, err
		}
		err = s.updateLinksWithFixedInbounds(tx, []*model.Client{&client}, hostname)
		if err != nil {
			return nil, err
		}
		if act == "edit" {
			// Find changed inbounds
			inboundIds, err = s.findInboundsChanges(tx, &client, false)
			if err != nil {
				return nil, err
			}
		} else {
			err = json.Unmarshal(client.Inbounds, &inboundIds)
			if err != nil {
				return nil, err
			}
		}
		err = tx.Save(&client).Error
		if err != nil {
			return nil, err
		}
	case "addbulk":
		var clients []*model.Client
		err = json.Unmarshal(data, &clients)
		if err != nil {
			return nil, err
		}
		if len(clients) == 0 {
			return nil, common.NewError("bulk client list is empty")
		}
		for _, client := range clients {
			err = s.ensureClientSubId(tx, client)
			if err != nil {
				return nil, err
			}
		}
		err = json.Unmarshal(clients[0].Inbounds, &inboundIds)
		if err != nil {
			return nil, err
		}
		err = s.updateLinksWithFixedInbounds(tx, clients, hostname)
		if err != nil {
			return nil, err
		}
		err = tx.Save(clients).Error
		if err != nil {
			return nil, err
		}
	case "editbulk":
		var clients []*model.Client
		err = json.Unmarshal(data, &clients)
		if err != nil {
			return nil, err
		}
		if len(clients) == 0 {
			return nil, common.NewError("bulk client list is empty")
		}
		for _, client := range clients {
			err = s.ensureClientSubId(tx, client)
			if err != nil {
				return nil, err
			}
			changedInboundIds, err := s.findInboundsChanges(tx, client, true)
			if err != nil {
				return nil, err
			}
			if len(changedInboundIds) > 0 {
				inboundIds = common.UnionUintArray(inboundIds, changedInboundIds)
			}
		}
		if len(inboundIds) > 0 {
			err = s.updateLinksWithFixedInbounds(tx, clients, hostname)
			if err != nil {
				return nil, err
			}
		}
		err = tx.Save(clients).Error
		if err != nil {
			return nil, err
		}
	case "delbulk":
		var ids []uint
		err = json.Unmarshal(data, &ids)
		if err != nil {
			return nil, err
		}
		for _, id := range ids {
			var client model.Client
			err = tx.Where("id = ?", id).First(&client).Error
			if err != nil {
				return nil, err
			}
			var clientInbounds []uint
			err = json.Unmarshal(client.Inbounds, &clientInbounds)
			if err != nil {
				return nil, err
			}
			inboundIds = common.UnionUintArray(inboundIds, clientInbounds)
		}
		err = tx.Where("id in ?", ids).Delete(model.Client{}).Error
		if err != nil {
			return nil, err
		}
	case "del":
		var id uint
		err = json.Unmarshal(data, &id)
		if err != nil {
			return nil, err
		}
		var client model.Client
		err = tx.Where("id = ?", id).First(&client).Error
		if err != nil {
			return nil, err
		}
		err = json.Unmarshal(client.Inbounds, &inboundIds)
		if err != nil {
			return nil, err
		}
		err = tx.Where("id = ?", id).Delete(model.Client{}).Error
		if err != nil {
			return nil, err
		}
	default:
		return nil, common.NewErrorf("unknown action: %s", act)
	}

	return inboundIds, nil
}

func (s *ClientService) updateLinksWithFixedInbounds(tx *gorm.DB, clients []*model.Client, hostname string) error {
	var err error
	var inbounds []model.Inbound
	var inboundIds []uint

	err = json.Unmarshal(clients[0].Inbounds, &inboundIds)
	if err != nil {
		return err
	}

	// Zero inbounds means removing local links only.
	// node_id IS NULL: local links for cluster replicas would carry THIS
	// panel's hostname; their real links are generated by the node sync as
	// "[node] " external links (which the non-local pass below preserves).
	if len(inboundIds) > 0 {
		err = tx.Model(model.Inbound{}).Preload("Tls").Where("id in ? and type in ? and node_id IS NULL", inboundIds, util.InboundTypeWithLink).Find(&inbounds).Error
		if err != nil {
			return err
		}
	}

	// Node links are system-owned (refreshNodeLinks rewrites them), so for an
	// existing client they come from the stored row, not the request payload:
	// a tab loaded before the last reconcile would otherwise strip them all.
	// A node link is re-attached only while its replica inbound is still bound
	// to the client, so revoking access takes effect even for an offline node.
	// Ported from shenaba/2s-ui (GPL-3.0).
	var nodeNames []string
	if err = tx.Model(model.Node{}).Pluck("name", &nodeNames).Error; err != nil {
		return err
	}
	keptReplicaTags := map[string]bool{}
	storedLinksById := map[uint]json.RawMessage{}
	if len(nodeNames) > 0 {
		if len(inboundIds) > 0 {
			var replicas []model.Inbound
			if err = tx.Model(model.Inbound{}).Select("id", "tag").
				Where("id in ? and node_id IS NOT NULL", inboundIds).Find(&replicas).Error; err != nil {
				return err
			}
			for _, r := range replicas {
				keptReplicaTags[r.Tag] = true
			}
		}
		var ids []uint
		for _, client := range clients {
			if client.Id != 0 {
				ids = append(ids, client.Id)
			}
		}
		if len(ids) > 0 {
			var rows []struct {
				Id    uint
				Links json.RawMessage
			}
			if err = tx.Model(model.Client{}).Select("id", "links").
				Where("id in ?", ids).Scan(&rows).Error; err != nil {
				return err
			}
			for _, r := range rows {
				storedLinksById[r.Id] = r.Links
			}
		}
	}

	for index, client := range clients {
		var clientLinks []map[string]string
		err = json.Unmarshal(client.Links, &clientLinks)
		if err != nil {
			return err
		}

		newClientLinks := []map[string]string{}
		for _, inbound := range inbounds {
			newLinks := util.LinkGenerator(client.Config, &inbound, hostname)
			for _, newLink := range newLinks {
				newClientLinks = append(newClientLinks, map[string]string{
					"remark": inbound.Tag,
					"type":   "local",
					"uri":    newLink,
				})
			}
		}

		// A client being created has no stored row to re-attach from, so its
		// payload links are kept verbatim.
		reattach := client.Id != 0 && len(nodeNames) > 0

		// Add non local links (node-owned ones re-attach from the DB below)
		for _, clientLink := range clientLinks {
			if clientLink["type"] == "local" {
				continue
			}
			if reattach && isNodeOwnedRemark(clientLink["remark"], nodeNames) {
				continue
			}
			newClientLinks = append(newClientLinks, clientLink)
		}
		if stored := storedLinksById[client.Id]; reattach && len(stored) > 0 {
			var storedLinks []map[string]string
			if err = json.Unmarshal(stored, &storedLinks); err != nil {
				return err
			}
			for _, l := range storedLinks {
				if l["type"] == "local" || !isNodeOwnedRemark(l["remark"], nodeNames) {
					continue
				}
				sep := strings.Index(l["remark"], "] ")
				if sep < 0 || !keptReplicaTags[l["remark"][sep+2:]] {
					continue // replica no longer bound to this client
				}
				newClientLinks = append(newClientLinks, l)
			}
		}

		clients[index].Links, err = json.MarshalIndent(newClientLinks, "", "  ")
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *ClientService) UpdateClientsOnInboundAdd(tx *gorm.DB, initIds string, inboundId uint, hostname string) error {
	clientIds := strings.Split(initIds, ",")
	var clients []model.Client
	err := tx.Model(model.Client{}).Where("id in ?", clientIds).Find(&clients).Error
	if err != nil {
		return err
	}
	var inbound model.Inbound
	err = tx.Model(model.Inbound{}).Preload("Tls").Where("id = ?", inboundId).Find(&inbound).Error
	if err != nil {
		return err
	}
	for _, client := range clients {
		// Add inbounds
		var clientInbounds []uint
		json.Unmarshal(client.Inbounds, &clientInbounds)
		clientInbounds = append(clientInbounds, inboundId)
		client.Inbounds, err = json.MarshalIndent(clientInbounds, "", "  ")
		if err != nil {
			return err
		}
		// Add links
		var clientLinks, newClientLinks []map[string]string
		json.Unmarshal(client.Links, &clientLinks)
		newLinks := util.LinkGenerator(client.Config, &inbound, hostname)
		for _, newLink := range newLinks {
			newClientLinks = append(newClientLinks, map[string]string{
				"remark": inbound.Tag,
				"type":   "local",
				"uri":    newLink,
			})
		}
		for _, clientLink := range clientLinks {
			if clientLink["remark"] != inbound.Tag {
				newClientLinks = append(newClientLinks, clientLink)
			}
		}

		client.Links, err = json.MarshalIndent(newClientLinks, "", "  ")
		if err != nil {
			return err
		}
		err = tx.Save(&client).Error
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *ClientService) UpdateClientsOnInboundDelete(tx *gorm.DB, id uint, tag string) error {
	var clientIds []uint
	err := tx.Raw("SELECT clients.id FROM clients, json_each(clients.inbounds) AS je WHERE je.value = ?", id).Scan(&clientIds).Error
	if err != nil {
		return err
	}
	if len(clientIds) == 0 {
		return nil
	}
	var nodeNames []string
	if err = tx.Model(model.Node{}).Pluck("name", &nodeNames).Error; err != nil {
		return err
	}
	var clients []model.Client
	err = tx.Model(model.Client{}).Where("id IN ?", clientIds).Find(&clients).Error
	if err != nil {
		return err
	}
	for _, client := range clients {
		// Delete inbounds (empty array, never null: the panel indexes into it)
		var clientInbounds []uint
		newClientInbounds := []uint{}
		json.Unmarshal(client.Inbounds, &clientInbounds)
		for _, clientInbound := range clientInbounds {
			if clientInbound != id {
				newClientInbounds = append(newClientInbounds, clientInbound)
			}
		}
		client.Inbounds, err = json.MarshalIndent(newClientInbounds, "", "  ")
		if err != nil {
			return err
		}
		// Delete links. A node link for the same inbound carries the tag
		// behind a "[<node>] " prefix, so an exact match alone would leave it
		// behind for good.
		var clientLinks []map[string]string
		newClientLinks := []map[string]string{}
		json.Unmarshal(client.Links, &clientLinks)
		for _, clientLink := range clientLinks {
			remark := clientLink["remark"]
			if remark == tag || isNodeLinkFor(remark, tag, nodeNames) {
				continue
			}
			newClientLinks = append(newClientLinks, clientLink)
		}
		client.Links, err = json.MarshalIndent(newClientLinks, "", "  ")
		if err != nil {
			return err
		}
		err = tx.Save(&client).Error
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *ClientService) UpdateLinksByInboundChange(tx *gorm.DB, inbounds *[]model.Inbound, hostname string, oldTag string) error {
	var err error
	for _, inbound := range *inbounds {
		var clientIds []uint
		err = tx.Raw("SELECT clients.id FROM clients, json_each(clients.inbounds) AS je WHERE je.value = ?", inbound.Id).Scan(&clientIds).Error
		if err != nil {
			return err
		}
		if len(clientIds) == 0 {
			continue
		}
		var clients []model.Client
		err = tx.Model(model.Client{}).Where("id IN ?", clientIds).Find(&clients).Error
		if err != nil {
			return err
		}
		for _, client := range clients {
			var clientLinks, newClientLinks []map[string]string
			json.Unmarshal(client.Links, &clientLinks)
			newLinks := util.LinkGenerator(client.Config, &inbound, hostname)
			for _, newLink := range newLinks {
				newClientLinks = append(newClientLinks, map[string]string{
					"remark": inbound.Tag,
					"type":   "local",
					"uri":    newLink,
				})
			}
			for _, clientLink := range clientLinks {
				if clientLink["type"] != "local" || (clientLink["remark"] != inbound.Tag && clientLink["remark"] != oldTag) {
					newClientLinks = append(newClientLinks, clientLink)
				}
			}

			client.Links, err = json.MarshalIndent(newClientLinks, "", "  ")
			if err != nil {
				return err
			}
			err = tx.Save(&client).Error
			if err != nil {
				return err
			}
		}
	}
	return nil
}

func (s *ClientService) DepleteClients() (inboundIds []uint, err error) {
	inboundIds, _, err = s.DepleteClientsWithChange()
	return inboundIds, err
}

// DepleteClientsWithChange is DepleteClients that also reports whether any
// client row changed (disabled, reset, or delay-start activated), so callers
// can fan the change out to cluster nodes.
func (s *ClientService) DepleteClientsWithChange() (inboundIds []uint, changed bool, err error) {
	var clients []model.Client
	var changes []model.Changes

	dt := time.Now().Unix()
	db := database.GetDB()

	tx := db.Begin()
	if tx.Error != nil {
		return nil, false, tx.Error
	}
	defer tx.Rollback()

	// Reset clients
	var resetChanged bool
	inboundIds, resetChanged, err = s.ResetClients(tx, dt)
	if err != nil {
		return nil, false, err
	}

	// Deplete clients
	err = tx.Model(model.Client{}).Where("enable = true AND ((volume >0 AND up+down > volume) OR (expiry > 0 AND expiry < ?))", dt).Scan(&clients).Error
	if err != nil {
		return nil, false, err
	}

	for _, client := range clients {
		logger.Debug("Client ", client.Name, " is going to be disabled")
		var userInbounds []uint
		json.Unmarshal(client.Inbounds, &userInbounds)
		// Find changed inbounds
		inboundIds = common.UnionUintArray(inboundIds, userInbounds)
		changes = append(changes, model.Changes{
			DateTime: dt,
			Actor:    "DepleteJob",
			Key:      "clients",
			Action:   "disable",
			Obj:      json.RawMessage("\"" + client.Name + "\""),
		})
	}

	// Save changes
	if len(changes) > 0 {
		err = tx.Model(model.Client{}).Where("enable = true AND ((volume >0 AND up+down > volume) OR (expiry > 0 AND expiry < ?))", dt).Update("enable", false).Error
		if err != nil {
			return nil, false, err
		}
		err = tx.Model(model.Changes{}).Create(&changes).Error
		if err != nil {
			return nil, false, err
		}
	}
	if err = tx.Commit().Error; err != nil {
		return nil, false, err
	}
	changed = resetChanged || len(changes) > 0
	if changed {
		LastUpdate.Store(dt)
	}
	if checkpointErr := db.Exec("PRAGMA wal_checkpoint(FULL)").Error; checkpointErr != nil {
		logger.Error("Error checkpointing WAL: ", checkpointErr.Error())
	}

	return inboundIds, changed, nil
}

func (s *ClientService) ResetClients(tx *gorm.DB, dt int64) ([]uint, bool, error) {
	var err error
	var resetClients, allClients []*model.Client
	var changes []model.Changes
	var inboundIds []uint
	// Set delay start without periodic reset
	err = tx.Model(model.Client{}).
		Where("enable = true AND delay_start = true AND auto_reset = false AND (Up + Down) > 0").Find(&resetClients).Error
	if err != nil {
		return nil, false, err
	}
	for _, client := range resetClients {
		client.Expiry = dt + (int64(client.ResetDays) * 86400)
		client.DelayStart = false
		changes = append(changes, model.Changes{
			DateTime: dt,
			Actor:    "ResetJob",
			Key:      "clients",
			Action:   "reset",
			Obj:      json.RawMessage("\"" + client.Name + "\""),
		})
	}
	allClients = append(allClients, resetClients...)

	// Set delay start with periodic reset
	err = tx.Model(model.Client{}).
		Where("enable = true AND delay_start = true AND auto_reset = true AND (Up + Down) > 0").Find(&resetClients).Error
	if err != nil {
		return nil, false, err
	}
	for _, client := range resetClients {
		client.NextReset = dt + (int64(client.ResetDays) * 86400)
		client.DelayStart = false
		changes = append(changes, model.Changes{
			DateTime: dt,
			Actor:    "ResetJob",
			Key:      "clients",
			Action:   "reset",
			Obj:      json.RawMessage("\"" + client.Name + "\""),
		})
	}
	allClients = append(allClients, resetClients...)

	// Set periodic reset
	err = tx.Model(model.Client{}).
		Where("delay_start = false AND auto_reset = true AND next_reset < ?", dt).Find(&resetClients).Error
	if err != nil {
		return nil, false, err
	}
	for _, client := range resetClients {
		client.NextReset = dt + (int64(client.ResetDays) * 86400)
		client.TotalUp += client.Up
		client.TotalDown += client.Down
		client.Up = 0
		client.Down = 0
		if !client.Enable {
			client.Enable = true
			var clientInboundIds []uint
			json.Unmarshal(client.Inbounds, &clientInboundIds)
			inboundIds = common.UnionUintArray(inboundIds, clientInboundIds)
		}
	}
	allClients = append(allClients, resetClients...)

	// Save clients
	if len(allClients) > 0 {
		err = tx.Save(allClients).Error
		if err != nil {
			return nil, false, err
		}
	}

	// Save changes
	if len(changes) > 0 {
		err = tx.Model(model.Changes{}).Create(&changes).Error
		if err != nil {
			return nil, false, err
		}
	}
	return inboundIds, len(allClients) > 0 || len(changes) > 0, nil
}

func (s *ClientService) findInboundsChanges(tx *gorm.DB, client *model.Client, fillOmitted bool) ([]uint, error) {
	var err error
	var oldClient model.Client
	var oldInboundIds, newInboundIds []uint
	err = tx.Model(model.Client{}).Where("id = ?", client.Id).First(&oldClient).Error
	if err != nil {
		return nil, err
	}
	if fillOmitted {
		client.Links = oldClient.Links
		client.Config = oldClient.Config
	}
	err = json.Unmarshal(oldClient.Inbounds, &oldInboundIds)
	if err != nil {
		return nil, err
	}
	err = json.Unmarshal(client.Inbounds, &newInboundIds)
	if err != nil {
		return nil, err
	}

	// Check client.Config changes
	if !bytes.Equal(oldClient.Config, client.Config) ||
		oldClient.Name != client.Name ||
		oldClient.Enable != client.Enable {
		return common.UnionUintArray(oldInboundIds, newInboundIds), nil
	}

	// Check client.Inbounds changes
	diffInbounds := common.DiffUintArray(oldInboundIds, newInboundIds)

	return diffInbounds, nil
}
