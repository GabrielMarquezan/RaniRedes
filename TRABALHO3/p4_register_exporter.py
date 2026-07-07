#!/usr/bin/env python3
"""
p4_register_exporter.py — Exportador de registradores P4 via Thrift
Lê os registradores do BMv2 Simple Switch via interface Thrift e
envia os dados como pacotes UDP ao controlador (controller_trabalho3.py
ou controller_trabalho4.py).

Executar FORA do Mininet, no host root (mesmo namespace do BMv2):
    python3 p4_register_exporter.py --thrift-port 9090 --controller 127.0.0.1

Dependência: runtime_CLI.py do repositório p4lang/behavioral-model
  ou usar a lib bm_runtime que vem com o BMv2.
"""

import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EXPORTER] %(message)s"
)
log = logging.getLogger(__name__)

# Tenta importar a biblioteca Thrift do BMv2
try:
    # Caminho típico em instalações p4lang/tutorials
    sys.path.insert(0, '/home/p4/tutorials/utils')
    from p4_mininet import P4Switch  # type: ignore
except ImportError:
    pass

try:
    # Acesso direto via Thrift (biblioteca bm_runtime)
    from bm_runtime.standard import Standard  # type: ignore
    from bm_runtime.standard.ttypes import *   # type: ignore
    from thrift.transport import TSocket, TTransport  # type: ignore
    from thrift.protocol import TBinaryProtocol       # type: ignore
    HAS_THRIFT = True
except ImportError:
    HAS_THRIFT = False
    log.warning("bm_runtime não encontrado — usando leitura via subprocess (simple_switch_CLI)")

# ─────────────────────────────────────────────────────────────────────────────
# Leitura dos registradores via subprocess (fallback sem Thrift)
# ─────────────────────────────────────────────────────────────────────────────


def find_cli_path(cli_path: str | None) -> str:
    """
    Retorna o caminho executável do simple_switch_CLI.
    Se cli_path for informado, usa ele; caso contrário, procura no PATH.
    """
    if cli_path:
        return cli_path
    found = shutil.which("simple_switch_CLI")
    if not found:
        raise FileNotFoundError(
            "simple_switch_CLI não encontrado no PATH. "
            "Use --cli-path para informar o caminho absoluto."
        )
    return found


def read_register_cli(reg_name: str, thrift_port: int, cli_path: str) -> int:
    """
    Lê um registrador P4 via simple_switch_CLI.
    Retorna o valor inteiro no índice 0.
    """
    cli_cmd = find_cli_path(cli_path)
    cmd = (
        f"echo 'register_read {reg_name} 0' "
        f"| {cli_cmd} --thrift-port {thrift_port}"
    )
    try:
        out = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        stdout = out.stdout.decode(errors="replace")
        stderr = out.stderr.decode(errors="replace")

        if out.returncode != 0:
            log.error(
                "simple_switch_CLI falhou para %s (rc=%d): %s",
                reg_name, out.returncode, stderr.strip() or stdout.strip()
            )
            return 0

        # A saída pode ter formatos como:
        #   reg_name[0]= 123
        #   reg_name[0] = 123
        #   reg_name[0]: 123
        # Usamos regex para capturar o valor numérico.
        for line in stdout.splitlines():
            if reg_name in line:
                match = re.search(r"[\:\=]\s*(\d+)", line)
                if match:
                    return int(match.group(1))

        log.warning(
            "Não foi possível fazer parse do valor de %s. Saída:\n%s",
            reg_name, stdout
        )
        return 0

    except subprocess.TimeoutExpired:
        log.error("Timeout ao ler %s via simple_switch_CLI", reg_name)
    except FileNotFoundError as exc:
        log.error("%s", exc)
    except Exception as exc:
        log.error("Erro inesperado ao ler %s: %s", reg_name, exc)
    return 0


