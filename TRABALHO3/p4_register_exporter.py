#!/usr/bin/env python3
"""
p4_register_exporter.py — Exportador de registradores P4 via Thrift
Lê os registradores do BMv2 Simple Switch via interface Thrift e
envia os dados como pacotes UDP ao controlador (controller_trabalho3.py).

Executar DENTRO do ambiente Mininet, no contexto do switch s1:
    python3 p4_register_exporter.py --thrift-port 9090 --controller 127.0.0.1:9999

Dependência: runtime_CLI.py do repositório p4lang/behavioral-model
  ou usar a lib bm_runtime que vem com o BMv2.
"""

import socket
import struct
import time
import argparse
import logging
import sys

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

import subprocess

def read_register_cli(reg_name: str, thrift_port: int) -> int:
    """
    Lê um registrador P4 via simple_switch_CLI.
    Retorna o valor inteiro no índice 0.
    """
    cmd = f"echo 'register_read {reg_name} 0' | simple_switch_CLI --thrift-port {thrift_port}"
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, timeout=3)
        # A saída tem formato:  reg_name[0]= VALUE
        for line in out.decode().splitlines():
            if reg_name in line and '=' in line:
                return int(line.split('=')[-1].strip())
    except Exception as exc:
        log.error("Erro ao ler %s: %s", reg_name, exc)
    return 0


def read_all_registers(thrift_port: int) -> dict:
    """Lê todos os 4 registradores de telemetria."""
    return {
        "packet_count": read_register_cli("reg_packet_count", thrift_port),
        "byte_count":   read_register_cli("reg_byte_count",   thrift_port),
        "icmp_count":   read_register_cli("reg_icmp_count",   thrift_port),
        "min_ttl":      read_register_cli("reg_min_ttl",      thrift_port),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Empacotamento e envio UDP
# ─────────────────────────────────────────────────────────────────────────────

TELEMETRY_FORMAT = "!IQQIb"

def send_telemetry(sock: socket.socket, controller_addr: tuple,
                   switch_id: int, regs: dict) -> None:
    """Empacota e envia um datagrama UDP de telemetria."""
    payload = struct.pack(
        TELEMETRY_FORMAT,
        switch_id,
        regs["packet_count"],
        regs["byte_count"],
        regs["icmp_count"],
        regs["min_ttl"],
    )
    sock.sendto(payload, controller_addr)
    log.info(
        "Enviado → %s:%d  SW%d pkts=%d bytes=%d icmp=%d ttl=%d",
        *controller_addr, switch_id,
        regs["packet_count"], regs["byte_count"],
        regs["icmp_count"], regs["min_ttl"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Loop principal
# ─────────────────────────────────────────────────────────────────────────────

def run(thrift_port: int, switch_id: int, controller_host: str,
        controller_port: int, interval: float) -> None:

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (controller_host, controller_port)

    log.info(
        "Exportador iniciado | Thrift:%d → UDP %s:%d | intervalo=%.1fs",
        thrift_port, controller_host, controller_port, interval
    )

    try:
        while True:
            regs = read_all_registers(thrift_port)
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
    parser.add_argument("--thrift-port",    type=int,   default=9090,        help="Porta Thrift do BMv2")
    parser.add_argument("--switch-id",      type=int,   default=1,           help="ID do switch")
    parser.add_argument("--controller",     default="127.0.0.1",             help="IP do controlador")
    parser.add_argument("--controller-port",type=int,   default=9999,        help="Porta UDP do controlador")
    parser.add_argument("--interval",       type=float, default=1.0,         help="Intervalo de exportação (s)")
    args = parser.parse_args()

    run(
        thrift_port=args.thrift_port,
        switch_id=args.switch_id,
        controller_host=args.controller,
        controller_port=args.controller_port,
        interval=args.interval,
    )
