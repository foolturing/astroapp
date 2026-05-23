#!/bin/bash
set -e

# Load env vars
source /etc/environment
export DEEPSEEK_API_KEY DEEPSEEK_BASE_URL AUTH_TOKEN

# Kill old
pkill -9 -f "server.py" 2>/dev/null || true
pkill -9 -f gunicorn 2>/dev/null || true
sleep 1

# Activate venv and start Flask
cd /opt/astrologist
source venv/bin/activate
nohup python3 server.py > /tmp/astro.log 2>&1 &

sleep 3

# Check if it's alive
if curl -s http://localhost:5001/ > /dev/null 2>&1; then
    echo "Server is running on port 5001"
else
    echo "Startup failed, check /tmp/astro.log:"
    cat /tmp/astro.log
    exit 1
fi

# Update nginx to point to 5001 (Flask default port)
if ! grep -q "proxy_pass http://127.0.0.1:5001" /etc/nginx/sites-available/astrologist 2>/dev/null; then
    sed -i 's|proxy_pass http://127.0.0.1:[0-9]*|proxy_pass http://127.0.0.1:5001|' /etc/nginx/sites-available/astrologist 2>/dev/null || true
fi

systemctl restart nginx 2>/dev/null || nginx -s reload 2>/dev/null || true

echo "Done. Visit https://898802.xyz"
