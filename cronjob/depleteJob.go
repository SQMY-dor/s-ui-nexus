package cronjob

import (
	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"
)

type DepleteJob struct {
	service.ClientService
	service.InboundService
	service.NodeSyncService
}

func NewDepleteJob() *DepleteJob {
	return new(DepleteJob)
}

func (s *DepleteJob) Run() {
	inboundIds, changed, err := s.ClientService.DepleteClientsWithChange()
	if err != nil {
		logger.Warning("Disable depleted users failed: ", err)
		return
	}
	if changed {
		// A disable, re-enable, periodic reset or delay-start expiry change
		// must reach the cluster nodes too, not wait for the hourly safety net.
		// RestartInbounds below only touches local inbounds (node_id IS NULL).
		s.NodeSyncService.MarkAllDirty()
		go s.NodeSyncService.ReconcileDirtyOnline()
	}
	if len(inboundIds) > 0 {
		err := s.InboundService.RestartInbounds(database.GetDB(), inboundIds)
		if err != nil {
			logger.Error("unable to restart inbounds: ", err)
		}
	}
}
