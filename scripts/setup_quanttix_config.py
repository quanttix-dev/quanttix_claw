#!/usr/bin/env python3
"""
setup_quanttix_config.py
Configura o OpenClaw para rodar no ambiente Quanttix (RunPod / lab).

Uso:
    python3 scripts/setup_quanttix_config.py [--planner-url URL] [--token TOKEN]

O script:
  1. Gera (ou reutiliza) o OPENCLAW_GATEWAY_TOKEN
  2. Copia plugins para /root/ (disco local — NFS não suporta chmod)
  3. Cria symlink /root/.openclaw → /workspace/quanttix_claw/.openclaw/
     (dados/sessões ficam no volume persistente com espaço grande)
  4. Configura gateway (mode, bind, controlUi, allowedOrigins)
  5. Registra provider quanttix-planner via models.providers (models.mode=merge)
  6. Salva variáveis em scripts/quanttix.env para o start_gateway.sh carregar
"""

import argparse
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE   = REPO_ROOT / "scripts" / "quanttix.env"

# Caminhos de plugins (disco local — chmod funciona)
LOCAL_PLUGINS_ROOT = Path("/root/openclaw-plugins")
PLUGIN_SRC  = REPO_ROOT / "extensions" / "quanttix-planner"
PLUGIN_DST  = LOCAL_PLUGINS_ROOT / "quanttix-planner"

