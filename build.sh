#!/bin/sh
# Source build for a glibc or musl Linux host (Debian/Ubuntu/Alpine).
# Mirrors the Dockerfile: naive outbound is loaded at runtime through purego
# from libcronet.so, which must sit next to the sui binary.
set -e

cd frontend
npm ci
npm run build

cd ..
echo "Backend"

mkdir -p web/html
rm -fr web/html/*
cp -R frontend/dist/* web/html/

# Detect target architecture for libcronet.so (override with CRONET_ARCH=...).
if [ -z "$CRONET_ARCH" ]; then
	case "$(uname -m)" in
		x86_64 | amd64) CRONET_ARCH=amd64 ;;
		aarch64 | arm64) CRONET_ARCH=arm64 ;;
		*) CRONET_ARCH="" ;;
	esac
fi

BUILD_TAGS="with_quic,with_grpc,with_utls,with_acme,with_gvisor,with_naive_outbound,with_purego,with_tailscale"

if [ -n "$CRONET_ARCH" ]; then
	CRONET_URL="https://github.com/SagerNet/cronet-go/releases/latest/download/libcronet-linux-${CRONET_ARCH}.so"
	echo "Downloading $CRONET_URL"
	if command -v curl >/dev/null 2>&1; then
		curl -fsSL -o ./libcronet.so "$CRONET_URL"
	else
		wget -q -O ./libcronet.so "$CRONET_URL"
	fi
	chmod 755 ./libcronet.so
else
	echo "No prebuilt libcronet.so for $(uname -m); building without naive outbound"
	BUILD_TAGS="with_quic,with_grpc,with_utls,with_acme,with_gvisor,with_tailscale"
fi

export CGO_ENABLED=1
export CGO_CFLAGS="${CGO_CFLAGS:--D_LARGEFILE64_SOURCE}"
go build -ldflags="-w -s" -tags "$BUILD_TAGS" -o sui main.go

echo "Built ./sui"
[ -f ./libcronet.so ] && echo "Keep ./libcronet.so in the same directory as sui (naive outbound loads it at runtime)."
