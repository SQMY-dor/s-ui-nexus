package sub

import (
	"errors"
	"net/http"

	"github.com/felixbennettio/s-ui-next/logger"
	"github.com/felixbennettio/s-ui-next/service"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

type SubHandler struct {
	service.SettingService
	SubService
	JsonService
	ClashService
}

func NewSubHandler(g *gin.RouterGroup) {
	a := &SubHandler{}
	a.initRouter(g)
}

func (s *SubHandler) initRouter(g *gin.RouterGroup) {
	g.GET("/:subid", s.subs)
	g.HEAD("/:subid", s.subHeaders)
}

// writeSubError answers an unknown or disabled subscription id with 404 and
// a recognisable reason, and anything else with 400.
func writeSubError(c *gin.Context, err error) {
	if err != nil && errors.Is(err, gorm.ErrRecordNotFound) {
		c.String(http.StatusNotFound, "subscription not found")
		return
	}
	if err != nil {
		logger.Error(err)
	}
	c.String(http.StatusBadRequest, "Error!")
}

func (s *SubHandler) subs(c *gin.Context) {
	var headers []string
	var result *string
	var err error
	subId := c.Param("subid")
	format, isFormat := c.GetQuery("format")
	if isFormat {
		switch format {
		case "json":
			result, headers, err = s.JsonService.GetJson(subId, format)
		case "clash":
			result, headers, err = s.ClashService.GetClash(subId)
		default:
			c.String(http.StatusBadRequest, "unsupported format")
			return
		}
	} else {
		result, headers, err = s.SubService.GetSubs(subId)
	}
	if err != nil || result == nil {
		writeSubError(c, err)
		return
	}

	s.addHeaders(c, headers)

	c.String(http.StatusOK, *result)
}

func (s *SubHandler) subHeaders(c *gin.Context) {
	subId := c.Param("subid")
	client, err := s.SubService.getClientBySubId(subId)
	if err != nil {
		writeSubError(c, err)
		return
	}

	headers := s.SubService.getClientHeaders(client)
	s.addHeaders(c, headers)

	c.Status(http.StatusOK)
}

func (s *SubHandler) addHeaders(c *gin.Context, headers []string) {
	showInfo, err := s.SettingService.GetSubShowInfo()
	if err != nil {
		logger.Warning("read subShowInfo failed: ", err)
		showInfo = false
	}
	addSubscriptionHeaders(c, headers, showInfo)
}

func addSubscriptionHeaders(c *gin.Context, headers []string, showInfo bool) {
	if len(headers) > 0 && showInfo && headers[0] != "" {
		c.Writer.Header().Set("Subscription-Userinfo", headers[0])
	}
	if len(headers) > 1 {
		c.Writer.Header().Set("Profile-Update-Interval", headers[1])
	}
	if len(headers) > 2 {
		c.Writer.Header().Set("Profile-Title", headers[2])
	}
}
