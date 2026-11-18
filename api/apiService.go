package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"
	"github.com/felixbennettio/s-ui-next/util"
	"github.com/felixbennettio/s-ui-next/util/common"

	"github.com/gin-gonic/gin"
)

const maxDatabaseImportRequestBytes int64 = 128 << 20

type ApiService struct {
	service.SettingService
	service.UserService
	service.ConfigService
	service.ClientService
	service.TlsService
	service.InboundService
	service.OutboundService
	service.EndpointService
	service.ServicesService
	service.PanelService
	service.StatsService
	service.ServerService
	service.AuthService
	service.NodeService
	service.NodeSyncService
}

func (a *ApiService) LoadData(c *gin.Context) {
	data, err := a.getData(c)
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	jsonObj(c, data, nil)
}

func (a *ApiService) getData(c *gin.Context) (interface{}, error) {
	data := make(map[string]interface{}, 0)
	// Keep the cursor on server time and one second behind so updates written
	// during this request are observed by the next poll.
	data["lastUpdate"] = time.Now().Add(-time.Second).Unix()
	lu := c.Query("lu")
	isUpdated, err := a.ConfigService.CheckChanges(lu)
	if err != nil {
		return "", err
	}
	onlines, err := a.StatsService.GetOnlines()
	// Cluster heartbeat snapshot changes every 5s without bumping lastUpdate,
	// so it rides on every poll (it is tiny: one entry per node).
	data["nodesStatus"] = a.NodeService.GetStatuses()

	sysInfo := a.ServerService.GetSingboxInfo()
	data["coreApplying"] = a.ConfigService.CoreApplying()
	if sysInfo["running"] == false && data["coreApplying"] == false {
		logs := a.ServerService.GetLogs("1", "error")
		if len(logs) > 0 {
			data["lastLog"] = logs[0]
		}
	}

	if err != nil {
		return "", err
	}
	if isUpdated {
		config, err := a.SettingService.GetConfig()
		if err != nil {
			return "", err
		}
		clients, err := a.ClientService.GetAll()
		if err != nil {
			return "", err
		}
		tlsConfigs, err := a.TlsService.GetAll()
		if err != nil {
			return "", err
		}
		inbounds, err := a.InboundService.GetAll()
		if err != nil {
			return "", err
		}
		outbounds, err := a.OutboundService.GetAll()
		if err != nil {
			return "", err
		}
		endpoints, err := a.EndpointService.GetAll()
		if err != nil {
			return "", err
		}
		services, err := a.ServicesService.GetAll()
		if err != nil {
			return "", err
		}
		subURI, err := a.SettingService.GetFinalSubURI(getHostname(c))
		if err != nil {
			return "", err
		}
		trafficAge, err := a.SettingService.GetTrafficAge()
		if err != nil {
			return "", err
		}
		data["config"] = json.RawMessage(config)
		data["clients"] = clients
		data["tls"] = tlsConfigs
		data["inbounds"] = inbounds
		data["outbounds"] = outbounds
		data["endpoints"] = endpoints
		data["services"] = services
		data["subURI"] = subURI
		data["enableTraffic"] = trafficAge > 0
		data["onlines"] = onlines
		nodes, err := a.NodeService.GetAllWithStatus()
		if err != nil {
			return "", err
		}
		data["nodes"] = nodes
	} else {
		data["onlines"] = onlines
	}

	return data, nil
}

func (a *ApiService) LoadPartialData(c *gin.Context, objs []string) error {
	data, err := a.partialData(c, objs)
	if err != nil {
		return err
	}
	jsonObj(c, data, nil)
	return nil
}

func (a *ApiService) partialData(c *gin.Context, objs []string) (map[string]interface{}, error) {
	data := make(map[string]interface{}, 0)
	id := c.Query("id")

	for _, obj := range objs {
		switch obj {
		case "inbounds":
			inbounds, err := a.InboundService.Get(id)
			if err != nil {
				return nil, err
			}
			data[obj] = inbounds
		case "outbounds":
			outbounds, err := a.OutboundService.GetAll()
			if err != nil {
				return nil, err
			}
			data[obj] = outbounds
		case "endpoints":
			endpoints, err := a.EndpointService.GetAll()
			if err != nil {
				return nil, err
			}
			data[obj] = endpoints
		case "services":
			services, err := a.ServicesService.GetAll()
			if err != nil {
				return nil, err
			}
			data[obj] = services
		case "tls":
			tlsConfigs, err := a.TlsService.GetAll()
			if err != nil {
				return nil, err
			}
			data[obj] = tlsConfigs
		case "clients":
			// full=1 is the cluster reconcile read: a master needs config to
			// diff against its own copy (service/nodeSync.go).
			var clients *[]model.Client
			var err error
			if id == "" && c.Query("full") == "1" {
				clients, err = a.ClientService.GetAllWithConfig()
			} else {
				clients, err = a.ClientService.Get(id)
			}
			if err != nil {
				return nil, err
			}
			data[obj] = clients
		case "config":
			config, err := a.SettingService.GetConfig()
			if err != nil {
				return nil, err
			}
			data[obj] = json.RawMessage(config)
		case "settings":
			settings, err := a.SettingService.GetAllSetting()
			if err != nil {
				return nil, err
			}
			data[obj] = settings
		case "nodes":
			nodes, err := a.NodeService.GetAllWithStatus()
			if err != nil {
				return nil, err
			}
			data[obj] = nodes
		}
	}

	return data, nil
}

