#!/usr/bin/env sh
# One-time TLS certificate issuance via Let's Encrypt / Certbot.
# Run this BEFORE starting the full production stack for the first time.
#
# Usage:
#   DOMAIN=yourdomain.com EMAIL=admin@yourdomain.com ./scripts/init-certs.sh
#
# What it does:
#   1. Creates certbot/www and certbot/conf directories
#   2. Starts a minimal nginx container to serve the ACME challenge on port 80
#   3. Runs certbot certonly --webroot to obtain the certificate
#   4. Stops the temporary nginx and prints next steps
#
# After this completes, run:
#   sed -i "s/yourdomain.com/${DOMAIN}/g" nginx.prod.conf
#   docker-compose -f docker-compose.production.yml up -d
set -eu

DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"

if [ -z "${DOMAIN}" ]; then
  printf "Error: DOMAIN is not set.\nUsage: DOMAIN=example.com EMAIL=admin@example.com %s\n" "$0" >&2
  exit 1
fi
if [ -z "${EMAIL}" ]; then
  printf "Error: EMAIL is not set (required by Let's Encrypt for expiry notifications).\n" >&2
  exit 1
fi

echo "[init-certs] Domain : ${DOMAIN}"
echo "[init-certs] Email  : ${EMAIL}"
echo ""

mkdir -p certbot/conf certbot/www

# Temporary nginx config — HTTP only, ACME challenge + health check
TMP_CONF=$(mktemp)
cat > "${TMP_CONF}" <<NGINX
server {
  listen 80;
  server_name ${DOMAIN};
  location /.well-known/acme-challenge/ {
    root /var/www/certbot;
  }
  location / {
    return 200 'OK';
    add_header Content-Type text/plain;
  }
}
NGINX

echo "[init-certs] Starting temporary nginx on port 80 for ACME challenge..."
docker run --rm -d \
  --name relora-nginx-acme \
  -p 80:80 \
  -v "$(pwd)/certbot/www:/var/www/certbot:ro" \
  -v "${TMP_CONF}:/etc/nginx/conf.d/default.conf:ro" \
  nginx:1.27-alpine
rm -f "${TMP_CONF}"

echo "[init-certs] Requesting certificate from Let's Encrypt..."
docker run --rm \
  -v "$(pwd)/certbot/conf:/etc/letsencrypt" \
  -v "$(pwd)/certbot/www:/var/www/certbot" \
  certbot/certbot:latest \
  certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --email "${EMAIL}" \
  --agree-tos \
  --no-eff-email \
  --force-renewal \
  -d "${DOMAIN}"

echo ""
echo "[init-certs] Stopping temporary nginx..."
docker stop relora-nginx-acme

echo ""
echo "[init-certs] Done! Certificate stored in certbot/conf/live/${DOMAIN}/"
echo ""

# ── Auto-patch nginx.prod.conf ─────────────────────────────────────────────────
NGINX_CONF="nginx.prod.conf"
if grep -q "yourdomain.com" "${NGINX_CONF}" 2>/dev/null; then
  echo "[init-certs] Patching ${NGINX_CONF}: yourdomain.com → ${DOMAIN}"
  if sed --version 2>/dev/null | grep -q GNU; then
    # GNU sed (Linux)
    sed -i "s/yourdomain\.com/${DOMAIN}/g" "${NGINX_CONF}"
  else
    # BSD sed (macOS) needs an extension argument
    sed -i '' "s/yourdomain\.com/${DOMAIN}/g" "${NGINX_CONF}"
  fi
  echo "[init-certs] ${NGINX_CONF} updated."
else
  echo "[init-certs] ${NGINX_CONF} already patched or not found — skipping."
fi

echo ""
echo "Next steps:"
echo "  1. Set required env vars in .env (copy from .env.example, fill in all [PROD] values)"
echo "  2. Start the full stack:"
echo "       docker-compose -f docker-compose.production.yml up -d"
