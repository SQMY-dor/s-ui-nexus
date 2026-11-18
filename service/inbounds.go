package service

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/util"
	"github.com/felixbennettio/s-ui-next/util/common"

	"gorm.io/gorm"
)

type InboundService struct {
	ClientService
}

func (s *InboundService) Get(ids string) (*[]map[string]interface{}, error) {
	if ids == "" {
		return s.GetAll()
	}
	return s.getById(ids)
}

func (s *InboundService) getById(ids string) (*[]map[string]interface{}, error) {
	var inbound []model.Inbound
	var result []map[string]interface{}
	db := database.GetDB()
	err := db.Model(model.Inbound{}).Where("id in ?", strings.Split(ids, ",")).Scan(&inbound).Error
	if err != nil {
		return nil, err
	}
	for _, inb := range inbound {
		inbData, err := inb.MarshalFull()
		if err != nil {
			return nil, err
		}
		result = append(result, *inbData)
	}
	return &result, nil
}

func (s *InboundService) GetAll() (*[]map[string]interface{}, error) {
	db := database.GetDB()
	inbounds := []model.Inbound{}
	err := db.Model(model.Inbound{}).Scan(&inbounds).Error
	if err != nil {
		return nil, err
	}
	var data []map[string]interface{}
	for _, inbound := range inbounds {
		var shadowtls_version uint
		ss_managed := false
		inbData := map[string]interface{}{
			"id":     inbound.Id,
			"type":   inbound.Type,
			"tag":    inbound.Tag,
			"enable": inbound.Enable,
			"tls_id": inbound.TlsId,
		}
		// The traffic cap and the counters it is judged against; a replica's
		// traffic is counted on its node.
		if inbound.NodeId == nil {
			inbData["volume"] = inbound.Volume
			inbData["up"] = inbound.Up
			inbData["down"] = inbound.Down
		}
		if inbound.NodeId != nil {
			inbData["node_id"] = *inbound.NodeId
		}
		if inbound.Options != nil {
			var restFields map[string]json.RawMessage
			if err := json.Unmarshal(inbound.Options, &restFields); err != nil {
				return nil, err
			}
			inbData["listen"] = restFields["listen"]
			inbData["listen_port"] = restFields["listen_port"]
			if inbound.Type == "shadowtls" {
				json.Unmarshal(restFields["version"], &shadowtls_version)
			}
			if inbound.Type == "shadowsocks" {
				json.Unmarshal(restFields["managed"], &ss_managed)
			}
		}
		// The list's "users" key is present exactly when the inbound takes
		// users: a master adopting from this panel reads it that way
		// (nodeInboundTakesUsers), so it must stay on takesUsers.
		if takesUsers(inbound.Type, int(shadowtls_version), ss_managed) {
			users := []string{}
			err = db.Raw("SELECT clients.name FROM clients, json_each(clients.inbounds) as je WHERE je.value = ?", inbound.Id).Scan(&users).Error
			if err != nil {
				return nil, err
			}
			inbData["users"] = users
		}

		data = append(data, inbData)
	}
	return &data, nil
}

func (s *InboundService) FromIds(ids []uint) ([]*model.Inbound, error) {
	db := database.GetDB()
	inbounds := []*model.Inbound{}
	err := db.Model(model.Inbound{}).Where("id in ?", ids).Scan(&inbounds).Error
	if err != nil {
		return nil, err
	}
	return inbounds, nil
}

