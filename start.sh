#!/bin/sh
set -eu
Xvfb :99 -screen 0 1440x900x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
nginx -c /app/nginx.conf -g 'daemon off;' >/tmp/nginx.log 2>&1 &
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
