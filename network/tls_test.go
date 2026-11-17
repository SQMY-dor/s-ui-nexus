// Ported from shenaba/2s-ui (GPL-3.0), adapted for s-ui-nexus.

package network

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// writeSelfSigned writes a self-signed pair for cn into dir/<name>.{crt,key}.
func writeSelfSigned(t *testing.T, certPath, keyPath, cn string, serial int64) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(serial),
		Subject:      pkix.Name{CommonName: cn},
		DNSNames:     []string{cn},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	kb, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	// Write to a temp name and rename, like acme.sh's install does.
	for path, block := range map[string]*pem.Block{
		certPath: {Type: "CERTIFICATE", Bytes: der},
		keyPath:  {Type: "EC PRIVATE KEY", Bytes: kb},
	} {
		tmp := path + ".tmp"
		if err := os.WriteFile(tmp, pem.EncodeToMemory(block), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(tmp, path); err != nil {
			t.Fatal(err)
		}
	}
}

// startTLS serves cfg on 127.0.0.1 and completes handshakes until closed.
func startTLS(t *testing.T, cfg *tls.Config) string {
	t.Helper()
	ln, err := tls.Listen("tcp", "127.0.0.1:0", cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				_ = c.(*tls.Conn).Handshake()
				c.Close()
			}(conn)
		}
	}()
	return ln.Addr().String()
}

func servedSerial(t *testing.T, addr string) int64 {
	t.Helper()
	conn, err := tls.Dial("tcp", addr, &tls.Config{InsecureSkipVerify: true})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	return conn.ConnectionState().PeerCertificates[0].SerialNumber.Int64()
}

func TestTLSHotReloadOnFileChange(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "fullchain.pem"), filepath.Join(dir, "privkey.pem")
	writeSelfSigned(t, certPath, keyPath, "a.example.com", 1)

	cfg, err := NewTLSConfig("test-mtime", certPath, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ReleaseTLS("test-mtime") })
	addr := startTLS(t, cfg)
	if got := servedSerial(t, addr); got != 1 {
		t.Fatalf("serial before swap = %d, want 1", got)
	}

	// Simulate a renewal overwriting the files in place.
	writeSelfSigned(t, certPath, keyPath, "a.example.com", 2)
	// Make sure the stamp differs even on coarse-mtime filesystems.
	future := time.Now().Add(2 * time.Second)
	_ = os.Chtimes(certPath, future, future)
	_ = os.Chtimes(keyPath, future, future)
	if got := servedSerial(t, addr); got != 2 {
		t.Fatalf("serial after swap = %d, want 2 (not hot-reloaded)", got)
	}
}

func TestTLSKeepsOldCertOnBrokenReload(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "c.pem"), filepath.Join(dir, "k.pem")
	writeSelfSigned(t, certPath, keyPath, "b.example.com", 7)
	cfg, err := NewTLSConfig("", certPath, keyPath)
	if err != nil {
		t.Fatal(err)
	}
	addr := startTLS(t, cfg)
	// Half-written renewal: certificate replaced by garbage.
	if err := os.WriteFile(certPath, []byte("not a pem"), 0600); err != nil {
		t.Fatal(err)
	}
	if got := servedSerial(t, addr); got != 7 {
		t.Fatalf("serial = %d, want previous 7 kept", got)
	}
}

func TestUpdateCertPathsAndReloadCerts(t *testing.T) {
	dir := t.TempDir()
	c1, k1 := filepath.Join(dir, "1.crt"), filepath.Join(dir, "1.key")
	c2, k2 := filepath.Join(dir, "2.crt"), filepath.Join(dir, "2.key")
	writeSelfSigned(t, c1, k1, "c.example.com", 10)
	writeSelfSigned(t, c2, k2, "d.example.com", 20)

	if err := UpdateCertPaths("test-slot", c2, k2); err != ErrNoSlot {
		t.Fatalf("UpdateCertPaths without slot = %v, want ErrNoSlot", err)
	}
	cfg, err := NewTLSConfig("test-slot", c1, k1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ReleaseTLS("test-slot") })
	if !HasTLS("test-slot") {
		t.Fatal("slot not registered")
	}
	addr := startTLS(t, cfg)
	if got := servedSerial(t, addr); got != 10 {
		t.Fatalf("serial = %d, want 10", got)
	}
	// Bad pair is refused and the old one keeps serving.
	if err := UpdateCertPaths("test-slot", c2, k1); err == nil {
		t.Fatal("mismatched pair accepted")
	}
	if got := servedSerial(t, addr); got != 10 {
		t.Fatalf("serial after refused update = %d, want 10", got)
	}
	if err := UpdateCertPaths("test-slot", c2, k2); err != nil {
		t.Fatal(err)
	}
	if got := servedSerial(t, addr); got != 20 {
		t.Fatalf("serial after update = %d, want 20", got)
	}
	// Forced reload re-reads even when the stamp did not move.
	st, _ := os.Stat(c2)
	writeSelfSigned(t, c2, k2, "d.example.com", 21)
	_ = os.Chtimes(c2, st.ModTime(), st.ModTime())
	_ = os.Chtimes(k2, st.ModTime(), st.ModTime())
	if errs := ReloadCerts(); len(errs) != 0 {
		t.Fatalf("ReloadCerts errors: %v", errs)
	}
	if got := servedSerial(t, addr); got != 21 {
		t.Fatalf("serial after ReloadCerts = %d, want 21", got)
	}
	ReleaseTLS("test-slot")
	if HasTLS("test-slot") {
		t.Fatal("slot still registered after release")
	}
}
