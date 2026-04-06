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
  4. Configura gateway.mode=local e plugins.load.paths
  5. Salva variáveis em scripts/quanttix.env para o start_gateway.sh carregar
"""

import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE   = REPO_ROOT / "scripts" / "quanttix.env"
OPENCLAW_BIN = str(REPO_ROOT / "openclaw.mjs")

# Caminhos de plugins (disco local — chmod funciona)
LOCAL_PLUGINS_ROOT = Path("/root/openclaw-plugins")
PLUGIN_SRC  = REPO_ROOT / "extensions" / "quanttix-planner"
PLUGIN_DST  = LOCAL_PLUGINS_ROOT / "quanttix-planner"
BUNDLED_SRC = REPO_ROOT / "dist-runtime" / "extensions"
BUNDLED_DST = LOCAL_PLUGINS_ROOT / "extensions"

# Dados do OpenClaw (sessões, histórico) — ficam no volume /workspace
OPENCLAW_DATA_DIR = REPO_ROOT / ".openclaw"       # /workspace/quanttix_claw/.openclaw
OPENCLAW_HOME_LINK = Path("/root/.openclaw")       # symlink → acima


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def openclaw(*args: str, check: bool = True) -> subprocess.CompletedProcess:
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


def fix_permissions(path: Path) -> None:
    """755 em dirs, 644 em arquivos reais (ignora symlinks quebrados)."""
    for root, dirs, files in os.walk(path):
        os.chmod(root, 0o755)
        for f in files:
            full = os.path.join(root, f)
            # Pula symlinks quebrados (target não existe)
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
        # Era um diretório real — move conteúdo para o workspace e transforma em link
        print(f"\n[data] Movendo {OPENCLAW_HOME_LINK} para {OPENCLAW_DATA_DIR} …")
        for item in OPENCLAW_HOME_LINK.iterdir():
            dest = OPENCLAW_DATA_DIR / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
        shutil.rmtree(OPENCLAW_HOME_LINK)

    OPENCLAW_HOME_LINK.symlink_to(OPENCLAW_DATA_DIR)
    print(f"\n[data] Symlink criado: {OPENCLAW_HOME_LINK} → {OPENCLAW_DATA_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configura OpenClaw para Quanttix")
    parser.add_argument(
        "--planner-url",
        default=os.environ.get("CLAW_PLANNER_URL", "http://localhost:8091/v1"),
        help="URL do servidor llama.cpp do planejador (padrão: http://localhost:8091/v1)",
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
        "--skip-bundled",
        action="store_true",
        help="Não recopiar os bundled plugins (mais rápido em re-execuções)",
    )
    args = parser.parse_args()

    print("=== Configuração OpenClaw — Quanttix ===\n")

    LOCAL_PLUGINS_ROOT.mkdir(parents=True, exist_ok=True)

    env = load_env()

    # 1. Token
    token = args.token or env.get("OPENCLAW_GATEWAY_TOKEN", "") or secrets.token_hex(32)
    env["OPENCLAW_GATEWAY_TOKEN"] = token
    print(f"[token] OPENCLAW_GATEWAY_TOKEN={token[:8]}…")

    # 2. Porta e URL do planner
    env["CLAW_GATEWAY_PORT"] = args.gateway_port
    env["CLAW_PLANNER_URL"]  = args.planner_url
    print(f"[planner] URL: {args.planner_url}")

    # 3. Symlink dados → /workspace (sessões e histórico no volume grande)
    setup_openclaw_data_dir()

    # 4. Copia plugin custom para disco local (resolve bloqueio NFS)
    plugin_local = sync_to_local(PLUGIN_SRC, PLUGIN_DST, "plugin")
    if plugin_local:
        env["CLAW_PLUGIN_LOCAL_PATH"] = plugin_local

    # 5. Copia bundled plugins para disco local (resolve bloqueio NFS para todos os providers)
    if not args.skip_bundled:
        bundled_local = sync_to_local(BUNDLED_SRC, BUNDLED_DST, "bundled")
        if bundled_local:
            env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = bundled_local
    elif BUNDLED_DST.exists():
        env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = str(BUNDLED_DST)

    # Exporta token para autenticar comandos openclaw abaixo
    os.environ["OPENCLAW_GATEWAY_TOKEN"] = token

    # 6. Configura gateway via CLI
    print("\n[config] Aplicando configurações via openclaw config …")
    plugin_path = env.get("CLAW_PLUGIN_LOCAL_PATH", str(PLUGIN_DST))
    configs = [
        ("gateway.mode", "local"),
        ("gateway.bind", "loopback"),
        ("plugins.load.paths", f'["{plugin_path}"]'),
    ]
    for key, value in configs:
        result = openclaw("config", "set", key, value, check=False)
        status = "OK" if result.returncode == 0 else f"aviso ({result.stderr.strip()[:80]})"
        print(f"  {key}={value} → {status}")

    # 7. Salva env
    save_env(env)

    print("\n[pronto] Execute ./scripts/start_gateway.sh para subir o gateway.")


if __name__ == "__main__":
    main()