func (a *ApiService) GetUsers(c *gin.Context) {
	users, err := a.UserService.GetUsers()
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	jsonObj(c, *users, nil)
}

func (a *ApiService) GetSettings(c *gin.Context) {
	data, err := a.SettingService.GetAllSetting()
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	jsonObj(c, data, err)
}

func (a *ApiService) GetStats(c *gin.Context) {
	resource := c.Query("resource")
	tag := c.Query("tag")
	limit, err := strconv.Atoi(c.Query("limit"))
	if err != nil {
		limit = 100
	}
	data, err := a.StatsService.GetStats(resource, tag, limit)
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	jsonObj(c, data, err)
}

func (a *ApiService) GetStatus(c *gin.Context) {
	request := c.Query("r")
	result := a.ServerService.GetStatus(request)
	jsonObj(c, result, nil)
}

func (a *ApiService) GetOnlines(c *gin.Context) {
	onlines, err := a.StatsService.GetOnlines()
	jsonObj(c, onlines, err)
}

func (a *ApiService) GetLogs(c *gin.Context) {
	count := c.Query("c")
	level := c.Query("l")
	logs := a.ServerService.GetLogs(count, level)
	jsonObj(c, logs, nil)
}

func (a *ApiService) CheckChanges(c *gin.Context) {
	actor := c.Query("a")
	chngKey := c.Query("k")
	count := c.Query("c")
	changes := a.ConfigService.GetChanges(actor, chngKey, count)
	jsonObj(c, changes, nil)
}

func (a *ApiService) GetKeypairs(c *gin.Context) {
	kType := c.Query("k")
	options := c.Query("o")
	keypair := a.ServerService.GenKeypair(kType, options)
	jsonObj(c, keypair, nil)
}

func (a *ApiService) GetDb(c *gin.Context) {
	exclude := c.Query("exclude")
	db, err := database.GetDb(exclude)
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	c.Header("Content-Type", "application/octet-stream")
	c.Header("Content-Disposition", "attachment; filename=s-ui-next_"+time.Now().Format("20060102-150405")+".db")
	c.Writer.Write(db)
}

func (a *ApiService) postActions(c *gin.Context) (string, json.RawMessage, error) {
	var data map[string]json.RawMessage
	err := c.ShouldBind(&data)
	if err != nil {
		return "", nil, err
	}
	return string(data["action"]), data["data"], nil
}

func (a *ApiService) Login(c *gin.Context) {
	remoteIP := getRemoteIp(c)
	username := c.Request.FormValue("user")
	attemptKey := loginAttemptKey(c, username)
	if allowed, retryAfter := loginAllowed(attemptKey); !allowed {
		jsonMsg(c, "", common.NewErrorf("too many login attempts; try again in %s", retryAfter))
		return
	}
	user, err := a.UserService.CheckPassword(username, c.Request.FormValue("pass"), remoteIP)
	if err != nil {
		recordLoginFailure(attemptKey)
		jsonMsg(c, "", err)
		return
	}
	if user.TOTPEnabled {
		code := c.Request.FormValue("otp")
		if code == "" {
			jsonObj(c, gin.H{"requires2FA": true}, nil)
			return
		}
		if !a.UserService.VerifySecondFactor(user, code) {
			recordLoginFailure(attemptKey)
			jsonMsg(c, "", common.NewError("invalid TOTP or recovery code"))
			return
		}
	}
	loginUser := user.Username
	clearLoginFailures(attemptKey)
	a.UserService.RecordLogin(loginUser, remoteIP)

	sessionMaxAge, err := a.SettingService.GetSessionMaxAge()
	if err != nil {
		logger.Infof("Unable to get session's max age from DB")
	}

	err = SetLoginUser(c, loginUser, sessionMaxAge)
	if err == nil {
		logger.Info("user ", loginUser, " login success")
	} else {
		logger.Warning("login failed: ", err)
	}

	jsonMsg(c, "", nil)
}

