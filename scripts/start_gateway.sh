#!/usr/bin/env bash
# start_gateway.sh — Inicia (ou para) o gateway OpenClaw no ambiente Quanttix
# Uso:
#   ./scripts/start_gateway.sh             # inicia (loopback — só acesso local)
#   ./scripts/start_gateway.sh --expose    # inicia exposto na LAN/RunPod (porta 8888)
#   ./scripts/start_gateway.sh --stop      # para
#   ./scripts/start_gateway.sh --restart   # para e reinicia
#   ./scripts/start_gateway.sh --status    # verifica
#   ./scripts/start_gateway.sh --logs      # tail do log em tempo real

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/scripts/quanttix.env"
OPENCLAW_BIN="$REPO_ROOT/openclaw.mjs"
LOG_FILE="/tmp/quanttix/openclaw_gateway.log"
PID_FILE="/tmp/quanttix/openclaw_gateway.pid"
PROXY_PID_FILE="/tmp/quanttix/canvas_proxy.pid"
PROXY_LOG="/tmp/quanttix/canvas_proxy.log"
GATEWAY_PORT="${CLAW_GATEWAY_PORT:-18789}"
EXPOSE_PORT="${CLAW_EXPOSE_PORT:-8888}"

# ── helpers ──────────────────────────────────────────────────────────────────
_info()  { echo -e "\033[0;36m[CLAW]\033[0m  $*"; }
_ok()    { echo -e "\033[0;32m[CLAW]\033[0m  $*"; }
_warn()  { echo -e "\033[0;33m[CLAW]\033[0m  $*"; }
_err()   { echo -e "\033[0;31m[CLAW]\033[0m  $*" >&2; }

# Carrega quanttix.env se existir
_load_env() {
    if [[ -f "$ENV_FILE" ]]; then
        # shellcheck disable=SC1090
        set -a; source "$ENV_FILE"; set +a
    fi
}

_port_in_use() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

_stop_gateway() {
    _info "Parando gateway OpenClaw …"
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && _ok "Gateway (PID $PID) encerrado."
        else
            _warn "PID $PID não encontrado — já estava parado."
        fi
        rm -f "$PID_FILE"
    fi
    # Para proxy canvas se rodando
    if [[ -f "$PROXY_PID_FILE" ]]; then
        PPID=$(cat "$PROXY_PID_FILE")
        kill "$PPID" 2>/dev/null && _ok "Canvas proxy (PID $PPID) encerrado."
        rm -f "$PROXY_PID_FILE"
    fi
    pkill -f "openclaw.*gateway" 2>/dev/null || true
    pkill -f "canvas-proxy.mjs" 2>/dev/null || true
    sleep 1
    _ok "Pronto."
}

_status_gateway() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        _ok "Gateway rodando (PID $(cat "$PID_FILE"), porta $GATEWAY_PORT)"
    elif _port_in_use "$GATEWAY_PORT"; then
        _warn "Porta $GATEWAY_PORT em uso (sem PID registrado)"
    else
        _info "Gateway parado."
    fi
}

# ── main ─────────────────────────────────────────────────────────────────────
case "${1:-}" in
    --stop)    _load_env; _stop_gateway; exit 0 ;;
    --status)  _load_env; _status_gateway; exit 0 ;;
    --restart) _load_env; _stop_gateway; sleep 1; exec "$0" ;;
    --expose)  GATEWAY_EXPOSE="true" ;;
    --logs)
        echo -e "\033[1;37m[CLAW] Logs em tempo real — Ctrl+C para sair\033[0m"
        trap 'echo -e "\n\033[0;36m[CLAW]\033[0m Logs encerrados. Gateway ainda rodando."; exit 0' INT
        # Log interno do gateway (onde o OpenClaw realmente escreve)
        TODAY_LOG="/tmp/openclaw/openclaw-$(date +%Y-%m-%d).log"
        FOLLOW_LOG="${TODAY_LOG}"
        # Fallback para o nohup stdout se o log interno não existir ainda
        [[ ! -f "$FOLLOW_LOG" ]] && FOLLOW_LOG="$LOG_FILE"
        tail -n 80 -F "$FOLLOW_LOG" 2>/dev/null | while IFS= read -r line; do
            # Timestamp: 2026-04-06T01:43:05.530+00:00  → cinza
            ts=$(echo "$line" | grep -oP '^\d{4}-\d{2}-\d{2}T[\d:.+]+')
            rest="${line#"$ts"}"
            ts_fmt="\033[2;37m${ts}\033[0m"  # cinza dim

            # Colorir por categoria
            case "$rest" in
                *"failed"*|*"error"*|*"Error"*|*"ERROR"*)
                    echo -e "${ts_fmt}\033[0;31m${rest}\033[0m" ;;  # vermelho
                *"[plugins]"*)
                    echo -e "${ts_fmt}\033[0;33m${rest}\033[0m" ;;  # amarelo
                *"[gateway] ready"*)
                    echo -e "${ts_fmt}\033[1;32m${rest}\033[0m" ;;  # verde bold
                *"[gateway]"*)
                    echo -e "${ts_fmt}\033[0;36m${rest}\033[0m" ;;  # ciano
                *"[hooks]"*)
                    echo -e "${ts_fmt}\033[0;32m${rest}\033[0m" ;;  # verde
                *"[canvas]"*)
                    echo -e "${ts_fmt}\033[0;35m${rest}\033[0m" ;;  # magenta
                *"[heartbeat]"*|*"[health-monitor]"*)
                    echo -e "${ts_fmt}\033[2;37m${rest}\033[0m" ;;  # cinza dim
                *)
                    echo -e "${ts_fmt}${rest}" ;;
            esac
        done
        exit 0
        ;;
