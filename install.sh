#!/bin/bash
set -e
echo "=== Astrologist Install ==="

# Fix SSH (坑1: missing /run/sshd)
mkdir -p /run/sshd && chmod 755 /run/sshd

# SSH config (坑2: port + firewall)
cat > /etc/ssh/sshd_config << 'SSHEOF'
Include /etc/ssh/sshd_config.d/*.conf
Port 443
Port 22
PermitRootLogin yes
PasswordAuthentication yes
UsePAM yes
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
SSHEOF
mkdir -p /etc/ssh/sshd_config.d
systemctl restart ssh
echo "SSH done"

# Install packages
apt update
apt install -y ufw nginx python3-pip python3-venv
pip3 install flask requests swisseph

# Firewall (坑2)
ufw allow 443/tcp
ufw allow 80/tcp
ufw allow 22/tcp
ufw --force enable

# Nginx
cat > /etc/nginx/sites-available/astrologist << 'NGXEOF'
server {
    listen 80;
    server_name 898802.xyz www.898802.xyz;
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
NGXEOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/astrologist /etc/nginx/sites-enabled/
nginx -t && systemctl restart nginx
echo "Nginx done"

# Auto-start on reboot
(crontab -l 2>/dev/null; echo '@reboot cd /opt/astrologist && DEEPSEEK_API_KEY=sk-901922ed10664abd981537a2e4bb1bb8 DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic AUTH_TOKEN=10d0ss python3 server.py > /tmp/astro.log 2>&1') | crontab -

# Start now
pkill -f "server.py" 2>/dev/null || true
cd /opt/astrologist && DEEPSEEK_API_KEY=sk-901922ed10664abd981537a2e4bb1bb8 DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic AUTH_TOKEN=10d0ss nohup python3 server.py > /tmp/astro.log 2>&1 &
sleep 4
curl -s http://localhost/ | head -1 && echo ""
echo "=== DONE: https://898802.xyz ==="
