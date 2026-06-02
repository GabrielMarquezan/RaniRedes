#!/usr/bin/env python3
"""
Trabalho 3 — Controlador de Telemetria P4
Disciplina: Redes de Computadores
Descrição: Recebe mensagens UDP de telemetria exportadas por switches P4,
           decodifica os campos, mantém histórico e exibe em tempo real
           via Flask + Flask-SocketIO.
"""

import struct
import socket
import threading
import time
import json
import logging
from datetime import datetime
from collections import defaultdict, deque
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit

# ─────────────────────────────────────────────────────────────────────────────
# Configuração geral
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Endereço e porta UDP onde o switch envia as mensagens de telemetria
TELEMETRY_HOST = "0.0.0.0"
TELEMETRY_PORT = 9999

# Tamanho máximo do histórico por switch (amostras)
MAX_HISTORY = 60

# ─────────────────────────────────────────────────────────────────────────────
# Formato do pacote de telemetria (deve ser idêntico ao que o P4 exporta)
#
# Campos (big-endian, sem padding):
#   switch_id    : uint32  (4 bytes)
#   packet_count : uint64  (8 bytes)
#   byte_count   : uint64  (8 bytes)
#   icmp_count   : uint32  (4 bytes)
#   min_ttl      : uint8   (1 byte)
#
# Total: 25 bytes  →  struct format "!IQQIb"
# Nota: ajuste aqui se o seu programa P4 exportar campos em ordem/tamanho
#       diferentes. Consulte seu header de telemetria no arquivo .p4.
# ─────────────────────────────────────────────────────────────────────────────

TELEMETRY_FORMAT = "!IQQI B"  # big-endian: uint32, uint64, uint64, uint32, uint8
TELEMETRY_FORMAT = "!IQQIB"
TELEMETRY_SIZE   = struct.calcsize(TELEMETRY_FORMAT)  # 25 bytes

# ─────────────────────────────────────────────────────────────────────────────
# Estado global — armazena métricas por switch_id
# ─────────────────────────────────────────────────────────────────────────────

# Última leitura de cada switch: { switch_id -> dict }
latest_metrics: dict = {}

# Histórico temporal: { switch_id -> deque([{timestamp, ...}, ...]) }
history: dict = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

# Lock para acesso thread-safe às estruturas acima
data_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Flask + SocketIO
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = "telemetria-p4-t3"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ─────────────────────────────────────────────────────────────────────────────
# Decodificação do pacote UDP de telemetria
# ─────────────────────────────────────────────────────────────────────────────

def decode_telemetry(raw: bytes) -> dict | None:
    """
    Decodifica um pacote UDP de telemetria recebido do switch P4.

    Espera exatamente TELEMETRY_SIZE bytes no formato TELEMETRY_FORMAT.
    Retorna um dicionário com os campos decodificados ou None em caso de erro.

    Adaptação para INT (In-band Network Telemetry):
        Se a telemetria vier embutida em pacotes clonados, será necessário
        receber o pacote Ethernet/IP completo (via socket AF_PACKET ou
        scapy) e extrair o payload INT antes de chamar struct.unpack.
        Substitua a linha raw[:TELEMETRY_SIZE] pelo slice correto do
        payload INT após remover os cabeçalhos Ethernet, IP e UDP.
    """
    if len(raw) < TELEMETRY_SIZE:
        log.warning(
            "Pacote muito curto: %d bytes (esperado %d)", len(raw), TELEMETRY_SIZE
        )
        return None

    try:
        switch_id, packet_count, byte_count, icmp_count, min_ttl = struct.unpack(
            TELEMETRY_FORMAT, raw[:TELEMETRY_SIZE]
        )
        return {
            "switch_id":    switch_id,
            "packet_count": packet_count,
            "byte_count":   byte_count,
            "icmp_count":   icmp_count,
            "min_ttl":      min_ttl & 0xFF,  # garante unsigned
            "timestamp":    datetime.now().strftime("%H:%M:%S"),
            "timestamp_full": datetime.now().isoformat(),
        }
    except struct.error as exc:
        log.error("Erro ao decodificar pacote: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Receptor UDP (roda em thread separada)
# ─────────────────────────────────────────────────────────────────────────────

def udp_receiver():
    """
    Escuta na porta UDP TELEMETRY_PORT, decodifica cada datagrama recebido
    e atualiza as estruturas de estado global.  Notifica o frontend via
    SocketIO após cada atualização.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((TELEMETRY_HOST, TELEMETRY_PORT))
    log.info("Receptor UDP ativo em %s:%d", TELEMETRY_HOST, TELEMETRY_PORT)

    while True:
        try:
            raw, addr = sock.recvfrom(4096)
            log.debug("Datagrama recebido de %s (%d bytes)", addr, len(raw))

            metrics = decode_telemetry(raw)
            if metrics is None:
                continue

            sid = metrics["switch_id"]
            log.info(
                "Switch %d → pkts=%d bytes=%d icmp=%d ttl=%d",
                sid, metrics["packet_count"], metrics["byte_count"],
                metrics["icmp_count"], metrics["min_ttl"],
            )

            with data_lock:
                latest_metrics[sid] = metrics
                history[sid].append(metrics)

            # Envia evento para todos os clientes conectados ao dashboard
            socketio.emit("telemetry_update", {
                "switch_id": sid,
                "metrics":   metrics,
                "history":   list(history[sid]),
            })

        except Exception as exc:
            log.error("Erro no receptor UDP: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Rotas Flask
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Página principal do dashboard."""
    return render_template("index.html")


@app.route("/api/metrics")
def api_metrics():
    """Retorna as métricas atuais de todos os switches em JSON."""
    with data_lock:
        return jsonify({
            "switches": latest_metrics,
            "count":    len(latest_metrics),
        })


@app.route("/api/history/<int:switch_id>")
def api_history(switch_id: int):
    """Retorna o histórico de amostras de um switch específico."""
    with data_lock:
        return jsonify({
            "switch_id": switch_id,
            "samples":   list(history.get(switch_id, [])),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Evento SocketIO: cliente solicita estado atual ao conectar
# ─────────────────────────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    log.info("Cliente conectado ao dashboard")
    with data_lock:
        snapshot = {
            sid: {
                "metrics": m,
                "history": list(history[sid]),
            }
            for sid, m in latest_metrics.items()
        }
    emit("initial_state", snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Inicia receptor UDP em thread daemon (encerra junto com o processo)
    recv_thread = threading.Thread(target=udp_receiver, daemon=True)
    recv_thread.start()

    log.info("Dashboard disponível em http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
