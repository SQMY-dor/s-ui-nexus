package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"strconv"
	"strings"

	"github.com/felixbennettio/s-ui-next/database/model"
	"github.com/felixbennettio/s-ui-next/util/common"
	"gorm.io/gorm"
)

// ErrResourceNotFound is returned by ResolveDeleteTarget when the given
// target does not match any row.
var ErrResourceNotFound = errors.New("resource not found")

type deleteTargetSpec struct {
	model interface{}
	// nameColumn is the human key: "tag" for core objects, "name" otherwise.
	nameColumn string
	// byTag reports the canonical del payload is the tag string (the services'
	// Save("del") contract); otherwise it is the numeric id.
	byTag bool
}

var deleteTargets = map[string]deleteTargetSpec{
	"inbounds":  {model: &model.Inbound{}, nameColumn: "tag", byTag: true},
	"outbounds": {model: &model.Outbound{}, nameColumn: "tag", byTag: true},
	"services":  {model: &model.Service{}, nameColumn: "tag", byTag: true},
	"endpoints": {model: &model.Endpoint{}, nameColumn: "tag", byTag: true},
	"clients":   {model: &model.Client{}, nameColumn: "name"},
	"tls":       {model: &model.Tls{}, nameColumn: "name"},
	"nodes":     {model: &model.Node{}, nameColumn: "name"},
}

type deleteTargetRow struct {
	Id   uint
	Name string
}

// ResolveDeleteTarget turns an API delete target -- a tag/name string, a
// numeric id (JSON number or numeric string), or an object {id}, {tag} or
// {name} -- into the payload the resource's Save("del") expects (the tag
// string for inbounds/outbounds/services/endpoints, the numeric id for
// clients/tls/nodes). It returns ErrResourceNotFound when nothing matches,
// so a caller can answer 404 instead of reporting a no-op delete as success.
// Resources it does not know are passed through unchanged.
func ResolveDeleteTarget(db *gorm.DB, resource string, data json.RawMessage) (json.RawMessage, error) {
	spec, ok := deleteTargets[resource]
	if !ok {
		return data, nil
	}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 || bytes.Equal(trimmed, []byte("null")) {
		return nil, common.NewError("validation: data must name the resource to delete (id or " + spec.nameColumn + ")")
	}

	var (
		row *deleteTargetRow
		err error
	)
	switch trimmed[0] {
	case '"':
		var value string
		if err = json.Unmarshal(trimmed, &value); err != nil {
			return nil, err
		}
		value = strings.TrimSpace(value)
		// A tag/name wins over an id so a resource literally tagged "1"
		// stays reachable; then a numeric string is tried as an id.
		row, err = findDeleteTarget(db, spec, spec.nameColumn, value)
		if err == nil && row == nil {
			if id, convErr := strconv.ParseUint(value, 10, 64); convErr == nil {
				row, err = findDeleteTarget(db, spec, "id", id)
			}
		}
	case '{':
		var obj map[string]json.RawMessage
		if err = json.Unmarshal(trimmed, &obj); err != nil {
			return nil, err
		}
		if raw, has := obj["id"]; has {
			id, convErr := parseDeleteId(raw)
			if convErr != nil {
				return nil, convErr
			}
			row, err = findDeleteTarget(db, spec, "id", id)
		} else {
			var value string
			found := false
			for _, key := range []string{spec.nameColumn, "tag", "name"} {
				if raw, has := obj[key]; has {
					if err = json.Unmarshal(raw, &value); err != nil {
						return nil, common.NewErrorf("validation: %s must be a string", key)
					}
					found = true
					break
				}
			}
			if !found {
				return nil, common.NewError("validation: data object needs id or " + spec.nameColumn)
			}
			row, err = findDeleteTarget(db, spec, spec.nameColumn, strings.TrimSpace(value))
		}
	default:
		id, convErr := parseDeleteId(trimmed)
		if convErr != nil {
			return nil, common.NewError("validation: data must be an id, a " + spec.nameColumn + " string, or an object {id} / {" + spec.nameColumn + "}")
		}
		row, err = findDeleteTarget(db, spec, "id", id)
		if err == nil && row == nil && spec.byTag {
			row, err = findDeleteTarget(db, spec, spec.nameColumn, strconv.FormatUint(id, 10))
		}
	}
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrResourceNotFound
	}
	if spec.byTag {
		return json.Marshal(row.Name)
	}
	return json.Marshal(row.Id)
}

func parseDeleteId(raw json.RawMessage) (uint64, error) {
	var number json.Number
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value interface{}
	if err := decoder.Decode(&value); err != nil {
		return 0, common.NewError("validation: id must be a positive integer")
	}
	switch v := value.(type) {
	case json.Number:
		number = v
	case string:
		number = json.Number(strings.TrimSpace(v))
	default:
		return 0, common.NewError("validation: id must be a positive integer")
	}
	id, err := strconv.ParseUint(number.String(), 10, 64)
	if err != nil || id == 0 {
		return 0, common.NewError("validation: id must be a positive integer")
	}
	return id, nil
}

func findDeleteTarget(db *gorm.DB, spec deleteTargetSpec, column string, value interface{}) (*deleteTargetRow, error) {
	if s, isString := value.(string); isString && s == "" {
		return nil, nil
	}
	var rows []deleteTargetRow
	err := db.Model(spec.model).
		Select("id", spec.nameColumn+" AS name").
		Where(column+" = ?", value).
		Limit(2).
		Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	switch len(rows) {
	case 0:
		return nil, nil
	case 1:
		return &rows[0], nil
	default:
		return nil, common.NewErrorf("%s %q matches more than one resource; delete by id", column, value)
	}
}
