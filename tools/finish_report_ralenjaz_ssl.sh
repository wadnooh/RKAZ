#!/bin/bash
# Run on VPS AFTER DNS A record: report.ralenjaz.com → 191.101.2.59
# Issues Let's Encrypt cert and redirects rekaz.wadnooh.com → report.ralenjaz.com
# Does NOT touch report.rtcco.org / /opt/weeklyreport
set -euo pipefail

echo "Checking DNS..."
getent hosts report.ralenjaz.com || true
dig +short report.ralenjaz.com A || true

certbot --nginx -d report.ralenjaz.com --non-interactive --agree-tos --redirect -m admin@wadnooh.com

cat > /etc/nginx/sites-available/rekaz.wadnooh.com << 'EOF'
# Legacy host — redirect to RKAZ primary domain
server {
    listen 80;
    listen [::]:80;
    server_name rekaz.wadnooh.com;
    return 301 https://report.ralenjaz.com$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name rekaz.wadnooh.com;

    ssl_certificate /etc/letsencrypt/live/rekaz.wadnooh.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/rekaz.wadnooh.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    return 301 https://report.ralenjaz.com$request_uri;
}
EOF

nginx -t
systemctl reload nginx

if grep -q '^APP_BASE_URL=' /etc/rekaz.env; then
  sed -i 's|^APP_BASE_URL=.*|APP_BASE_URL=https://report.ralenjaz.com|' /etc/rekaz.env
else
  echo 'APP_BASE_URL=https://report.ralenjaz.com' >> /etc/rekaz.env
fi
systemctl restart rekaz
sleep 2

echo "=== verify new ==="
curl -sS https://report.ralenjaz.com/health
echo
echo "=== verify redirect ==="
curl -sSI https://rekaz.wadnooh.com/login | head -20
echo "=== rtcco untouched ==="
curl -sS -o /dev/null -w 'rtcco:%{http_code}\n' https://report.rtcco.org/
