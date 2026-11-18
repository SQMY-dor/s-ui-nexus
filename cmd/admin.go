package cmd

import (
	"fmt"

	"github.com/felixbennettio/s-ui-next/config"
	"github.com/felixbennettio/s-ui-next/database"
	"github.com/felixbennettio/s-ui-next/service"
)

func resetAdmin() {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println(err)
		return
	}

	userService := service.UserService{}
	err = userService.UpdateFirstUser("admin", "admin")
	if err != nil {
		fmt.Println("reset admin credentials failed:", err)
	} else {
		fmt.Println("reset admin credentials success")
	}
}

func updateAdmin(username string, password string) {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println(err)
		return
	}

	if username != "" || password != "" {
		userService := service.UserService{}
		err := userService.UpdateFirstUser(username, password)
		if err != nil {
			fmt.Println("reset admin credentials failed:", err)
		} else {
			fmt.Println("reset admin credentials success")
		}
	}
}

func showAdmin() {
	err := database.InitDB(config.GetDBPath())
	if err != nil {
		fmt.Println(err)
		return
	}
	userService := service.UserService{}
	userModel, err := userService.GetFirstUser()
	if err != nil {
		fmt.Println("get current user info failed,error info:", err)
		return
	}
	if userModel == nil {
		return
	}
	fmt.Println("First admin credentials:")
	fmt.Println("\tUsername:\t", userModel.Username)
	if userModel.Password == "" {
		fmt.Println("\tPassword:\t (empty)")
	} else {
		fmt.Println("\tPassword:\t (stored hashed; the original password can not be shown)")
	}
	fmt.Println("To regain access, set a new password with `sui admin -password <new>`")
	fmt.Println("or reset the account to admin/admin with `sui admin -reset`.")
}
