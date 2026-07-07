#!/usr/bin/env python3
"""
p4_register_exporter.py — Exportador de registradores P4 via Thrift
Lê os registradores do BMv2 Simple Switch via simple_switch_CLI e envia os
dados como pacotes UDP ao controlador (controller_trabalho4.py).

Executar FORA do Mininet, no host root (mesmo namespace do BMv2):
    python3 p4_register_exporter.py --thrift-port 9090 --controller 127.0.0.1
"""

import re
import shutil
import socket
import struct
import subprocess
import time
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EXPORTER] %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Leitura dos registradores via subprocess (simple_switch_CLI)
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
    Executa register_read via simple_switch_CLI e retorna o valor lido.
    Em caso de falha, retorna 0.
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

        if out.returncode != 0:
            log.error(
                "simple_switch_CLI falhou para %s (rc=%d): %s",
                reg_name, out.returncode, stdout.strip()
            )
            return 0

        for line in stdout.splitlines():
            if reg_name in line:
                match = re.search(r"[\:\=]\s*(\d+)", line)
                if match:
                    return int(match.group(1))

        log.warning("Não foi possível fazer parse do valor de %s", reg_name)
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
    args = parser.parse_args()

    run(
        thrift_port=args.thrift_port,
        switch_id=args.switch_id,
        controller_host=args.controller,
        controller_port=args.controller_port,
        interval=args.interval,
        cli_path=args.cli_path,
    )