func (a *ApiService) ChangePass(c *gin.Context) {
	id := c.Request.FormValue("id")
	oldPass := c.Request.FormValue("oldPass")
	newUsername := c.Request.FormValue("newUsername")
	newPass := c.Request.FormValue("newPass")
	err := a.UserService.ChangePass(id, oldPass, newUsername, newPass)
	if err == nil {
		logger.Info("change user credentials success")
		jsonMsg(c, "save", nil)
	} else {
		logger.Warning("change user credentials failed:", err)
		jsonMsg(c, "", err)
	}
}

// Save handles POST save. fanout triggers an immediate cluster node reconcile
// for client/inbound changes (v1 always; v2 only with sync=true). It affects
// latency only: the hourly ReconcileAllOnline pushes state regardless. Pushed
// @cluster clients reference the receiving panel's own inbounds, so the
// node_id scoping in expectedClients keeps them out of every outgoing push.
func (a *ApiService) Save(c *gin.Context, loginUser string, fanout bool) {
	hostname := getHostname(c)
	obj := c.Request.FormValue("object")
	act := c.Request.FormValue("action")
	data := c.Request.FormValue("data")
	initUsers := c.Request.FormValue("initUsers")
	apply := true
	if value := c.Request.FormValue("apply"); value != "" {
		apply, _ = strconv.ParseBool(value)
	}
	objs, err := a.ConfigService.SaveWithApply(obj, act, json.RawMessage(data), initUsers, loginUser, hostname, apply)
	if err != nil {
		jsonMsg(c, "save", err)
		return
	}
	if fanout && (obj == "clients" || obj == "inbounds") {
		// Fire-and-forget: the txn is committed; network IO must not block
		// the response. Unrelated nodes are a cheap no-op diff.
		a.NodeSyncService.MarkAllDirty()
		go a.NodeSyncService.ReconcileDirtyOnline()
	}
	updated, err := a.partialData(c, objs)
	if err != nil {
		// The transaction has committed. A refresh failure must never turn that
		// successful mutation into a failed save (and invite a duplicate retry).
		logger.Warning("configuration saved, but reloading panel data failed: ", err)
		c.JSON(http.StatusOK, Msg{Success: true, Obj: map[string]interface{}{}, Warning: "savedButRefreshFailed"})
		return
	}
	jsonObj(c, updated, nil)
}

func (a *ApiService) ExportWireGuard(c *gin.Context) {
	peerIndex, err := strconv.Atoi(c.Request.FormValue("peerIndex"))
	if err != nil {
		jsonObj(c, nil, common.NewError("peerIndex must be a number"))
		return
	}
	result, err := a.EndpointService.ExportWireGuardPeer(c.Request.FormValue("tag"), peerIndex)
	jsonObj(c, result, err)
}

func (a *ApiService) RestartApp(c *gin.Context) {
	err := a.PanelService.RestartPanel(3)
	jsonMsg(c, "restartApp", err)
}

func (a *ApiService) RestartSb(c *gin.Context) {
	err := a.ConfigService.RestartCore()
	jsonMsg(c, "restartSb", err)
}

func (a *ApiService) LinkConvert(c *gin.Context) {
	link := c.Request.FormValue("link")
	result, _, err := util.GetOutbound(link, 0)
	jsonObj(c, result, err)
}

func (a *ApiService) SubConvert(c *gin.Context) {
	link := c.Request.FormValue("link")
	result, err := util.GetExternalSub(link)
	jsonObj(c, result, err)
}

func (a *ApiService) ImportDb(c *gin.Context) {
	if err := limitDatabaseImport(c); err != nil {
		jsonMsg(c, "", err)
		return
	}
	file, _, err := c.Request.FormFile("db")
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	defer file.Close()
	err = database.ImportDB(file)
	jsonMsg(c, "", err)
}

func limitDatabaseImport(c *gin.Context) error {
	if c.Request.ContentLength > maxDatabaseImportRequestBytes {
		return &http.MaxBytesError{Limit: maxDatabaseImportRequestBytes}
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxDatabaseImportRequestBytes)
	return nil
}

func (a *ApiService) Logout(c *gin.Context) {
	loginUser := GetLoginUser(c)
	if loginUser != "" {
		logger.Infof("user %s logout", loginUser)
	}
	ClearSession(c)
	jsonMsg(c, "", nil)
}

