#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"

Xvfb "$DISPLAY" -screen 0 1440x900x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!

# Give Xvfb a moment to create the display socket before desktop services start.
i=0
while [ ! -S "/tmp/.X11-unix/X${DISPLAY#:}" ] && [ "$i" -lt 50 ]; do
  i=$((i + 1))
  sleep 0.1
done

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
  cat /tmp/xvfb.log >&2 || true
  exit 1
fi

fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -rfbport 5900 -nopw >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/websockify.log 2>&1 &
nginx -c /app/nginx.conf -g 'daemon off;' >/tmp/nginx.log 2>&1 &

exec uvicorn app.main:app --host 127.0.0.1 --port 8000
