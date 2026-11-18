package service

import (
	"path/filepath"
	"testing"

	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/database/model"
)

func TestUpdateFirstUserPasswordOnlyKeepsUsername(t *testing.T) {
	if err := database.InitDB(filepath.Join(t.TempDir(), "s-ui-next.db")); err != nil {
		t.Fatal(err)
	}
	db := database.GetDB()
	db.Where("1 = 1").Delete(&model.User{})
	svc := UserService{}
	if err := svc.UpdateFirstUser("", "x"); err == nil {
		t.Fatal("creating the first user without a username should fail")
	}
	if err := svc.UpdateFirstUser("root", "first-pass"); err != nil {
		t.Fatal(err)
	}
	if err := svc.UpdateFirstUser("", "second-pass"); err != nil {
		t.Fatalf("password-only update failed: %v", err)
	}
	user, err := svc.GetFirstUser()
	if err != nil {
		t.Fatal(err)
	}
	if user.Username != "root" {
		t.Fatalf("username changed to %q", user.Username)
	}
	if !verifyPassword(user.Password, "second-pass") {
		t.Fatal("new password does not verify")
	}
	if err := svc.UpdateFirstUser("admin2", ""); err != nil {
		t.Fatalf("username-only update failed: %v", err)
	}
	user, _ = svc.GetFirstUser()
	if user.Username != "admin2" || !verifyPassword(user.Password, "second-pass") {
		t.Fatal("username-only update should keep the password")
	}
	if err := svc.UpdateFirstUser("", ""); err == nil {
		t.Fatal("empty update should fail")
	}
}
