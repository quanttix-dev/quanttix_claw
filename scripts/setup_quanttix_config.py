#!/usr/bin/env python3
"""
setup_quanttix_config.py
Configura o OpenClaw para rodar no ambiente Quanttix (RunPod / lab).

Uso:
    python3 scripts/setup_quanttix_config.py [--planner-url URL] [--token TOKEN]

O script:
  1. Gera (ou reutiliza) o OPENCLAW_GATEWAY_TOKEN
  2. Configura o provider local apontando para o llama.cpp do planejador (porta 8091)
  3. Salva as variáveis em scripts/quanttix.env para o start_gateway.sh carregar
"""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / "scripts" / "quanttix.env"
OPENCLAW_BIN = str(REPO_ROOT / "openclaw.mjs")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def openclaw(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Executa um subcomando openclaw via Node."""
    return run(["node", OPENCLAW_BIN, *args], check=check)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Configura OpenClaw para Quanttix")
    parser.add_argument(
        "--planner-url",
        default=os.environ.get("CLAW_PLANNER_URL", "http://localhost:8091/v1"),
        help="URL base do servidor llama.cpp do planejador (padrão: http://localhost:8091/v1)",
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
    args = parser.parse_args()

    print("=== Configuração OpenClaw — Quanttix ===\n")

    # Carrega env existente e mescla com novos valores
    env = load_env()

    # 1. Token
    token = args.token or env.get("OPENCLAW_GATEWAY_TOKEN", "") or secrets.token_hex(32)
    env["OPENCLAW_GATEWAY_TOKEN"] = token
    print(f"[token] OPENCLAW_GATEWAY_TOKEN={token[:8]}…")

    # 2. Porta do gateway
    env["CLAW_GATEWAY_PORT"] = args.gateway_port

    # 3. URL do planner
    env["CLAW_PLANNER_URL"] = args.planner_url
    print(f"[planner] URL: {args.planner_url}")

    # Exporta token para que os comandos openclaw abaixo consigam autenticar
    os.environ["OPENCLAW_GATEWAY_TOKEN"] = token

    # 4. Configura gateway + plugin via CLI (best-effort — gateway não precisa estar rodando)
    print("\n[config] Aplicando configurações via openclaw config …")
    plugin_dir = str(REPO_ROOT / "extensions" / "quanttix-planner")
    configs = [
        ("gateway.mode", "local"),
        ("gateway.bind", "loopback"),
        # Carrega o plugin quanttix-planner via load paths
        ("plugins.load.paths", f'["{plugin_dir}"]'),
    ]
    for key, value in configs:
        result = openclaw("config", "set", key, value, check=False)
        status = "OK" if result.returncode == 0 else f"aviso ({result.stderr.strip()[:80]})"
        print(f"  {key}={value} → {status}")

    # 5. Salva arquivo de env
    save_env(env)

    print("\n[pronto] Execute ./scripts/start_gateway.sh para subir o gateway.")


if __name__ == "__main__":
    main()
