#!/bin/bash
# Run on the VPS after DNS A records point to this server:
#   report.ralenjaz.com -> 191.101.2.59
#   rekaz.wadnooh.com   -> 191.101.2.59
#
# This keeps RKAZ on its own nginx site while the customer-facing domain
# moves to report.ralenjaz.com.
set -euo pipefail

PRIMARY_DOMAIN="${PRIMARY_DOMAIN:-report.ralenjaz.com}"
LEGACY_DOMAIN="${LEGACY_DOMAIN:-rekaz.wadnooh.com}"
APP_UPSTREAM="${APP_UPSTREAM:-http://127.0.0.1:8010}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-admin@wadnooh.com}"
SITE_NAME="rekaz"
SITE_PATH="/etc/nginx/sites-available/${SITE_NAME}"
SITE_LINK="/etc/nginx/sites-enabled/${SITE_NAME}"

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash $0" >&2
    exit 1
  fi
}

write_http_site() {
  cat > "${SITE_PATH}" <<EOF
# RKAZ temporary/current hosting on the existing VPS.
# Primary: ${PRIMARY_DOMAIN}
# Legacy:  ${LEGACY_DOMAIN}
server {
    listen 80;
    listen [::]:80;
    server_name ${PRIMARY_DOMAIN};

    client_max_body_size 32m;

    location / {
        proxy_pass ${APP_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
    }
}

server {
    listen 80;
    listen [::]:80;
    server_name ${LEGACY_DOMAIN};
    return 301 https://${PRIMARY_DOMAIN}\$request_uri;
}
EOF
  ln -sfn "${SITE_PATH}" "${SITE_LINK}"
}

ensure_env() {
  touch /etc/rekaz.env
  if grep -q '^APP_BASE_URL=' /etc/rekaz.env; then
    sed -i "s|^APP_BASE_URL=.*|APP_BASE_URL=https://${PRIMARY_DOMAIN}|" /etc/rekaz.env
  else
    echo "APP_BASE_URL=https://${PRIMARY_DOMAIN}" >> /etc/rekaz.env
  fi
  for line in 'FORCE_HTTPS=1' 'SESSION_COOKIE_SECURE=1' 'PREFERRED_URL_SCHEME=https'; do
    key="${line%%=*}"
    if grep -q "^${key}=" /etc/rekaz.env; then
      sed -i "s|^${key}=.*|${line}|" /etc/rekaz.env
    else
      echo "${line}" >> /etc/rekaz.env
    fi
  done
}

verify_dns() {
  echo "=== DNS ==="
  for domain in "${PRIMARY_DOMAIN}" "${LEGACY_DOMAIN}"; do
    echo "${domain}: $(dig +short "${domain}" A | tr '\n' ' ')"
  done
}

issue_certificates() {
  certbot --nginx \
    -d "${PRIMARY_DOMAIN}" \
    -d "${LEGACY_DOMAIN}" \
    --non-interactive \
    --agree-tos \
    --redirect \
    -m "${CERTBOT_EMAIL}"
}

verify() {
  systemctl restart rekaz
  systemctl reload nginx
  sleep 2

  echo "=== verify primary ==="
  curl -sS "https://${PRIMARY_DOMAIN}/health"
  echo
  echo "=== verify legacy redirect ==="
  curl -sSI "https://${LEGACY_DOMAIN}/login" | head -20
}

require_root
verify_dns
write_http_site
nginx -t
systemctl reload nginx
ensure_env
issue_certificates
nginx -t
verify
