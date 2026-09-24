#!/bin/bash
# GKH Agent service launcher
# Usage: ./start.sh {start|stop|restart|status|log}
# Env override: APP_PORT (default 39001)

set -u

APP_DIR="/data/liuqingting/guokahang"
PID_FILE="$APP_DIR/.app.pid"
LOG_FILE="$APP_DIR/app.log"
PORT="${APP_PORT:-39001}"

# DM runtime lib (dmPython needs libdmdpi.so)
export LD_LIBRARY_PATH="/data/dmdbms/bin:${LD_LIBRARY_PATH:-}"
export APP_PORT="$PORT"

cd "$APP_DIR" || { echo "project dir not found: $APP_DIR"; exit 1; }

is_running() {
    if [ -f "$PID_FILE" ]; then
        local p
        p=$(cat "$PID_FILE")
        kill -0 "$p" 2>/dev/null
        return $?
    fi
    return 1
}

start() {
    if is_running; then
        echo "already running (PID $(cat "$PID_FILE")) on port $PORT"
        return 0
    fi
    echo "starting on port $PORT ..."
    nohup python3 -m backend.app > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 5
    if is_running; then
        echo "started OK (PID $(cat "$PID_FILE"))"
        echo "  health: curl http://127.0.0.1:$PORT/health"
        echo "  query:  POST http://127.0.0.1:$PORT/skill/financial/query  body {question: ...}"
    else
        echo "start FAILED, log tail:"
        tail -15 "$LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop() {
    if is_running; then
        local p
        p=$(cat "$PID_FILE")
        kill "$p" && echo "stopped (PID $p)"
        rm -f "$PID_FILE"
    else
        echo "not running"
        rm -f "$PID_FILE"
    fi
}

status() {
    if is_running; then
        echo "running (PID $(cat "$PID_FILE")) on port $PORT"
    else
        echo "not running"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    log)     tail -f "$LOG_FILE" ;;
    *)       echo "usage: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
