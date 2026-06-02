#!/usr/bin/env python3
"""
telemetry_receiver.py — Módulo de recepção de telemetria P4
Pode ser usado de forma standalone (imprime no terminal) ou
importado pelo controller_trabalho3.py.

Uso standalone:
    python3 telemetry_receiver.py [--port 9999]
"""

import struct
import socket
import argparse
import logging
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Formato do pacote de telemetria — deve espelhar exatamente o header P4.
# Se o seu programa P4 exportar campos em ordem diferente, ajuste aqui.
#
# Formato atual (big-endian):
#   switch_id    uint32   4 bytes
#   packet_count uint64   8 bytes
#   byte_count   uint64   8 bytes
#   icmp_count   uint32   4 bytes
#   min_ttl      uint8    1 byte
#
TELEMETRY_FORMAT = "!IQQIB"
TELEMETRY_SIZE   = struct.calcsize(TELEMETRY_FORMAT)


def decode_packet(raw: bytes) -> dict | None:
    """
    Decodifica um datagrama UDP de telemetria.

    Parâmetros
    ----------
    raw : bytes
        Payload bruto recebido via UDP.

    Retorna
    -------
    dict com campos decodificados ou None se o pacote for inválido.

    Notas de adaptação
    ------------------
    * Clonagem de pacotes (mirroring):
        O switch clona o pacote original e adiciona metadados P4.
        Nesse caso, receba com AF_PACKET ou scapy, extraia o encapsulamento
        e passe apenas os bytes do relatório de telemetria para esta função.

    * INT (In-band Network Telemetry):
        O cabeçalho INT é inserido entre IP e TCP/UDP.
        Use scapy para parsear e extrair o stack de metadados INT.

    * gRPC/gNMI (streaming):
        Troque o socket UDP por um channel gRPC e desserialize protobuf.
        Esta função pode ser substituída por um deserializador protobuf.
    """
    if len(raw) < TELEMETRY_SIZE:
        log.warning("Pacote descartado: %d bytes < %d esperados", len(raw), TELEMETRY_SIZE)
        return None

    try:
        switch_id, packet_count, byte_count, icmp_count, min_ttl = struct.unpack(
            TELEMETRY_FORMAT, raw[:TELEMETRY_SIZE]
        )
    except struct.error as exc:
        log.error("Falha no unpack: %s", exc)
        return None

    return {
        "switch_id":      switch_id,
        "packet_count":   packet_count,
        "byte_count":     byte_count,
        "icmp_count":     icmp_count,
        "min_ttl":        min_ttl & 0xFF,
        "timestamp":      datetime.now().strftime("%H:%M:%S"),
        "timestamp_full": datetime.now().isoformat(),
        "source_ip":      None,   # preenchido pelo chamador quando disponível
    }


def print_metrics(metrics: dict, source: tuple) -> None:
    """Exibe as métricas de forma legível no terminal."""
    src_ip, src_port = source
    print(
        f"\n{'─'*55}\n"
        f"  Fonte:        {src_ip}:{src_port}\n"
        f"  Switch ID:    {metrics['switch_id']}\n"
        f"  Pacotes:      {metrics['packet_count']}\n"
        f"  Bytes:        {metrics['byte_count']}\n"
        f"  ICMP:         {metrics['icmp_count']}\n"
        f"  TTL mínimo:   {metrics['min_ttl']}\n"
        f"  Timestamp:    {metrics['timestamp']}\n"
        f"{'─'*55}"
    )


def listen(host: str = "0.0.0.0", port: int = 9999,
           callback=None) -> None:
    """
    Loop principal de recepção.

    Parâmetros
    ----------
    host : str
        Interface de escuta (padrão: todas).
    port : int
        Porta UDP.
    callback : callable | None
        Função chamada com (metrics: dict, source: tuple) a cada pacote
        válido. Se None, imprime no terminal.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    log.info("Aguardando telemetria em udp://%s:%d  (struct size=%d bytes)",
             host, port, TELEMETRY_SIZE)

    while True:
        try:
            raw, addr = sock.recvfrom(4096)
            metrics = decode_packet(raw)
            if metrics is None:
                continue
            metrics["source_ip"] = addr[0]

            if callback:
                callback(metrics, addr)
            else:
                print_metrics(metrics, addr)

        except KeyboardInterrupt:
            log.info("Receptor encerrado pelo usuário.")
            break
        except Exception as exc:
            log.error("Erro inesperado: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Execução standalone
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Receptor de telemetria P4 (UDP)")
    parser.add_argument("--host", default="0.0.0.0", help="Interface de escuta")
    parser.add_argument("--port", type=int, default=9999, help="Porta UDP")
    args = parser.parse_args()
    listen(host=args.host, port=args.port)
