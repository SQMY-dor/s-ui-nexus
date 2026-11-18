package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/gin-contrib/sessions"
	"github.com/gin-contrib/sessions/cookie"
	"github.com/gin-gonic/gin"
)

func newCheckLoginRouter(t *testing.T) *gin.Engine {
	t.Helper()
	if err := database.InitDB(filepath.Join(t.TempDir(), "panel.db")); err != nil {
		t.Fatal(err)
	}
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(sessions.Sessions("s-ui-next", cookie.NewStore([]byte("test-secret-test-secret-1234567"))))
	protected := func(c *gin.Context) { c.String(http.StatusOK, "ok") }
	router.GET("/app/api/inbounds", checkLogin, protected)
	router.GET("/app/apiv2/load", checkLogin, protected)
	router.GET("/app/apiv3/bootstrap", checkLogin, protected)
	router.GET("/app/other", checkLogin, protected)
	return router
}

func TestCheckLoginAPIReturns401JSON(t *testing.T) {
	router := newCheckLoginRouter(t)
	for _, path := range []string{"/app/api/inbounds", "/app/apiv2/load", "/app/apiv3/bootstrap"} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
		if w.Code != http.StatusUnauthorized {
			t.Fatalf("%s: status = %d, want 401 (%s)", path, w.Code, w.Body.String())
		}
		var msg Msg
		if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if msg.Success || msg.Msg != "Invalid login" {
			t.Fatalf("%s: unexpected body %+v", path, msg)
		}
	}
}

func TestCheckLoginXHRReturns401JSON(t *testing.T) {
	router := newCheckLoginRouter(t)
	for _, path := range []string{"/app/api/inbounds", "/app/apiv2/load", "/app/apiv3/bootstrap"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("X-Requested-With", "XMLHttpRequest")
		w := httptest.NewRecorder()
		router.ServeHTTP(w, req)
		if w.Code != http.StatusUnauthorized {
			t.Fatalf("%s: status = %d, want 401 (%s)", path, w.Code, w.Body.String())
		}
		var msg Msg
		if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if msg.Success || msg.Msg != "Invalid login" {
			t.Fatalf("%s: unexpected body %+v", path, msg)
		}
	}
}

func TestCheckLoginXHRNonAPIRedirects(t *testing.T) {
	router := newCheckLoginRouter(t)
	req := httptest.NewRequest(http.MethodGet, "/app/other", nil)
	req.Header.Set("X-Requested-With", "XMLHttpRequest")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusTemporaryRedirect || w.Header().Get("Location") != "/app/login" {
		t.Fatalf("status = %d Location = %q, want 307 /app/login", w.Code, w.Header().Get("Location"))
	}
}

func TestCheckLoginRedirectKeepsBasePath(t *testing.T) {
	router := newCheckLoginRouter(t)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/app/other", nil))
	if w.Code != http.StatusTemporaryRedirect {
		t.Fatalf("status = %d, want 307", w.Code)
	}
	if got := w.Header().Get("Location"); got != "/app/login" {
		t.Fatalf("Location = %q, want /app/login", got)
	}
}

func TestFormatErrorMsgHasNoLeadingColon(t *testing.T) {
	got := formatErrorMsg("", errors.New("wrong user or password (IP: 127.0.0.1)\n"))
	if got != "wrong user or password (IP: 127.0.0.1)" {
		t.Fatalf("got %q", got)
	}
	if got := formatErrorMsg("save", errors.New("bad")); got != "save: bad" {
		t.Fatalf("got %q", got)
	}
}