func (s *InboundService) Save(tx *gorm.DB, act string, data json.RawMessage, initUserIds string, hostname string) error {
	var err error

	switch act {
	case "new", "edit":
		var inbound model.Inbound
		err = inbound.UnmarshalJSON(data)
		if err != nil {
			return err
		}
		// Cluster replicas are read-only on this panel (edit them on their
		// node; reconciliation refreshes the copy). Rejecting here also stops
		// an edit without node_id from silently flipping a replica into a
		// local inbound, which would feed a foreign port to the local core.
		if inbound.NodeId != nil {
			return common.NewError("inbound belongs to a cluster node: manage it on the node panel")
		}
		if inbound.TlsId > 0 {
			err = tx.Model(model.Tls{}).Where("id = ?", inbound.TlsId).Find(&inbound.Tls).Error
			if err != nil {
				return err
			}
		}
		var oldTag string
		// Whether the core holds the stored inbound: a disabled one is not in
		// it, so there is nothing to replace or remove.
		oldRunning := false
		if act == "edit" {
			var old model.Inbound
			err = tx.Model(model.Inbound{}).Select("tag", "node_id", "enable", "volume", "up", "down").
				Where("id = ?", inbound.Id).Find(&old).Error
			if err != nil {
				return err
			}
			if old.NodeId != nil {
				return common.NewError("inbound belongs to a cluster node: manage it on the node panel")
			}
			oldTag = old.Tag
			oldRunning = old.Enable
			// An edit that does not say keeps what is stored. Read as "on" --
			// the default a new inbound gets -- a caller that predates the
			// switch, or a form that rebuilt the object, would turn a disabled
			// inbound back on.
			if !inbound.EnableSet {
				inbound.Enable = old.Enable
			}
			err = keepInboundCap(&inbound, &old)
			if err != nil {
				return err
			}
		}
		err = checkInboundDetour(tx, &inbound, oldTag)
		if err != nil {
			return err
		}

		if corePtr.IsRunning() {
			if act == "edit" && oldRunning {
				err = corePtr.RemoveInbound(oldTag)
				if err != nil && err != os.ErrInvalid {
					return err
				}
				// Switched off: closing the listener leaves the connections
				// it accepted open.
				if !inbound.Enable {
					if box := corePtr.GetInstance(); box != nil {
						box.ConnTracker().CloseConnByInbound(oldTag)
					}
				}
			}

			if inbound.Enable {
				inboundConfig, err := inbound.MarshalJSON()
				if err != nil {
					return err
				}

				if act == "edit" {
					inboundConfig, err = s.addUsers(tx, inboundConfig, inbound.Id, inbound.Type)
				} else {
					inboundConfig, err = s.initUsers(tx, inboundConfig, initUserIds, inbound.Type)
				}
				if err != nil {
					return err
				}

				err = corePtr.AddInbound(inboundConfig)
				if err != nil {
					return err
				}
			}
		}

		err = util.FillOutJson(&inbound, hostname)
		if err != nil {
			return err
		}

		// The counters are the stats flush's alone (see model.Inbound).
		err = tx.Omit("up", "down").Save(&inbound).Error
		if err != nil {
			return err
		}
		switch act {
		case "new":
			err = s.ClientService.UpdateClientsOnInboundAdd(tx, initUserIds, inbound.Id, hostname)
		case "edit":
			err = s.ClientService.UpdateLinksByInboundChange(tx, &[]model.Inbound{inbound}, hostname, oldTag)
		}
		if err != nil {
			return err
		}
	case "del":
		var tag string
		err = json.Unmarshal(data, &tag)
		if err != nil {
			return err
		}
		var old model.Inbound
		err = tx.Model(model.Inbound{}).Select("id", "node_id").Where("tag = ?", tag).Scan(&old).Error
		if err != nil {
			return err
		}
		// Deleting a cluster replica is the un-adopt path: DB-only, the
		// inbound keeps running on its node and the next reconcile strips the
		// pushed @cluster users from it.
		if old.NodeId == nil && corePtr.IsRunning() {
			err = corePtr.RemoveInbound(tag)
			if err != nil && err != os.ErrInvalid {
				return err
			}
		}
		id := old.Id
		err = s.ClientService.UpdateClientsOnInboundDelete(tx, id, tag)
		if err != nil {
			return err
		}
		err = tx.Where("tag = ?", tag).Delete(model.Inbound{}).Error
		if err != nil {
			return err
		}
	case "resetTraffic":
		return s.resetTraffic(tx, data, hostname)
	default:
		return common.NewErrorf("unknown action: %s", act)
	}
	return nil
}

func (s *InboundService) UpdateOutJsons(tx *gorm.DB, inboundIds []uint, hostname string) error {
	var inbounds []model.Inbound
	// A replica's OutJson is the node-side link snapshot (host = the node);
	// rewriting it with this panel's hostname would point subscriptions here.
	err := tx.Model(model.Inbound{}).Preload("Tls").Where("id in ?", inboundIds).Where("node_id IS NULL").Find(&inbounds).Error
	if err != nil {
		return err
	}
	for _, inbound := range inbounds {
		err = util.FillOutJson(&inbound, hostname)
		if err != nil {
			return err
		}
		err = tx.Model(model.Inbound{}).Where("tag = ?", inbound.Tag).Update("out_json", inbound.OutJson).Error
		if err != nil {
			return err
		}
	}

	return nil
}

func (s *InboundService) GetAllConfig(db *gorm.DB) ([]json.RawMessage, error) {
	var inboundsJson []json.RawMessage
	var inbounds []*model.Inbound
	// node_id IS NULL: replicas of remote cluster inbounds must never reach
	// the local core; their ports live on other machines. A disabled inbound
	// stays out too.
	err := db.Model(model.Inbound{}).Preload("Tls").Where("node_id IS NULL AND enable = ?", true).Find(&inbounds).Error
	if err != nil {
		return nil, err
	}
	for _, inbound := range inbounds {
		inboundJson, err := inbound.MarshalJSON()
		if err != nil {
			return nil, err
		}
		inboundJson, err = s.addUsers(db, inboundJson, inbound.Id, inbound.Type)
		if err != nil {
			return nil, err
		}
		inboundsJson = append(inboundsJson, inboundJson)
	}
	return inboundsJson, nil
}

func (s *InboundService) hasUser(inboundType string) bool {
	switch inboundType {
	case "mixed", "socks", "http", "shadowsocks", "vmess", "trojan", "naive", "hysteria", "shadowtls", "tuic", "hysteria2", "vless", "anytls":
		return true
	}
	return false
}

