#!/usr/bin/env bash
# Generate self-signed certificate for local development.
# Wrapper over OpenSSL — production management is out of scope (ARCHITECTURE.md §20).
set -euo pipefail

CERT="${1:-cert.pem}"
KEY="${2:-key.pem}"
DAYS="${3:-365}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "error: openssl not found in PATH" >&2
  exit 1
fi

echo "Generating self-signed certificate..."
echo "  cert: $CERT"
echo "  key:  $KEY"
echo "  days: $DAYS"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$KEY" -out "$CERT" -days "$DAYS" \
  -subj "/CN=localhost/O=http-server-from-scratch/C=RU" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$KEY" 2>/dev/null || true
chmod 644 "$CERT" 2>/dev/null || true

echo "Done. Files are git-ignored — never commit real certificates."
echo "Run: python -m http_server --tls --cert $CERT --key $KEY"
