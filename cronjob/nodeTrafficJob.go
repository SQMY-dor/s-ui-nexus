// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package cronjob

import (
	"sync"

	"github.com/felixbennettio/s-ui-next/service"
)

type NodeTrafficJob struct {
	service.NodeSyncService
	running sync.Mutex
}

func NewNodeTrafficJob() *NodeTrafficJob {
	return &NodeTrafficJob{}
}

func (s *NodeTrafficJob) Run() {
	if !s.running.TryLock() {
		return
	}
	defer s.running.Unlock()
	s.NodeSyncService.CollectTraffic()
}