func (a *ApiService) LoadTokens() ([]byte, error) {
	return a.UserService.LoadTokens()
}

func (a *ApiService) GetTokens(c *gin.Context) {
	loginUser := GetLoginUser(c)
	tokens, err := a.UserService.GetUserTokens(loginUser)
	jsonObj(c, tokens, err)
}

func (a *ApiService) AddToken(c *gin.Context) {
	loginUser := GetLoginUser(c)
	expiry := c.Request.FormValue("expiry")
	expiryInt, err := strconv.ParseInt(expiry, 10, 64)
	if err != nil {
		jsonMsg(c, "", err)
		return
	}
	desc := c.Request.FormValue("desc")
	token, err := a.UserService.AddToken(loginUser, expiryInt, desc)
	jsonObj(c, token, err)
}

func (a *ApiService) DeleteToken(c *gin.Context) {
	loginUser := GetLoginUser(c)
	tokenId := c.Request.FormValue("id")
	err := a.UserService.DeleteUserToken(loginUser, tokenId)
	jsonMsg(c, "", err)
}

func (a *ApiService) GetSingboxConfig(c *gin.Context) {
	rawConfig, err := a.ConfigService.GetConfig("")
	if err != nil {
		c.Status(400)
		c.Writer.WriteString(err.Error())
		return
	}
	c.Header("Content-Type", "application/json")
	c.Header("Content-Disposition", "attachment; filename=config_"+time.Now().Format("20060102-150405")+".json")
	c.Writer.Write(*rawConfig)
}

func (a *ApiService) GetCheckOutbound(c *gin.Context) {
	tag := c.Query("tag")
	link := c.Query("link")
	result := a.ConfigService.CheckOutbound(tag, link)
	jsonObj(c, result, nil)
}

// ---------- cluster nodes (ported from shenaba/2s-ui, GPL-3.0) ----------

// TestNode probes the (possibly unsaved) node in form field "data".
func (a *ApiService) TestNode(c *gin.Context) {
	data := c.Request.FormValue("data")
	status, err := a.NodeService.TestNode(json.RawMessage(data))
	jsonObj(c, status, err)
}

// GetNodeInbounds lists a node's inbounds (GET ?id=<nodeId>), flagging adopted tags.
func (a *ApiService) GetNodeInbounds(c *gin.Context) {
	id, err := strconv.ParseUint(c.Query("id"), 10, 64)
	if err != nil {
		jsonMsg(c, "nodeInbounds", common.NewError("invalid node id"))
		return
	}
	inbounds, err := a.NodeSyncService.FetchNodeInbounds(uint(id))
	jsonObj(c, inbounds, err)
}

// AdoptInbounds adopts form "tags" (JSON array) of node form "id" as replicas.
func (a *ApiService) AdoptInbounds(c *gin.Context, loginUser string) {
	id, err := strconv.ParseUint(c.Request.FormValue("id"), 10, 64)
	if err != nil {
		jsonMsg(c, "adoptInbounds", common.NewError("invalid node id"))
		return
	}
	var tags []string
	if err := json.Unmarshal([]byte(c.Request.FormValue("tags")), &tags); err != nil {
		jsonMsg(c, "adoptInbounds", common.NewError("invalid tags"))
		return
	}
	// Same field, same format as api/save's initUsers for a new local inbound;
	// empty means import only.
	initUsers := c.PostForm("initUsers")
	if err := a.NodeSyncService.AdoptInbounds(uint(id), tags, initUsers, loginUser); err != nil {
		jsonMsg(c, "adoptInbounds", err)
		return
	}
	// Push the master's clients onto the freshly adopted inbounds right away.
	// If it loses to an in-flight run, the dirty flag lets the heartbeat
	// converge instead.
	go func() {
		if err := a.NodeSyncService.ReconcileNow(uint(id)); err != nil {
			logger.Warning("adopt: initial reconcile failed: ", err)
		}
	}()
	jsonMsg(c, "adoptInbounds", nil)
}

// ReconcileNode runs an interactive reconcile of node form "id".
func (a *ApiService) ReconcileNode(c *gin.Context) {
	id, err := strconv.ParseUint(c.Request.FormValue("id"), 10, 64)
	if err != nil {
		jsonMsg(c, "reconcileNode", common.NewError("invalid node id"))
		return
	}
	err = a.NodeSyncService.ReconcileNow(uint(id))
	jsonMsg(c, "reconcileNode", err)
}
