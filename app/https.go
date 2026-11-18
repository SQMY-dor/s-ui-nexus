// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package app

import (
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"
)

// startHTTPS runs the automatic-HTTPS startup steps (2s-ui app.go):
//
//  1. archive certificate paths typed into the settings before the upgrade as
//     manual registrations, so they show up on the Domains page;
//  2. reconcile the generated nginx reverse-proxy vhosts with the settings.
//
// Both only log on failure: neither may keep the panel from starting.
func (a *APP) startHTTPS() {
	var certService service.CertService
	certService.ArchiveLegacy()
	a.syncNginxProxy()
}

// syncNginxProxy aligns the auto-generated reverse-proxy configs in nginx with
// the saved settings. It runs after the web/sub servers started, i.e. once
// they have decided whether they serve plaintext (behind nginx) or TLS, and it
// is the only place a side switched OFF gets its vhost removed (the page that
// switches it off is served through that very vhost).
func (a *APP) syncNginxProxy() {
	sides, err := a.SettingService.ProxyVhostSpecs()
	if err != nil {
		logger.Warning("读取反代设置失败,跳过启动时的 nginx 对账(nginx 保持原样):", err)
		return
	}
	specs, err := service.BuildVhostSpecs(sides...)
	if err != nil {
		logger.Warning("反代设置不完整,跳过启动时的 nginx 对账(nginx 保持原样):", err)
		return
	}
	var acme service.AcmeService
	if len(specs) == 0 && !acme.HasGeneratedVhosts() {
		// Nothing wanted and nothing of ours on disk: do not probe nginx at all.
		return
	}
	if _, err := acme.SyncVhosts(specs); err != nil {
		logger.Warning("启动时对账 nginx 反代配置失败(不影响面板运行):", err)
	}
}