def read_all_registers(thrift_port: int, cli_path: str) -> dict:
    """Lê todos os 4 registradores de telemetria."""
    return {
        "packet_count": read_register_cli("reg_packet_count", thrift_port, cli_path),
        "byte_count":   read_register_cli("reg_byte_count",   thrift_port, cli_path),
        "icmp_count":   read_register_cli("reg_icmp_count",   thrift_port, cli_path),
        "min_ttl":      read_register_cli("reg_min_ttl",      thrift_port, cli_path),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Empacotamento e envio UDP
# ─────────────────────────────────────────────────────────────────────────────

TELEMETRY_FORMAT = "!IQQIB"
TELEMETRY_SIZE = struct.calcsize(TELEMETRY_FORMAT)


def send_telemetry(sock: socket.socket, controller_addr: tuple,
                   switch_id: int, regs: dict) -> None:
    """Empacota e envia um datagrama UDP de telemetria."""
    # Garante que min_ttl caiba em um byte sem sinal
    min_ttl = max(0, min(255, int(regs.get("min_ttl", 0))))

    payload = struct.pack(
        TELEMETRY_FORMAT,
        switch_id,
        regs["packet_count"],
        regs["byte_count"],
        regs["icmp_count"],
        min_ttl,
    )
    sock.sendto(payload, controller_addr)
    log.info(
        "Enviado → %s:%d  SW%d pkts=%d bytes=%d icmp=%d ttl=%d",
        *controller_addr, switch_id,
        regs["packet_count"], regs["byte_count"],
        regs["icmp_count"], min_ttl,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Diagnóstico de conexão com o BMv2
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_thrift(thrift_port: int, cli_path: str) -> bool:
    """
    Testa se consegue ler um registrador do BMv2 antes de iniciar o loop.
    Retorna True se a conexão Thrift está funcionando.
    """
    log.info("Diagnosticando conexão Thrift na porta %d...", thrift_port)
    try:
        cli_cmd = find_cli_path(cli_path)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return False

    cmd = (
        f"echo 'register_read reg_packet_count 0' "
        f"| {cli_cmd} --thrift-port {thrift_port}"
    )
    try:
        out = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
        stdout = out.stdout.decode(errors="replace")
        stderr = out.stderr.decode(errors="replace")

        if out.returncode != 0:
            log.error(
                "Diagnóstico Thrift falhou (rc=%d). stderr: %s",
                out.returncode, stderr.strip() or stdout.strip()
            )
            return False

        if "reg_packet_count" in stdout:
            log.info("Conexão Thrift OK. Saída de exemplo: %s", stdout.strip().splitlines()[-1])
            return True

        log.warning("Conexão Thrift retornou saída inesperada: %s", stdout)
        return False

    except Exception as exc:
        log.error("Erro durante diagnóstico Thrift: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────────────────────────────────────

def run(thrift_port: int, switch_id: int, controller_host: str,
        controller_port: int, interval: float, cli_path: str) -> None:

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (controller_host, controller_port)

    log.info(
        "Exportador iniciado | Thrift:%d → UDP %s:%d | intervalo=%.1fs",
        thrift_port, controller_host, controller_port, interval
    )

    # Diagnóstico inicial: aborta se não consegue falar com o BMv2
    if not diagnose_thrift(thrift_port, cli_path):
        log.error("Não foi possível confirmar comunicação com o BMv2. Verifique:")
        log.error("  - se o Mininet/topo_trabalho3.py está rodando;")
        log.error("  - se a porta Thrift está correta (padrão 9090);")
        log.error("  - se simple_switch_CLI está no PATH ou use --cli-path.")
        sock.close()
        sys.exit(1)

    try:
        while True:
            regs = read_all_registers(thrift_port, cli_path)
            send_telemetry(sock, addr, switch_id, regs)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Exportador encerrado.")
    finally:
        sock.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exportador de registradores P4 → UDP")
    parser.add_argument("--thrift-port",     type=int,   default=9090,        help="Porta Thrift do BMv2")
    parser.add_argument("--switch-id",       type=int,   default=1,           help="ID do switch")
    parser.add_argument("--controller",      default="127.0.0.1",             help="IP do controlador")
    parser.add_argument("--controller-port", type=int,   default=9999,        help="Porta UDP do controlador")
    parser.add_argument("--interval",        type=float, default=1.0,         help="Intervalo de exportação (s)")
    parser.add_argument("--cli-path",        default=None,                    help="Caminho do executável simple_switch_CLI")
    parser.add_argument("--debug",           action="store_true",             help="Habilita logs de debug")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    run(
        thrift_port=args.thrift_port,
        switch_id=args.switch_id,
        controller_host=args.controller,
        controller_port=args.controller_port,
        interval=args.interval,
        cli_path=args.cli_path,
    )
