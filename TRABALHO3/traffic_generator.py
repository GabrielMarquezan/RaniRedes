#!/usr/bin/env python3
"""
traffic_generator.py — Gerador de tráfego para o Trabalho 4
Deve ser executado DENTRO do host h1 no Mininet:

    mininet> h1 python3 traffic_generator.py

Fases:
1. Normal: ping lento para h2.
2. Ataque: flood de ICMP (ping -f) para h2.
3. Recuperação: volta ao ping lento.

O objetivo é gerar uma condição de tráfego normal, provocar o bloqueio
automático pelo controlador (quando pkts/s > limiar) e, em seguida,
demonstrar o desbloqueio automático quando o tráfego volta ao normal.
"""

import os
import time
import argparse
import signal
import subprocess

TARGET_IP = "10.0.0.2"
NORMAL_DURATION = 10      # segundos
ATTACK_DURATION = 15      # segundos
RECOVERY_DURATION = 10    # segundos


def kill_pings():
    """Encerra processos de ping pendentes dentro do host."""
    os.system("pkill -f 'ping ' || true")
    time.sleep(0.5)


def run_phase(name: str, cmd: list[str], duration: int):
    """
    Executa um comando por um tempo determinado e depois o encerra.
    """
    print(f"\n=== FASE: {name} ({duration}s) ===")
    print(f"Comando: {' '.join(cmd)}")

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=duration)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    kill_pings()


def main():
    parser = argparse.ArgumentParser(
        description="Gerador de tráfego normal/ataque/recuperação para o Trabalho 4"
    )
    parser.add_argument("--target", default=TARGET_IP, help="IP de destino do tráfego")
    parser.add_argument("--normal", type=int, default=NORMAL_DURATION, help="Duração da fase normal (s)")
    parser.add_argument("--attack", type=int, default=ATTACK_DURATION, help="Duração da fase de ataque (s)")
    parser.add_argument("--recovery", type=int, default=RECOVERY_DURATION, help="Duração da fase de recuperação (s)")
    args = parser.parse_args()

    print("Iniciando gerador de tráfego do Trabalho 4")
    print(f"Alvo: {args.target}")
    print(f"Configuração: normal={args.normal}s, ataque={args.attack}s, recuperação={args.recovery}s")

    # Garante limpeza ao receber SIGINT
    signal.signal(signal.SIGINT, lambda _s, _f: (kill_pings(), exit(0)))

    # Fase normal: ping a cada 1 segundo
    run_phase("NORMAL", ["ping", "-i", "1", args.target], args.normal)

    # Fase de ataque: flood de pacotes ICMP
    # Nota: ping -f requer root, mas dentro do host Mininet normalmente é permitido.
    run_phase("ATAQUE", ["ping", "-f", args.target], args.attack)

    # Fase de recuperação: ping lento novamente
    run_phase("RECUPERAÇÃO", ["ping", "-i", "1", args.target], args.recovery)

    print("\n=== Gerador finalizado ===")


if __name__ == "__main__":
    main()
