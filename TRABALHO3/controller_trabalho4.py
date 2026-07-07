#!/usr/bin/env python3
"""
Trabalho 4 — Controlador de Telemetria P4 com Decisão Automática
Disciplina: Redes de Computadores

Descrição:
    Recebe mensagens UDP de telemetria exportadas por switches P4/BMv2,
    decodifica os campos, calcula taxas de pacotes/s e toma decisões
    automáticas: instala ou remove regras na drop_table do switch via
    simple_switch_CLI. Exibe tudo em tempo real via Flask + Flask-SocketIO.

Ciclo de controle:
    switch mede → controlador decide → regra no switch → efeito no tráfego
"""

import argparse
import struct
import socket
import subprocess
import threading
import time
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
# Total: 25 bytes  →  struct format "!IQQIB"
# ─────────────────────────────────────────────────────────────────────────────

TELEMETRY_FORMAT = "!IQQIB"
TELEMETRY_SIZE   = struct.calcsize(TELEMETRY_FORMAT)  # 25 bytes

# ─────────────────────────────────────────────────────────────────────────────
# Configurações da política de mitigação
# ─────────────────────────────────────────────────────────────────────────────

# Taxa que caracteriza ataque (pacotes por segundo)
LIMIT_PKTS_PER_SEC = 120

# IP que será bloqueado quando a taxa for ultrapassada
BLOCKED_SRC_IP = "10.0.0.1"
BLOCKED_SRC_IP_INT = 0x0A000001  # representação numérica para a tabela P4

# Histerese: amostras consecutivas acima/abaixo do limiar
SAMPLES_TO_BLOCK = 2
SAMPLES_TO_UNBLOCK = 5

# Porta Thrift do switch BMv2
SWITCH_THRIFT_PORT = 9090

# ─────────────────────────────────────────────────────────────────────────────
# Estado global
# ─────────────────────────────────────────────────────────────────────────────

# Última leitura de cada switch: { switch_id -> dict }
latest_metrics: dict = {}

# Histórico temporal: { switch_id -> deque([{timestamp, ...}, ...]) }
history: dict = defaultdict(lambda: deque(maxlen=MAX_HISTORY))

# Estado por switch para cálculo de taxa
# { switch_id -> {"last_packet_count": int, "last_time": float, "pkts_per_sec": float} }
rate_state: dict = {}

# Estado da decisão
# { switch_id -> {"consecutive_above": int, "consecutive_below": int,
#                 "blocked": bool, "handle": int|None} }
decision_state: dict = defaultdict(lambda: {
    "consecutive_above": 0,
    "consecutive_below": 0,
    "blocked": False,
    "handle": None,
})

# Lock para acesso thread-safe às estruturas acima
data_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Flask + SocketIO
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = "telemetria-p4-t4"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# ─────────────────────────────────────────────────────────────────────────────
# Decodificação do pacote UDP de telemetria
# ─────────────────────────────────────────────────────────────────────────────

