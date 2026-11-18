package api

import (
	"net"
	"net/http"
	"strings"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"

	"github.com/gin-gonic/gin"
)

type Msg struct {
	Success bool        `json:"success"`
	Msg     string      `json:"msg"`
	Obj     interface{} `json:"obj"`
	Warning string      `json:"warning,omitempty"`
}

func getRemoteIp(c *gin.Context) string {
	value := c.GetHeader("X-Forwarded-For")
	if value != "" {
		for _, candidate := range strings.Split(value, ",") {
			candidate = strings.TrimSpace(candidate)
			if net.ParseIP(candidate) != nil {
				return candidate
			}
		}
	}
	return directPeerIP(c)
}

func getHostname(c *gin.Context) string {
	host := c.Request.Host
	if strings.Contains(host, ":") {
		host, _, _ = net.SplitHostPort(c.Request.Host)
		if strings.Contains(host, ":") {
			host = "[" + host + "]"
		}
	}
	return host
}

func jsonMsg(c *gin.Context, msg string, err error) {
	jsonMsgObj(c, msg, nil, err)
}

func jsonObj(c *gin.Context, obj interface{}, err error) {
	jsonMsgObj(c, "", obj, err)
}

func jsonMsgObj(c *gin.Context, msg string, obj interface{}, err error) {
	m := Msg{
		Obj: obj,
	}
	if err == nil {
		m.Success = true
		if msg != "" {
			m.Msg = msg
		}
	} else {
		m.Success = false
		m.Msg = formatErrorMsg(msg, err)
		logger.Warning("failed :", err)
	}
	c.JSON(http.StatusOK, m)
}

// formatErrorMsg joins an optional context prefix with the error text without
// a dangling ": " when there is no prefix, and drops the trailing newline that
// common.NewError (fmt.Sprintln) leaves behind.
func formatErrorMsg(msg string, err error) string {
	text := strings.TrimSpace(err.Error())
	if msg == "" {
		return text
	}
	return msg + ": " + text
}

func pureJsonMsg(c *gin.Context, success bool, msg string) {
	if success {
		c.JSON(http.StatusOK, Msg{
			Success: true,
			Msg:     msg,
		})
	} else {
		c.JSON(http.StatusOK, Msg{
			Success: false,
			Msg:     msg,
		})
	}
}

// isAPIRequestPath reports whether path is one of the panel's JSON API
// routes (<base>api/..., <base>apiv2/..., <base>apiv3/...).
func isAPIRequestPath(path string) bool {
	for _, segment := range strings.Split(path, "/") {
		switch segment {
		case "api", "apiv2", "apiv3":
			return true
		}
	}
	return false
}

// loginRedirectPath returns the panel login page under the configured web
// path, e.g. /app/login, instead of a hard-coded /login that 404s when the
// panel is not served from the root.
func loginRedirectPath() string {
	settingService := service.SettingService{}
	basePath, err := settingService.GetWebPath()
	if err != nil || basePath == "" {
		basePath = "/"
	}
	if !strings.HasSuffix(basePath, "/") {
		basePath += "/"
	}
	return basePath + "login"
}

func checkLogin(c *gin.Context) {
	if !IsLogin(c) {
		if isAPIRequestPath(c.Request.URL.Path) {
			// Every unauthenticated API call, panel XHR included, gets 401 with
			// the JSON body the Web panel matches on; it then logs out and
			// routes to its login page.
			c.JSON(http.StatusUnauthorized, Msg{Success: false, Msg: "Invalid login"})
		} else {
			c.Redirect(http.StatusTemporaryRedirect, loginRedirectPath())
		}
		c.Abort()
	} else {
		c.Next()
	}
}
