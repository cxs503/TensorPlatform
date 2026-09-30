#!/usr/bin/env bash
set -euo pipefail

CERT_FILE="${TLS_CERT_FILE:-./secrets/tls/fullchain.pem}"
KEY_FILE="${TLS_KEY_FILE:-./secrets/tls/privkey.pem}"

for file in "$CERT_FILE" "$KEY_FILE"; do
  if [ ! -r "$file" ]; then
    echo "TLS readiness failed: unreadable file $file" >&2
    exit 1
  fi
done

openssl x509 -in "$CERT_FILE" -noout -checkend 604800 >/dev/null
cert_public_key=$(openssl x509 -in "$CERT_FILE" -pubkey -noout | openssl sha256)
key_public_key=$(openssl pkey -in "$KEY_FILE" -pubout 2>/dev/null | openssl sha256)
if [ "$cert_public_key" != "$key_public_key" ]; then
  echo "TLS readiness failed: certificate and private key do not match" >&2
  exit 1
fi

echo "TLS certificate and key are valid for at least 7 days"
