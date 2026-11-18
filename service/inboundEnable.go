package service

import (
	"encoding/json"

	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/util/common"

	"gorm.io/gorm"
)

// Inbound enable switch: detour consistency.
//
// Ported in spirit from shenaba/2s-ui (GPL-3.0) checkListenDetour, adapted for
// s-ui-nexus (which has no client-detour riders). A sing-box inbound may hand
// its accepted connections to another inbound by tag (listen option "detour",
// e.g. a ShadowTLS v3 front for a Shadowsocks inbound). The core resolves that
// per connection, so a detour to an inbound that is not running fails silently
// on every connection instead of at start. The switch therefore refuses:
//   - switching off an inbound that an enabled local inbound detours to, and
//   - switching on (or saving enabled) an inbound whose own detour target is a
//     local inbound that is switched off.

// inboundDetour reads the listen "detour" of a stored or decoded inbound.
func inboundDetour(options json.RawMessage) string {
	if len(options) == 0 {
		return ""
	}
	var opts struct {
		Detour string `json:"detour"`
	}
	if json.Unmarshal(options, &opts) != nil {
		return ""
	}
	return opts.Detour
}

// checkInboundDetour applies the rules above to an inbound about to be saved.
// oldTag is its stored tag on an edit ("" for a new one): riders still name
// the old tag until they are edited themselves.
func checkInboundDetour(tx *gorm.DB, inbound *model.Inbound, oldTag string) error {
	if inbound.NodeId != nil {
		return nil
	}
	var others []model.Inbound
	err := tx.Model(model.Inbound{}).Select("id", "tag", "enable", "options").
		Where("node_id IS NULL AND id <> ?", inbound.Id).Find(&others).Error
	if err != nil {
		return err
	}
	if !inbound.Enable {
		for _, o := range others {
			if !o.Enable {
				continue
			}
			if d := inboundDetour(o.Options); d != "" && (d == inbound.Tag || (oldTag != "" && d == oldTag)) {
				return common.NewErrorf("inbound %s hands its connections to %s (detour): switch it off or change its detour first", o.Tag, inbound.Tag)
			}
		}
		return nil
	}
	target := inboundDetour(inbound.Options)
	if target == "" {
		return nil
	}
	for _, o := range others {
		if o.Tag == target && !o.Enable {
			return common.NewErrorf("inbound %s detours to %s, which is switched off: switch that on first", inbound.Tag, target)
		}
	}
	return nil
}
