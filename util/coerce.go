package util

// Readers for values that arrive as interface{} from JSON. Inbound options,
// TLS configs and address rows reach the link and subscription builders as
// map[string]interface{} straight out of the database, so a value the schema
// calls a string or a list can be absent, null, or in either of the shapes
// sing-box accepts for a Listable field.
//
// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

// AsBool reads a value the schema says is a bool, or false.
func AsBool(v interface{}) bool {
	b, _ := v.(bool)
	return b
}

// AsStringList reads a value the schema says is a list of strings, skipping
// anything that is not one. A bare non-empty string counts as a list of one:
// sing-box's Listable[string] (alpn, ech.config, the ws Host header, ...)
// marshals a single-entry list as a scalar.
func AsStringList(v interface{}) []string {
	switch items := v.(type) {
	case string:
		if items == "" {
			return nil
		}
		return []string{items}
	case []string:
		return items
	case []interface{}:
		list := make([]string, 0, len(items))
		for _, item := range items {
			if s, ok := item.(string); ok {
				list = append(list, s)
			}
		}
		return list
	}
	return nil
}