def decode_telemetry(raw: bytes) -> dict | None:
    """
    Decodifica um pacote UDP de telemetria recebido do switch P4.
    Espera exatamente TELEMETRY_SIZE bytes no formato TELEMETRY_FORMAT.
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
            "switch_id":      switch_id,
            "packet_count":   packet_count,
            "byte_count":     byte_count,
            "icmp_count":     icmp_count,
            "min_ttl":        min_ttl & 0xFF,
            "timestamp":      datetime.now().strftime("%H:%M:%S"),
            "timestamp_full": datetime.now().isoformat(),
        }
    except struct.error as exc:
        log.error("Erro ao decodificar pacote: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Política de decisão automática
# ─────────────────────────────────────────────────────────────────────────────

def calculate_rate(switch_id: int, packet_count: int) -> float:
    """
    Calcula a taxa de pacotes/s para um switch a partir do contador acumulado.
    Na primeira amostra retorna 0.0 e apenas inicializa o estado.
    """
    now = time.time()

    if switch_id not in rate_state:
        rate_state[switch_id] = {
            "last_packet_count": packet_count,
            "last_time": now,
            "pkts_per_sec": 0.0,
        }
        return 0.0

    state = rate_state[switch_id]
    delta_pkts = packet_count - state["last_packet_count"]
    delta_time = now - state["last_time"]

    if delta_time > 0:
        pkts_per_sec = delta_pkts / delta_time
    else:
        pkts_per_sec = 0.0

    state["last_packet_count"] = packet_count
    state["last_time"] = now
    state["pkts_per_sec"] = pkts_per_sec

    return pkts_per_sec


def install_drop_rule(src_ip_int: int, thrift_port: int = SWITCH_THRIFT_PORT) -> int | None:
    """
    Adiciona entrada na drop_table. Retorna o handle da regra ou None.
    """
    cmd = (
        f"echo 'table_add MyIngress.drop_table MyIngress.drop {src_ip_int}' "
        f"| simple_switch_CLI --thrift-port {thrift_port}"
    )
    log.info("Instalando regra drop para IP 0x%08X na porta Thrift %d", src_ip_int, thrift_port)
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)
        decoded = out.decode()
        log.info("Saída CLI: %s", decoded.strip())
        # Exemplo de saída: "Entry has been added with handle 5"
        for line in decoded.splitlines():
            if "handle" in line.lower():
                parts = line.split()
                return int(parts[-1].rstrip("."))
    except subprocess.CalledProcessError as exc:
        log.error("Falha ao instalar regra: %s", exc.output.decode(errors="ignore"))
    except Exception as exc:
        log.error("Erro inesperado ao instalar regra: %s", exc)
    return None


def remove_drop_rule(handle: int, thrift_port: int = SWITCH_THRIFT_PORT) -> bool:
    """
    Remove entrada da drop_table pelo handle.
    """
    cmd = (
        f"echo 'table_delete MyIngress.drop_table {handle}' "
        f"| simple_switch_CLI --thrift-port {thrift_port}"
    )
    log.info("Removendo regra drop (handle %d) da porta Thrift %d", handle, thrift_port)
    try:
        subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("Falha ao remover regra: %s", exc.output.decode(errors="ignore"))
    except Exception as exc:
        log.error("Erro inesperado ao remover regra: %s", exc)
    return False


def evaluate_policy(switch_id: int, metrics: dict) -> dict:
    """
    Avalia a política de mitigação para um switch a partir das métricas recebidas.
    Retorna dict com a ação tomada (ou None), taxa e status de bloqueio.
    """
    pkts_per_sec = calculate_rate(switch_id, metrics["packet_count"])
    state = decision_state[switch_id]

    action_taken = None

    if pkts_per_sec > LIMIT_PKTS_PER_SEC:
        state["consecutive_above"] += 1
        state["consecutive_below"] = 0
    else:
        # Enquanto bloqueado, uma taxa 0 significa que o trafego do IP
        # atacante esta sendo dropado no ingress antes de incrementar os
        # contadores. Essas amostras nao indicam retorno ao normal, portanto
        # nao devem contar para o desbloqueio.
        if state["blocked"] and pkts_per_sec == 0:
            pass
        else:
            state["consecutive_below"] += 1
            state["consecutive_above"] = 0

    # Bloqueia
    if not state["blocked"] and state["consecutive_above"] >= SAMPLES_TO_BLOCK:
        handle = install_drop_rule(BLOCKED_SRC_IP_INT, SWITCH_THRIFT_PORT)
        if handle is not None:
            state["blocked"] = True
            state["handle"] = handle
            action_taken = "block"
            log.warning(
                "SW%d BLOQUEADO: pkts/s=%.1f > limiar=%d",
                switch_id, pkts_per_sec, LIMIT_PKTS_PER_SEC
            )

    # Desbloqueia
    elif state["blocked"] and state["consecutive_below"] >= SAMPLES_TO_UNBLOCK:
        if state["handle"] is not None and remove_drop_rule(state["handle"], SWITCH_THRIFT_PORT):
            state["blocked"] = False
            state["handle"] = None
            action_taken = "unblock"
            log.info(
                "SW%d DESBLOQUEADO: pkts/s=%.1f voltou ao normal",
                switch_id, pkts_per_sec
            )

    return {
        "switch_id": switch_id,
        "pkts_per_sec": round(pkts_per_sec, 2),
        "blocked": state["blocked"],
        "action": action_taken,
        "limit": LIMIT_PKTS_PER_SEC,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Receptor UDP (roda em thread separada)
# ─────────────────────────────────────────────────────────────────────────────

def udp_receiver():
    """
    Escuta na porta UDP TELEMETRY_PORT, decodifica cada datagrama recebido,
    atualiza as estruturas de estado global e executa a política de decisão.
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

            # Lógica de controle automático
            action_result = evaluate_policy(sid, metrics)

            with data_lock:
                latest_metrics[sid] = metrics
                history[sid].append(metrics)

            # Envia evento para todos os clientes conectados ao dashboard
            # Fora de um handler de request, emit() envia para todos por padrao.
            socketio.emit("telemetry_update", {
                "switch_id": sid,
                "metrics":   metrics,
                "history":   list(history[sid]),
                "policy":    action_result,
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


@app.route("/api/status")
def api_status():
    """Health-check: retorna estado do receptor UDP e switches conhecidos."""
    with data_lock:
        return jsonify({
            "udp_host": TELEMETRY_HOST,
            "udp_port": TELEMETRY_PORT,
            "switches": {
                sid: {
                    "metrics": m,
                    "policy": {
                        "pkts_per_sec": rate_state.get(sid, {}).get("pkts_per_sec", 0.0),
                        "blocked": decision_state[sid]["blocked"],
                        "limit": LIMIT_PKTS_PER_SEC,
                    },
                }
                for sid, m in latest_metrics.items()
            },
            "switch_count": len(latest_metrics),
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
                "policy": {
                    "switch_id": sid,
                    "pkts_per_sec": rate_state.get(sid, {}).get("pkts_per_sec", 0.0),
                    "blocked": decision_state[sid]["blocked"],
                    "limit": LIMIT_PKTS_PER_SEC,
                },
            }
            for sid, m in latest_metrics.items()
        }
    emit("initial_state", snapshot)


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Controlador de telemetria P4 com decisão automática")
    parser.add_argument("--udp-port",  type=int, default=TELEMETRY_PORT, help="Porta UDP de telemetria")
    parser.add_argument("--http-port", type=int, default=5000,           help="Porta HTTP do dashboard")
    parser.add_argument("--debug",     action="store_true",             help="Habilita logs de debug")
    args = parser.parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    # Permite sobrescrever a porta UDP via CLI
    TELEMETRY_PORT = args.udp_port

    # Inicia receptor UDP em thread daemon (encerra junto com o processo)
    recv_thread = threading.Thread(target=udp_receiver, daemon=True)
    recv_thread.start()

    # Pequena pausa para garantir que o socket UDP esteja vinculado antes do HTTP subir
    time.sleep(0.2)

    log.info("Dashboard disponível em http://0.0.0.0:%d", args.http_port)
    log.info("Receptor UDP escutando em %s:%d", TELEMETRY_HOST, TELEMETRY_PORT)

    # allow_unsafe_werkzeug só existe em versões mais antigas do Flask-SocketIO
    run_kwargs = {"host": "0.0.0.0", "port": args.http_port, "debug": False}
    try:
        socketio.run(app, **run_kwargs, allow_unsafe_werkzeug=True)
    except TypeError:
        socketio.run(app, **run_kwargs)
