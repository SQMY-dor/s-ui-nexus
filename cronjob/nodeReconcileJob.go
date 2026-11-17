// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package cronjob

import (
	"sync"

	"github.com/felixbennettio/s-ui-next/service"
)

// NodeReconcileJob is the hourly drift safety net: it reconciles every online
// node whether or not it is flagged dirty, catching changes made directly on a
// node that the master would otherwise never learn about.
type NodeReconcileJob struct {
	service.NodeSyncService
	running sync.Mutex
}

func NewNodeReconcileJob() *NodeReconcileJob {
	return &NodeReconcileJob{}
}

func (s *NodeReconcileJob) Run() {
	if !s.running.TryLock() {
		return
	}
	defer s.running.Unlock()
	s.NodeSyncService.ReconcileAllOnline()
}
