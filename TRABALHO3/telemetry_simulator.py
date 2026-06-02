#!/usr/bin/env python3
"""
telemetry_simulator.py — Simulador de telemetria P4
Envia pacotes UDP falsos com o mesmo formato do switch P4 real,
permitindo testar o dashboard sem precisar de Mininet/BMv2.

Uso:
    python3 telemetry_simulator.py
    python3 telemetry_simulator.py --switches 3 --interval 1.5 --port 9999
"""

import struct
import socket
import time
import random
import argparse
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SIMULATOR] %(message)s"
)
log = logging.getLogger(__name__)

# Deve ser idêntico ao formato em telemetry_receiver.py / controller_trabalho3.py
TELEMETRY_FORMAT = "!IQQIB"
TELEMETRY_SIZE   = struct.calcsize(TELEMETRY_FORMAT)


class SwitchState:
    """
    Mantém o estado acumulado de um switch simulado.
    A cada tick, incrementa os contadores de forma realista:
    - rajadas de ICMP simulam pings entre hosts
    - o byte_count cresce proporcionalmente ao packet_count
    """

    def __init__(self, switch_id: int):
        self.switch_id    = switch_id
        self.packet_count = 0
        self.byte_count   = 0
        self.icmp_count   = 0
        self.min_ttl      = 64
        self._burst_mode  = False
        self._burst_ticks = 0

    def tick(self) -> dict:
        """Avança o estado e retorna as métricas atuais."""
        # Decide se entra em modo de rajada (simula tráfego intenso)
        if not self._burst_mode and random.random() < 0.15:
            self._burst_mode  = True
            self._burst_ticks = random.randint(3, 8)

        if self._burst_mode:
            pkt_delta  = random.randint(80, 200)
            icmp_delta = random.randint(10, 40)
            self._burst_ticks -= 1
            if self._burst_ticks <= 0:
                self._burst_mode = False
        else:
            pkt_delta  = random.randint(5, 30)
            icmp_delta = random.randint(0, 5)

        byte_delta = pkt_delta * random.randint(64, 1500)

        self.packet_count += pkt_delta
        self.byte_count   += byte_delta
        self.icmp_count   += icmp_delta
        self.min_ttl       = random.choice([64, 128]) - random.randint(0, 5)

        return {
            "switch_id":    self.switch_id,
            "packet_count": self.packet_count,
            "byte_count":   self.byte_count,
            "icmp_count":   self.icmp_count,
            "min_ttl":      max(1, self.min_ttl),
        }


def pack_metrics(m: dict) -> bytes:
    """Serializa as métricas no formato binário esperado pelo controlador."""
    return struct.pack(
        TELEMETRY_FORMAT,
        m["switch_id"],
        m["packet_count"],
        m["byte_count"],
        m["icmp_count"],
        m["min_ttl"],
    )


def run_simulator(
    host: str       = "127.0.0.1",
    port: int       = 9999,
    num_switches: int = 1,
    interval: float  = 1.0,
):
    """
    Loop principal do simulador.

    Parâmetros
    ----------
    host         : destino UDP (o controlador)
    port         : porta UDP do controlador
    num_switches : quantos switches simular (IDs 1..num_switches)
    interval     : segundos entre envios por switch
    """
    sock     = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    switches = [SwitchState(i + 1) for i in range(num_switches)]

    log.info(
        "Simulando %d switch(es) → udp://%s:%d  (intervalo=%.1fs)",
        num_switches, host, port, interval,
    )
    log.info("Pressione Ctrl+C para parar.")

    try:
        while True:
            for sw in switches:
                metrics = sw.tick()
                payload = pack_metrics(metrics)
                sock.sendto(payload, (host, port))
                log.info(
                    "SW%d  pkts=%-8d bytes=%-12d icmp=%-5d ttl=%d",
                    metrics["switch_id"],
                    metrics["packet_count"],
                    metrics["byte_count"],
                    metrics["icmp_count"],
                    metrics["min_ttl"],
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Simulador encerrado.")
    finally:
        sock.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulador de telemetria P4")
    parser.add_argument("--host",     default="127.0.0.1", help="IP do controlador")
    parser.add_argument("--port",     type=int,   default=9999,  help="Porta UDP")
    parser.add_argument("--switches", type=int,   default=1,     help="Nº de switches")
    parser.add_argument("--interval", type=float, default=1.0,   help="Intervalo (s)")
    args = parser.parse_args()

    run_simulator(
        host=args.host,
        port=args.port,
        num_switches=args.switches,
        interval=args.interval,
    )
