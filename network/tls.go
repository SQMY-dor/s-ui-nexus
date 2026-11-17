// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package network

import (
	"crypto/tls"
	"errors"
	"os"
	"sync"
	"time"
)

// Hot-reloading certificates for the panel ("web") and subscription ("sub")
// servers.
//
// 2s-ui's certReloader re-stats the two files on every handshake and reloads
// when the mtime moved, so a renewal that overwrites /root/cert/<domain>/ in
// place takes effect without a restart. s-ui-nexus keeps that and adds a small
// registry of named slots on top, so that:
//
//   - applying another certificate to a running server (new paths) swaps it in
//     place: UpdateCertPaths("web", cert, key);
//   - a renewal job can force a re-read regardless of mtime: ReloadCerts().
//
// A failed reload never drops the served certificate: the previous pair keeps
// being served and the error is reported to the caller (or ignored on the
// handshake path).

// certReloader serves one certificate/key pair and reloads it when the files
// change on disk.
type certReloader struct {
	mu       sync.RWMutex
	certFile string
	keyFile  string
	cert     *tls.Certificate
	certStat fileStamp
	keyStat  fileStamp
}

// fileStamp is what "the file changed" is judged by. Size is part of it
// because mtime has coarse granularity on some filesystems, and inequality
// (not After) is used so restoring an older file also counts as a change.
type fileStamp struct {
	mod  time.Time
	size int64
}

func stampOf(path string) (fileStamp, error) {
	info, err := os.Stat(path)
	if err != nil {
		return fileStamp{}, err
	}
	return fileStamp{mod: info.ModTime(), size: info.Size()}, nil
}

func newCertReloader(certFile, keyFile string) (*certReloader, error) {
	r := &certReloader{}
	if err := r.load(certFile, keyFile); err != nil {
		return nil, err
	}
	return r, nil
}

// load reads the pair and, on success only, makes it the served one.
func (r *certReloader) load(certFile, keyFile string) error {
	cs, errC := stampOf(certFile)
	ks, errK := stampOf(keyFile)
	cert, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return err
	}
	r.mu.Lock()
	r.certFile, r.keyFile = certFile, keyFile
	r.cert = &cert
	if errC == nil {
		r.certStat = cs
	}
	if errK == nil {
		r.keyStat = ks
	}
	r.mu.Unlock()
	return nil
}

func (r *certReloader) paths() (string, string) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.certFile, r.keyFile
}

func (r *certReloader) changed() bool {
	certFile, keyFile := r.paths()
	cs, errC := stampOf(certFile)
	ks, errK := stampOf(keyFile)
	if errC != nil || errK != nil {
		return false
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	return cs != r.certStat || ks != r.keyStat
}

func (r *certReloader) reload() error {
	certFile, keyFile := r.paths()
	return r.load(certFile, keyFile)
}

func (r *certReloader) getCertificate() *tls.Certificate {
	if r.changed() {
		// On failure keep serving the previous certificate. A half-written
		// pair (cert replaced, key not yet) fails here and is retried on the
		// next handshake, once both files are in place.
		_ = r.reload()
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.cert
}

var (
	slotsMu sync.Mutex
	slots   = map[string]*certReloader{}
)

// ErrNoSlot is returned by UpdateCertPaths when no running server serves TLS
// under that name, i.e. the server is in plaintext mode and switching it to
// HTTPS needs a restart (the listener itself changes).
var ErrNoSlot = errors.New("tls: server is not serving TLS; restart required")

// NewTLSConfig builds a tls.Config that serves certFile/keyFile and reloads
// them when they change. name ("web" / "sub") registers the reloader so it can
// be swapped or force-reloaded later; an empty name registers nothing.
//
// Unlike 2s-ui it does not reject handshakes whose SNI differs from the
// configured domain: s-ui-next never did, and the Host check is
// middleware.DomainValidator's job (see docs/https.md).
func NewTLSConfig(name, certFile, keyFile string) (*tls.Config, error) {
	reloader, err := newCertReloader(certFile, keyFile)
	if err != nil {
		return nil, err
	}
	if name != "" {
		slotsMu.Lock()
		slots[name] = reloader
		slotsMu.Unlock()
	}
	return &tls.Config{
		MinVersion: tls.VersionTLS12,
		GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
			return reloader.getCertificate(), nil
		},
	}, nil
}

// ReleaseTLS forgets the named slot (server stopped or restarted in plaintext).
func ReleaseTLS(name string) {
	slotsMu.Lock()
	delete(slots, name)
	slotsMu.Unlock()
}

// HasTLS reports whether a running server serves TLS under name.
func HasTLS(name string) bool {
	slotsMu.Lock()
	defer slotsMu.Unlock()
	return slots[name] != nil
}

// UpdateCertPaths points the named running server at another pair. The new
// pair is loaded and validated first; on error the old one keeps serving.
func UpdateCertPaths(name, certFile, keyFile string) error {
	slotsMu.Lock()
	r := slots[name]
	slotsMu.Unlock()
	if r == nil {
		return ErrNoSlot
	}
	return r.load(certFile, keyFile)
}

// ReloadCerts force-reloads every registered slot, e.g. after a renewal.
// Errors are collected per slot; failing slots keep their previous pair.
func ReloadCerts() map[string]error {
	slotsMu.Lock()
	snapshot := make(map[string]*certReloader, len(slots))
	for k, v := range slots {
		snapshot[k] = v
	}
	slotsMu.Unlock()
	errs := map[string]error{}
	for name, r := range snapshot {
		if err := r.reload(); err != nil {
			errs[name] = err
		}
	}
	return errs
}
