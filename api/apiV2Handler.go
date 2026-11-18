package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/util/common"

	"github.com/gin-gonic/gin"
)

type TokenInMemory struct {
	Token    string
	Expiry   int64
	Username string
}

type APIv2Handler struct {
	ApiService
	tokens  []TokenInMemory
	tokensM sync.RWMutex
}

func NewAPIv2Handler(g *gin.RouterGroup) *APIv2Handler {
	a := &APIv2Handler{}
	a.ReloadTokens()
	a.initRouter(g)
	return a
}

func (a *APIv2Handler) initRouter(g *gin.RouterGroup) {
	g.Use(func(c *gin.Context) {
		a.checkToken(c)
	})
	g.POST("/:postAction", a.postHandler)
	g.GET("/:getAction", a.getHandler)
}

func (a *APIv2Handler) postHandler(c *gin.Context) {
	username := a.findUsername(c)
	action := c.Param("postAction")

	switch action {
	case "save":
		// sync=true asks for the immediate cluster fanout the web UI always
		// gets; without it a change still reaches nodes via the hourly
		// safety net. Malformed values are rejected: silently degrading to
		// "no fanout" would report success while nodes stay stale for up to
		// an hour. (Ported from shenaba/2s-ui.)
		fanout := false
		if v := c.Request.FormValue("sync"); v != "" {
			parsed, err := strconv.ParseBool(v)
			if err != nil {
				jsonMsg(c, "sync", common.NewErrorf("invalid sync value %q: %v", v, err))
				return
			}
			fanout = parsed
		}
		a.ApiService.Save(c, username, fanout)
	case "wireguardExport":
		a.ApiService.ExportWireGuard(c)
	case "restartApp":
		a.ApiService.RestartApp(c)
	case "restartSb":
		a.ApiService.RestartSb(c)
	case "linkConvert":
		a.ApiService.LinkConvert(c)
	case "subConvert":
		a.ApiService.SubConvert(c)
	case "importdb":
		a.ApiService.ImportDb(c)
	default:
		jsonMsg(c, "failed", common.NewError("unknown action: ", action))
	}
}

func (a *APIv2Handler) getHandler(c *gin.Context) {
	action := c.Param("getAction")

	switch action {
	case "load":
		a.ApiService.LoadData(c)
	case "inbounds", "outbounds", "endpoints", "services", "tls", "clients", "config", "nodes":
		err := a.ApiService.LoadPartialData(c, []string{action})
		if err != nil {
			jsonMsg(c, action, err)
		}
		return
	case "users":
		a.ApiService.GetUsers(c)
	case "settings":
		a.ApiService.GetSettings(c)
	case "stats":
		a.ApiService.GetStats(c)
	case "status":
		a.ApiService.GetStatus(c)
	case "onlines":
		a.ApiService.GetOnlines(c)
	case "logs":
		a.ApiService.GetLogs(c)
	case "changes":
		a.ApiService.CheckChanges(c)
	case "keypairs":
		a.ApiService.GetKeypairs(c)
	case "getdb":
		a.ApiService.GetDb(c)
	case "checkOutbound":
		a.ApiService.GetCheckOutbound(c)
	default:
		jsonMsg(c, "failed", common.NewError("unknown action: ", action))
	}
}

func (a *APIv2Handler) findUsername(c *gin.Context) string {
	token := strings.TrimSpace(c.Request.Header.Get("Token"))
	if authorization := strings.TrimSpace(c.Request.Header.Get("Authorization")); strings.HasPrefix(strings.ToLower(authorization), "bearer ") {
		token = strings.TrimSpace(authorization[7:])
	}
	if token == "" {
		token = strings.TrimSpace(c.Request.Header.Get("X-API-Token"))
	}
	return a.findUsernameByToken(token)
}

func (a *APIv2Handler) findUsernameByToken(token string) string {
	if token == "" {
		return ""
	}
	now := time.Now().Unix()
	a.tokensM.RLock()
	defer a.tokensM.RUnlock()
	for _, t := range a.tokens {
		if t.Expiry > 0 && t.Expiry < now {
			continue
		}
		if t.Token == token {
			return t.Username
		}
	}
	return ""
}

func (a *APIv2Handler) checkToken(c *gin.Context) {
	username := a.findUsername(c)
	if username != "" {
		c.Next()
		return
	}
	c.JSON(http.StatusUnauthorized, Msg{Success: false, Msg: "invalid token"})
	c.Abort()
}

func (a *APIv2Handler) ReloadTokens() {
	tokens, err := a.ApiService.LoadTokens()
	if err == nil {
		var newTokens []TokenInMemory
		err = json.Unmarshal(tokens, &newTokens)
		if err != nil {
			logger.Error("unable to load tokens: ", err)
		}
		a.tokensM.Lock()
		a.tokens = newTokens
		a.tokensM.Unlock()
	} else {
		logger.Error("unable to load tokens: ", err)
	}
}
