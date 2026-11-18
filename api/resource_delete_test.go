package api

import (
	"net/http"
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
)

func TestResolveV3DeleteTargetNotFoundIs404(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "panel.db")); err != nil {
		t.Fatal(err)
	}
	_, status, err := resolveV3DeleteTarget("inbounds", []byte(`"1"`))
	if err == nil || status != http.StatusNotFound {
		t.Fatalf("status = %d err = %v, want 404", status, err)
	}
}
