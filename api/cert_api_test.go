// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	projectLogger "github.com/felixbennettio/s-ui-next/logger"

	"github.com/gin-contrib/sessions"
	"github.com/gin-contrib/sessions/cookie"
	"github.com/gin-gonic/gin"
	"github.com/op/go-logging"
)

func certTestRouter(t *testing.T) *gin.Engine {
	t.Helper()
	projectLogger.InitLogger(logging.ERROR)
	t.Setenv("HOME", t.TempDir())
	t.Setenv("SUI_ACME_HOME", t.TempDir())
	t.Setenv("SUI_CERT_DIR", t.TempDir())
	t.Setenv("SUI_NGINX_CONF_DIR", t.TempDir())
	if err := database.InitDB(filepath.Join(t.TempDir(), "panel.db")); err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	e := gin.New()
	e.Use(sessions.Sessions("s-ui-next", cookie.NewStore([]byte("0123456789abcdef0123456789abcdef"))))
	NewAPIHandler(e.Group("/api"), nil)
	return e
}

func decodeMsg(t *testing.T, body []byte) Msg {
	t.Helper()
	var m Msg
	if err := json.Unmarshal(body, &m); err != nil {
		t.Fatalf("bad json %q: %v", body, err)
	}
	return m
}

func TestCertEndpointsRequireLogin(t *testing.T) {
	e := certTestRouter(t)
	for _, tc := range []struct{ method, path string }{
		{"GET", "/api/certs"}, {"GET", "/api/acmeStatus"}, {"GET", "/api/detectNginx"},
		{"POST", "/api/issueCert"}, {"POST", "/api/applyCert"}, {"POST", "/api/deleteCert"},
		{"POST", "/api/syncNginxProxy"}, {"POST", "/api/renewCert"}, {"POST", "/api/installAcme"},
	} {
		w := httptest.NewRecorder()
		req := httptest.NewRequest(tc.method, tc.path, nil)
		req.Header.Set("X-Requested-With", "XMLHttpRequest")
		e.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("%s %s without session: status %d, want 401", tc.method, tc.path, w.Code)
		}
		m := decodeMsg(t, w.Body.Bytes())
		if m.Success || m.Msg != "Invalid login" {
			t.Errorf("%s %s without session: %+v", tc.method, tc.path, m)
		}
	}
}

func TestCertHandlersDispatch(t *testing.T) {
	certTestRouter(t)
	run := func(post bool, action string, form url.Values) Msg {
		w := httptest.NewRecorder()
		c, _ := gin.CreateTestContext(w)
		method := "GET"
		if post {
			method = "POST"
		}
		c.Request = httptest.NewRequest(method, "/api/"+action, strings.NewReader(form.Encode()))
		c.Request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		var handled bool
		if post {
			handled = handleCertPost(c, action)
		} else {
			handled = handleCertGet(c, action)
		}
		if !handled {
			t.Fatalf("%s not handled", action)
		}
		return decodeMsg(t, w.Body.Bytes())
	}
	if m := run(false, "certs", nil); !m.Success {
		t.Fatalf("certs: %+v", m)
	}
	if m := run(false, "acmeStatus", nil); !m.Success || m.Obj == nil {
		t.Fatalf("acmeStatus: %+v", m)
	}
	// Injection attempts are refused with a message, never executed.
	if m := run(true, "issueCert", url.Values{"domain": {"a.com;id"}, "method": {"auto"}}); m.Success {
		t.Fatalf("issueCert accepted a bad domain: %+v", m)
	}
	if m := run(true, "applyCert", url.Values{"domain": {"nothere.example.com"}, "targets": {"web"}}); m.Success {
		t.Fatalf("applyCert for unknown cert succeeded: %+v", m)
	}
	if m := run(true, "deleteCert", url.Values{}); m.Success {
		t.Fatalf("deleteCert without domain succeeded: %+v", m)
	}
	if m := run(true, "syncNginxProxy", url.Values{"webNginx": {"true"}, "webDomainSet": {"true"}, "webDomain": {""}}); m.Success {
		t.Fatalf("syncNginxProxy with an enabled side and no domain succeeded: %+v", m)
	}
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = httptest.NewRequest("GET", "/api/load", nil)
	if handleCertGet(c, "load") || handleCertPost(c, "save") {
		t.Fatal("cert dispatcher swallowed an unrelated action")
	}
}
