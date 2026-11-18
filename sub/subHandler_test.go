package sub

import (
	"errors"
	"fmt"
	"net/http/httptest"
	"testing"

	projectLogger "github.com/felixbennettio/s-ui-next/logger"
	"github.com/gin-gonic/gin"
	"github.com/op/go-logging"
	"gorm.io/gorm"
)

func TestAddSubscriptionHeadersHonorsShowInfo(t *testing.T) {
	gin.SetMode(gin.TestMode)
	headers := []string{"upload=1; download=2; total=3; expire=4", "12", "client"}

	hiddenRecorder := httptest.NewRecorder()
	hiddenContext, _ := gin.CreateTestContext(hiddenRecorder)
	addSubscriptionHeaders(hiddenContext, headers, false)
	if value := hiddenRecorder.Header().Get("Subscription-Userinfo"); value != "" {
		t.Fatalf("Subscription-Userinfo must be hidden, got %q", value)
	}
	if value := hiddenRecorder.Header().Get("Profile-Update-Interval"); value != "12" {
		t.Fatalf("Profile-Update-Interval = %q, want 12", value)
	}

	visibleRecorder := httptest.NewRecorder()
	visibleContext, _ := gin.CreateTestContext(visibleRecorder)
	addSubscriptionHeaders(visibleContext, headers, true)
	if value := visibleRecorder.Header().Get("Subscription-Userinfo"); value != headers[0] {
		t.Fatalf("Subscription-Userinfo = %q, want %q", value, headers[0])
	}
}

func TestAddSubscriptionHeadersAcceptsPartialValues(t *testing.T) {
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	addSubscriptionHeaders(context, nil, true)
}

func TestWriteSubErrorUnknownIsNotFound(t *testing.T) {
	projectLogger.InitLogger(logging.ERROR)
	gin.SetMode(gin.TestMode)
	recorder := httptest.NewRecorder()
	context, _ := gin.CreateTestContext(recorder)
	writeSubError(context, fmt.Errorf("lookup: %w", gorm.ErrRecordNotFound))
	if recorder.Code != 404 || recorder.Body.String() != "subscription not found" {
		t.Fatalf("got %d %q", recorder.Code, recorder.Body.String())
	}
	recorder = httptest.NewRecorder()
	context, _ = gin.CreateTestContext(recorder)
	writeSubError(context, errors.New("boom"))
	if recorder.Code != 400 {
		t.Fatalf("got %d, want 400", recorder.Code)
	}
}
