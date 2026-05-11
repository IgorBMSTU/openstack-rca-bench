#!/bin/bash
# Loki and Promtail Management Script
# Location: /home/accentos/loki/manage.sh

LOKI_DIR="/home/accentos/loki"
LOKI_PID_FILE="$LOKI_DIR/loki.pid"
PROMTAIL_PID_FILE="$LOKI_DIR/promtail.pid"

check_status() {
    echo "=== Loki Status ==="
    if curl -s http://localhost:3100/ready > /dev/null 2>&1; then
        echo "✅ Loki is running on http://localhost:3100"
        curl -s http://localhost:3100/ready | head -1
    else
        echo "❌ Loki is not running"
    fi
    
    echo ""
    echo "=== Promtail Status ==="
    if curl -s http://localhost:9080/api/v1/status/targets > /dev/null 2>&1; then
        echo "✅ Promtail is running on http://localhost:9080"
    else
        echo "❌ Promtail is not running"
    fi
}

start_loki() {
    echo "Starting Loki..."
    cd $LOKI_DIR
    nohup ./loki-linux-amd64 -config.file=loki-config.yaml > loki.log 2>&1 &
    echo $! > $LOKI_PID_FILE
    sleep 2
    if curl -s http://localhost:3100/ready > /dev/null 2>&1; then
        echo "✅ Loki started successfully"
    else
        echo "❌ Failed to start Loki"
    fi
}

start_promtail() {
    echo "Starting Promtail..."
    cd $LOKI_DIR
    sudo nohup ./promtail-linux-amd64 -config.file=promtail-config.yaml > promtail.log 2>&1 &
    echo $! > $PROMTAIL_PID_FILE
    sleep 2
    if curl -s http://localhost:9080/api/v1/status/targets > /dev/null 2>&1; then
        echo "✅ Promtail started successfully"
    else
        echo "❌ Failed to start Promtail"
    fi
}

stop_loki() {
    echo "Stopping Loki..."
    pkill -f loki-linux-amd64
    rm -f $LOKI_PID_FILE
    echo "✅ Loki stopped"
}

stop_promtail() {
    echo "Stopping Promtail..."
    sudo pkill -f promtail-linux-amd64
    rm -f $PROMTAIL_PID_FILE
    echo "✅ Promtail stopped"
}

query_logs() {
    local job=$1
    local limit=${2:-10}
    echo "Querying logs for job: $job (limit: $limit)"
    curl -s "http://localhost:3100/loki/api/v1/query_range?query=%7Bjob%3D%22${job}%22%7D&limit=${limit}" | jq '.data.result[] | .values[] | .[1]' 2>/dev/null || echo "No logs found or jq not installed"
}

case "$1" in
    status)
        check_status
        ;;
    start)
        start_loki
        start_promtail
        ;;
    start-loki)
        start_loki
        ;;
    start-promtail)
        start_promtail
        ;;
    stop)
        stop_promtail
        stop_loki
        ;;
    stop-loki)
        stop_loki
        ;;
    stop-promtail)
        stop_promtail
        ;;
    restart)
        stop_promtail
        stop_loki
        sleep 2
        start_loki
        start_promtail
        ;;
    query)
        query_logs $2 $3
        ;;
    *)
        echo "Usage: $0 {status|start|stop|restart|start-loki|stop-loki|start-promtail|stop-promtail|query <job> [limit]}"
        echo ""
        echo "Available jobs: neutron, ironic, mysql, rabbitmq, redis, system-messages"
        exit 1
        ;;
esac
