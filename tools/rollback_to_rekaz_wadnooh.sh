#!/bin/bash
# Emergency rollback: make rekaz.wadnooh.com the primary RKAZ domain again.
# Run on the RKAZ VPS.
set -euo pipefail

PRIMARY_DOMAIN="${PRIMARY_DOMAIN:-rekaz.wadnooh.com}"
SECONDARY_DOMAIN="${SECONDARY_DOMAIN:-report.ralenjaz.com}"
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

proxy_location() {
  cat <<EOF
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
EOF
}

write_site() {
  local primary_cert="/etc/letsencrypt/live/${PRIMARY_DOMAIN}/fullchain.pem"
  local primary_key="/etc/letsencrypt/live/${PRIMARY_DOMAIN}/privkey.pem"
  local secondary_cert="/etc/letsencrypt/live/${SECONDARY_DOMAIN}/fullchain.pem"
  local secondary_key="/etc/letsencrypt/live/${SECONDARY_DOMAIN}/privkey.pem"

  cat > "${SITE_PATH}" <<EOF
# RKAZ emergency rollback.
# Primary:   ${PRIMARY_DOMAIN}
# Redirect:  ${SECONDARY_DOMAIN} -> ${PRIMARY_DOMAIN}
server {
    listen 80;
    listen [::]:80;
    server_name ${PRIMARY_DOMAIN};
$(proxy_location)
}

server {
    listen 80;
    listen [::]:80;
    server_name ${SECONDARY_DOMAIN};
    return 301 https://${PRIMARY_DOMAIN}\$request_uri;
}
EOF

  if [ -f "${primary_cert}" ] && [ -f "${primary_key}" ]; then
    cat >> "${SITE_PATH}" <<EOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${PRIMARY_DOMAIN};

    ssl_certificate ${primary_cert};
    ssl_certificate_key ${primary_key};
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

$(proxy_location)
}
EOF
  fi

  if [ -f "${secondary_cert}" ] && [ -f "${secondary_key}" ]; then
    cat >> "${SITE_PATH}" <<EOF

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${SECONDARY_DOMAIN};

    ssl_certificate ${secondary_cert};
    ssl_certificate_key ${secondary_key};
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    return 301 https://${PRIMARY_DOMAIN}\$request_uri;
}
EOF
  fi

  ln -sfn "${SITE_PATH}" "${SITE_LINK}"
}

ensure_primary_certificate() {
  if [ -f "/etc/letsencrypt/live/${PRIMARY_DOMAIN}/fullchain.pem" ]; then
    return 0
  fi
  certbot --nginx \
    -d "${PRIMARY_DOMAIN}" \
    --non-interactive \
    --agree-tos \
    -m "${CERTBOT_EMAIL}"
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

verify() {
  systemctl restart rekaz
  systemctl reload nginx
  sleep 2

  echo "=== primary health ==="
  curl -sS "https://${PRIMARY_DOMAIN}/health"
  echo
  echo "=== secondary redirect ==="
  curl -sSI "https://${SECONDARY_DOMAIN}/login" | head -20 || true
}

require_root
ensure_primary_certificate
write_site
nginx -t
ensure_env
verify