// takesUsers is the one rule for whether an inbound carries a per-client user
// list, so whether clients can be bound to it at all: a type with users,
// shadowtls only from v3, and no managed shadowsocks (its users live outside
// the panel). The inbounds list (GetAll) decides by it, and so, through that
// list, does a master adopting from this panel. Ported from shenaba/2s-ui
// (GPL-3.0) #300.
func takesUsers(inboundType string, shadowtlsVersion int, ssManaged bool) bool {
	if !(&InboundService{}).hasUser(inboundType) {
		return false
	}
	switch inboundType {
	case "shadowtls":
		return shadowtlsVersion >= 3
	case "shadowsocks":
		return !ssManaged
	}
	return true
}

// fetchUsers collects the per-client config for one inbound type. condition is
// a WHERE fragment and args are its placeholders -- callers must not build
// values into the fragment, since one of them comes from a request.
func (s *InboundService) fetchUsers(db *gorm.DB, inboundType string, condition string, inbound map[string]interface{}, args ...interface{}) ([]json.RawMessage, error) {
	if inboundType == "shadowtls" {
		version, _ := inbound["version"].(float64)
		if int(version) < 3 {
			return nil, nil
		}
	}
	if inboundType == "shadowsocks" {
		method, _ := inbound["method"].(string)
		if method == "2022-blake3-aes-128-gcm" {
			inboundType = "shadowsocks16"
		}
	}

	var users []string

	err := db.Raw(
		fmt.Sprintf(`SELECT json_extract(clients.config, "$.%s")
		FROM clients WHERE enable = true AND %s`,
			inboundType, condition), args...).Scan(&users).Error
	if err != nil {
		return nil, err
	}
	var usersJson []json.RawMessage
	for _, user := range users {
		if inboundType == "vless" && inbound["tls"] == nil {
			user = strings.Replace(user, "xtls-rprx-vision", "", -1)
		}
		usersJson = append(usersJson, json.RawMessage(user))
	}
	return usersJson, nil
}

func (s *InboundService) addUsers(db *gorm.DB, inboundJson []byte, inboundId uint, inboundType string) ([]byte, error) {
	if !s.hasUser(inboundType) {
		return inboundJson, nil
	}

	var inbound map[string]interface{}
	err := json.Unmarshal(inboundJson, &inbound)
	if err != nil {
		return nil, err
	}

	inbound["users"], err = s.fetchUsers(db, inboundType, "? IN (SELECT json_each.value FROM json_each(clients.inbounds))", inbound, inboundId)
	if err != nil {
		return nil, err
	}

	return json.Marshal(inbound)
}

// parseClientIds parses the comma-separated initUsers request field. Empty
// parts are skipped; anything else that is not an id is refused.
func parseClientIds(raw string) ([]uint, error) {
	var ids []uint
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		id, err := strconv.ParseUint(part, 10, 32)
		if err != nil {
			return nil, common.NewErrorf("invalid client id %q", part)
		}
		ids = append(ids, uint(id))
	}
	return ids, nil
}

func (s *InboundService) initUsers(db *gorm.DB, inboundJson []byte, clientIds string, inboundType string) ([]byte, error) {
	ClientIds, err := parseClientIds(clientIds)
	if err != nil {
		return nil, err
	}
	if len(ClientIds) == 0 {
		return inboundJson, nil
	}

	if !s.hasUser(inboundType) {
		return inboundJson, nil
	}

	var inbound map[string]interface{}
	err = json.Unmarshal(inboundJson, &inbound)
	if err != nil {
		return nil, err
	}

	// The same clients AddInboundsToClients binds, or the core would serve a
	// @cluster client whose row never gets the inbound. Ids are bound as
	// placeholders: the list comes from the request (it used to be pasted into
	// the SQL).
	inbound["users"], err = s.fetchUsers(db, inboundType, "id IN ? AND "+bindableClientsWhere, inbound, ClientIds, clusterGroup)
	if err != nil {
		return nil, err
	}

	return json.Marshal(inbound)
}

func (s *InboundService) RestartInbounds(tx *gorm.DB, ids []uint) error {
	if !corePtr.IsRunning() {
		return nil
	}
	var inbounds []*model.Inbound
	// Not a disabled inbound either: re-adding it here (a client or TLS edit)
	// would switch it back on.
	err := tx.Model(model.Inbound{}).Preload("Tls").Where("id in ?", ids).Where("node_id IS NULL AND enable = ?", true).Find(&inbounds).Error
	if err != nil {
		return err
	}
	for _, inbound := range inbounds {
		err = corePtr.RemoveInbound(inbound.Tag)
		if err != nil && err != os.ErrInvalid {
			return err
		}
		// Close all existing connections
		corePtr.GetInstance().ConnTracker().CloseConnByInbound(inbound.Tag)

		inboundConfig, err := inbound.MarshalJSON()
		if err != nil {
			return err
		}
		inboundConfig, err = s.addUsers(tx, inboundConfig, inbound.Id, inbound.Type)
		if err != nil {
			return err
		}
		err = corePtr.AddInbound(inboundConfig)
		if err != nil {
			return err
		}
	}
	return nil
}