# Dados do OpenClaw (sessões, histórico) — ficam no volume /workspace
OPENCLAW_DATA_DIR = REPO_ROOT / ".openclaw"       # /workspace/quanttix_claw/.openclaw
OPENCLAW_HOME_LINK = Path("/root/.openclaw")       # symlink → acima


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def openclaw(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Usa o binário global openclaw (npm install -g openclaw@latest)."""
    return run(["openclaw", *args], check=check)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"')
    return env


def save_env(env: dict[str, str]) -> None:
    lines = ["# Auto-gerado por setup_quanttix_config.py — não edite manualmente\n"]
    for k, v in env.items():
        lines.append(f'{k}="{v}"\n')
    ENV_FILE.write_text("".join(lines))
    print(f"\n[OK] Variáveis salvas em {ENV_FILE.relative_to(REPO_ROOT)}")


def fix_permissions(path: Path) -> None:
    """755 em dirs, 644 em arquivos reais (ignora symlinks quebrados)."""
    for root, dirs, files in os.walk(path):
        os.chmod(root, 0o755)
        for f in files:
            full = os.path.join(root, f)
            if os.path.islink(full) and not os.path.exists(full):
                continue
            os.chmod(full, 0o644)


def sync_to_local(src: Path, dst: Path, label: str) -> str:
    """Copia src para dst no disco local e corrige permissões."""
    if not src.exists():
        print(f"  [aviso] {label}: {src} não encontrado — pulando")
        return ""
    print(f"\n[{label}] Copiando para disco local: {dst}")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    fix_permissions(dst)
    print(f"  chmod 755/644 aplicado ✓")
    return str(dst)


def setup_openclaw_data_dir() -> None:
    """
    Garante que /root/.openclaw aponta para /workspace/quanttix_claw/.openclaw/
    Isso mantém sessões e histórico no volume NFS (espaço grande) e não em /root/.
    """
    OPENCLAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if OPENCLAW_HOME_LINK.is_symlink():
        if OPENCLAW_HOME_LINK.resolve() == OPENCLAW_DATA_DIR.resolve():
            print(f"\n[data] Symlink já correto: {OPENCLAW_HOME_LINK} → {OPENCLAW_DATA_DIR}")
            return
        OPENCLAW_HOME_LINK.unlink()

    if OPENCLAW_HOME_LINK.exists():
        print(f"\n[data] Movendo {OPENCLAW_HOME_LINK} para {OPENCLAW_DATA_DIR} …")
        for item in OPENCLAW_HOME_LINK.iterdir():
            dest = OPENCLAW_DATA_DIR / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
        shutil.rmtree(OPENCLAW_HOME_LINK)

    OPENCLAW_HOME_LINK.symlink_to(OPENCLAW_DATA_DIR)
    print(f"\n[data] Symlink criado: {OPENCLAW_HOME_LINK} → {OPENCLAW_DATA_DIR}")


def detect_runpod_proxy_origin(expose_port: str) -> str:
    """Detecta a URL do proxy RunPod a partir do hostname do POD."""
    pod_id = os.environ.get("RUNPOD_POD_ID", "")
    if not pod_id:
        # Tenta extrair do hostname (formato: container_id, mas não tem pod_id)
        # O pod_id normalmente vem da env var ou do SSH host
        return ""
    return f"https://{pod_id}-{expose_port}.proxy.runpod.net"


def main() -> None:
    parser = argparse.ArgumentParser(description="Configura OpenClaw para Quanttix")
    parser.add_argument(
        "--planner-url",
        default=os.environ.get("CLAW_PLANNER_URL", "http://localhost:8091/v1"),
        help="URL do servidor llama.cpp do planejador (padrão: http://localhost:8091/v1)",
    )
    parser.add_argument(
        "--planner-model-id",
        default=os.environ.get("CLAW_PLANNER_MODEL_ID", "gemma-4-e2b"),
        help="Model ID do planner no llama.cpp (padrão: gemma-4-e2b)",
    )
    parser.add_argument(
        "--planner-ctx",
        type=int,
        default=int(os.environ.get("CLAW_PLANNER_CTX", "32768")),
        help="Context window do planner (padrão: 32768)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""),
        help="Token do gateway (gerado automaticamente se omitido)",
    )
    parser.add_argument(
        "--gateway-port",
        default=os.environ.get("CLAW_GATEWAY_PORT", "18789"),
        help="Porta do gateway OpenClaw (padrão: 18789)",
    )
    parser.add_argument(
        "--expose-port",
        default=os.environ.get("CLAW_EXPOSE_PORT", "8888"),
        help="Porta do proxy canvas para acesso externo (padrão: 8888)",
    )
    parser.add_argument(
        "--canvas-origin",
        default=os.environ.get("CLAW_CANVAS_ORIGIN", ""),
        help="URL de origem para Canvas UI (ex: https://POD_ID-8888.proxy.runpod.net)",
    )
    parser.add_argument(
        "--telegram-bot-token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="Token do Telegram Bot (@Quanttix_bot) para canais de negociação",
    )
    parser.add_argument(
        "--telegram-test-chat",
        default=os.environ.get("TELEGRAM_TEST_CHAT_ID", ""),
        help="chat_id numérico do contato de teste no Telegram (obtenha via /getUpdates)",
    )
    args = parser.parse_args()

    print("=== Configuração OpenClaw — Quanttix ===\n")

    LOCAL_PLUGINS_ROOT.mkdir(parents=True, exist_ok=True)

    env = load_env()

    # 1. Token
    token = args.token or env.get("OPENCLAW_GATEWAY_TOKEN", "") or secrets.token_hex(32)
    env["OPENCLAW_GATEWAY_TOKEN"] = token
    print(f"[token] OPENCLAW_GATEWAY_TOKEN={token[:8]}…")

    # 2. Porta, URL do planner e expose
    env["CLAW_GATEWAY_PORT"] = args.gateway_port
    env["CLAW_EXPOSE_PORT"]  = args.expose_port
    env["CLAW_PLANNER_URL"]  = args.planner_url
    env["CLAW_PLANNER_MODEL_ID"] = args.planner_model_id
    env["CLAW_PLANNER_CTX"]  = str(args.planner_ctx)
    print(f"[planner] URL: {args.planner_url}")
    print(f"[planner] Model ID: {args.planner_model_id}")
    print(f"[planner] Context: {args.planner_ctx}")

    # 3. Symlink dados → /workspace (sessões e histórico no volume grande)
    setup_openclaw_data_dir()

    # 4. Copia plugin custom para disco local (resolve bloqueio NFS)
    plugin_local = sync_to_local(PLUGIN_SRC, PLUGIN_DST, "plugin")
    if plugin_local:
        env["CLAW_PLUGIN_LOCAL_PATH"] = plugin_local

    # Exporta token para autenticar comandos openclaw abaixo
    os.environ["OPENCLAW_GATEWAY_TOKEN"] = token

    # 5. Configura gateway via CLI
    print("\n[config] Aplicando configurações via openclaw config …")
    plugin_path = env.get("CLAW_PLUGIN_LOCAL_PATH", str(PLUGIN_DST))

    # 5a. Provider do planner (via models.providers — não depende do plugin SDK)
    planner_url = env.get("CLAW_PLANNER_URL", args.planner_url)
    planner_model = env.get("CLAW_PLANNER_MODEL_ID", args.planner_model_id)
    planner_ctx = int(env.get("CLAW_PLANNER_CTX", str(args.planner_ctx)))
    provider_config = {
        "baseUrl": planner_url,
        "apiKey": "local",
        "api": "openai-completions",
        "models": [{
            "id": planner_model,
            "name": "Gemma 4 E2B (Quanttix Planner)",
            "reasoning": False,
            "input": ["text"],
            "cost": {"input": 0, "output": 0},
            "contextWindow": planner_ctx,
            "maxTokens": 4096,
        }],
    }
    provider_json = json.dumps(provider_config, separators=(",", ":"))

    # 5b. Canvas UI — allowedOrigins para acesso externo via RunPod proxy
    canvas_origin = args.canvas_origin or env.get("CLAW_CANVAS_ORIGIN", "")
    if not canvas_origin:
        canvas_origin = detect_runpod_proxy_origin(args.expose_port)
    if canvas_origin:
        env["CLAW_CANVAS_ORIGIN"] = canvas_origin
        print(f"[canvas] Origin: {canvas_origin}")

    configs: list[tuple[str, str]] = [
        # Gateway
        ("gateway.mode", "local"),
        ("gateway.bind", "loopback"),
        # Token para CLI conectar ao gateway via WebSocket
        ("gateway.remote.token", token),
        # Control UI — permite acesso externo via proxy
        ("gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback", "true"),
        # Plugins
        ("plugins.load.paths", f'["{plugin_path}"]'),
        # Models — registra provider local via config (bypass do plugin SDK)
        ("models.mode", "merge"),
        ("models.providers.quanttix-planner", provider_json),
    ]

    # Adiciona allowedOrigins se temos uma origin configurada
    if canvas_origin:
        configs.append((
            "gateway.controlUi.allowedOrigins",
            f'["{canvas_origin}"]',
        ))

    # Adiciona configuração Telegram se token presente
    telegram_bot_token = args.telegram_bot_token or env.get("TELEGRAM_BOT_TOKEN", "")
    telegram_test_chat = args.telegram_test_chat or env.get("TELEGRAM_TEST_CHAT_ID", "")

    if telegram_bot_token:
        configs.extend([
            ("channels.telegram.botToken", telegram_bot_token),
            ("channels.telegram.accounts.default.dmPolicy", "allowlist"),
        ])
        if telegram_test_chat:
            configs.append(("channels.telegram.accounts.default.allowFrom", f'["{telegram_test_chat}"]'))
        print(f"\n[telegram] Token configurado: {telegram_bot_token[:12]}…")
        if telegram_test_chat:
            print(f"[telegram] allowFrom: [{telegram_test_chat}]")
    else:
        print("\n[telegram] Token não fornecido — canal Telegram não configurado")
        print("           Passe --telegram-bot-token ou defina TELEGRAM_BOT_TOKEN")

    for key, value in configs:
        result = openclaw("config", "set", key, value, check=False)
        status = "OK" if result.returncode == 0 else f"aviso ({result.stderr.strip()[:80]})"
        print(f"  {key}={value[:60]}{'…' if len(value) > 60 else ''} → {status}")

    # 6. Salva env
    if telegram_bot_token:
        env["TELEGRAM_BOT_TOKEN"]  = telegram_bot_token
    if telegram_test_chat:
        env["TELEGRAM_TEST_CHAT_ID"] = telegram_test_chat

    save_env(env)

    print("\n[pronto] Para subir o gateway:")
    print("  ./scripts/start_gateway.sh              # loopback (local)")
    print("  ./scripts/start_gateway.sh --expose     # exposto via proxy (porta 8888)")


if __name__ == "__main__":
    main()
