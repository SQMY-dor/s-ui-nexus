// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package cronjob

import (
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"
)

// CertRenewJob is the daily certificate renewal check. 2s-ui relies on the
// crontab acme.sh installs for itself; s-ui-nexus additionally runs
// `acme.sh --cron` from the panel once a day, so renewal does not depend on a
// working system cron, and then hot-reloads the panel/subscription
// certificates (network.ReloadCerts). sing-box inbounds that read the same
// files via certificate_path reload them on their own (sing-box watches the
// files), so the core is not restarted.
type CertRenewJob struct {
	acme service.AcmeService
}

func NewCertRenewJob() *CertRenewJob {
	return &CertRenewJob{}
}

func (j *CertRenewJob) Run() {
	out, err := j.acme.RenewDue()
	if err != nil {
		logger.Warning("certificate renewal check: ", err)
		return
	}
	if out != "" {
		logger.Debug("certificate renewal check done")
	}
}