esac

_load_env

GATEWAY_BIND="loopback"
GATEWAY_EXPOSE="${GATEWAY_EXPOSE:-false}"

# Verifica se setup já foi executado
if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
    _err "OPENCLAW_GATEWAY_TOKEN não definido."
    _err "Execute primeiro: python3 scripts/setup_quanttix_config.py"
    exit 1
fi

# Verifica Node.js
NODE_VER=$(node --version 2>/dev/null | sed 's/v//') || { _err "Node.js não encontrado."; exit 1; }
NODE_MAJOR="${NODE_VER%%.*}"
if (( NODE_MAJOR < 22 )); then
    _err "Node.js >= 22 necessário (atual: v$NODE_VER)"
    exit 1
fi

# ── Resolver binário do OpenClaw ─────────────────────────────────────────────
if command -v openclaw &>/dev/null; then
    RUN_CMD="openclaw"
    _info "OpenClaw $(openclaw --version 2>/dev/null || echo '') — $(which openclaw)"
else
    _err "OpenClaw não encontrado. Instale com: npm install -g openclaw@latest"
    exit 1
fi

# Para gateway anterior se ainda rodando
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    _info "Gateway já rodando (PID $(cat "$PID_FILE")) — reiniciando …"
    _stop_gateway
fi

# Cria dir de logs/pids
mkdir -p /tmp/quanttix

# Inicia gateway (sempre loopback)
_info "Iniciando OpenClaw gateway na porta $GATEWAY_PORT (loopback) …"
export OPENCLAW_GATEWAY_TOKEN

# shellcheck disable=SC2086
nohup $RUN_CMD gateway run \
    --bind loopback \
    --port "$GATEWAY_PORT" \
    --allow-unconfigured \
    > "$LOG_FILE" 2>&1 &
GATEWAY_PID=$!
echo "$GATEWAY_PID" > "$PID_FILE"

# Aguarda até 15s
_info "Aguardando gateway subir (PID $GATEWAY_PID) …"
for i in $(seq 1 15); do
    sleep 1
    if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
        _err "Gateway encerrou inesperadamente. Últimas linhas do log:"
        tail -20 "$LOG_FILE" >&2
        exit 1
    fi
    if _port_in_use "$GATEWAY_PORT"; then
        _ok "Gateway OpenClaw pronto na porta $GATEWAY_PORT (PID $GATEWAY_PID)"
        break
    fi
done

if ! _port_in_use "$GATEWAY_PORT"; then
    _warn "Gateway pode ainda estar inicializando — verifique: tail -f $LOG_FILE"
fi

# Inicia proxy canvas se --expose foi passado
if [[ "$GATEWAY_EXPOSE" == "true" ]]; then
    _info "Iniciando canvas proxy na porta $EXPOSE_PORT …"
    export CLAW_GATEWAY_PORT="$GATEWAY_PORT"
    export CANVAS_PROXY_PORT="$EXPOSE_PORT"
    nohup node "$REPO_ROOT/scripts/canvas-proxy.mjs" > "$PROXY_LOG" 2>&1 &
    PROXY_PID=$!
    echo "$PROXY_PID" > "$PROXY_PID_FILE"
    sleep 1
    if kill -0 "$PROXY_PID" 2>/dev/null; then
        _ok "Canvas proxy pronto (PID $PROXY_PID, porta $EXPOSE_PORT)"
        _ok "Acesse: https://\${RUNPOD_POD_ID:-<pod-id>}-${EXPOSE_PORT}.proxy.runpod.net/__openclaw__/canvas/"
    else
        _err "Canvas proxy falhou. Log: $PROXY_LOG"
    fi
fi
