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
import shutil
import time
import argparse
import signal
import subprocess

TARGET_IP = "10.0.0.2"
NORMAL_DURATION = 10      # segundos
ATTACK_DURATION = 15      # segundos
RECOVERY_DURATION = 10    # segundos
ATTACK_INTERVAL = 0.0     # 0.0 = ping -f (flood); >0 = ping -i <intervalo>


def kill_pings():
    """Encerra processos de ping pendentes dentro do host."""
    os.system("pkill -f 'ping ' || true")
    os.system("pkill -f 'hping3 ' || true")
    time.sleep(0.5)


def run_phase(name: str, cmd: list[str], duration: int):
    """
    Executa um comando por um tempo determinado e depois o encerra.
    Retorna o código de retorno do processo.
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

    # Garante que nenhum ping residual continue rodando
    kill_pings()


def build_attack_cmd(target: str, interval: float, packet_size: int | None) -> list[str]:
    """
    Monta o comando de ataque. Prefere hping3 se disponível (mais previsível);
    caso contrário usa ping -f ou ping -i.
    """
    if shutil.which("hping3"):
        cmd = ["hping3", "--icmp", "--flood", target]
        if interval > 0:
            # hping3 --interval aceita microssegundos (1 = 1us); usamos --fast ou count
            cmd = ["hping3", "--icmp", "--fast", target]
        if packet_size:
            cmd.extend(["-d", str(packet_size)])
        return cmd

    # Fallback para ping
    if interval > 0:
        cmd = ["ping", "-i", str(interval), target]
    else:
        cmd = ["ping", "-f", target]
    if packet_size:
        cmd.extend(["-s", str(packet_size)])
    return cmd


def main():
    parser = argparse.ArgumentParser(
        description="Gerador de tráfego normal/ataque/recuperação para o Trabalho 4"
    )
    parser.add_argument("--target", default=TARGET_IP, help="IP de destino do tráfego")
    parser.add_argument("--normal", type=int, default=NORMAL_DURATION, help="Duração da fase normal (s)")
    parser.add_argument("--attack", type=int, default=ATTACK_DURATION, help="Duração da fase de ataque (s)")
    parser.add_argument("--recovery", type=int, default=RECOVERY_DURATION, help="Duração da fase de recuperação (s)")
    parser.add_argument("--attack-interval", type=float, default=ATTACK_INTERVAL,
                        help="Intervalo entre pacotes de ataque em segundos (0 = flood máximo)")
    parser.add_argument("--size", type=int, default=None, help="Tamanho do payload ICMP (opcional)")
    args = parser.parse_args()

    print("Iniciando gerador de tráfego do Trabalho 4")
    print(f"Alvo: {args.target}")
    print(f"Configuração: normal={args.normal}s, ataque={args.attack}s, recuperação={args.recovery}s")
    print(f"Ataque: intervalo={args.attack_interval}s | ferramenta={'hping3' if shutil.which('hping3') else 'ping'}")

    # Garante limpeza ao receber SIGINT
    signal.signal(signal.SIGINT, lambda _s, _f: (kill_pings(), exit(0)))

    # Fase normal: ping a cada 1 segundo
    run_phase("NORMAL", ["ping", "-i", "1", args.target], args.normal)

    # Fase de ataque: flood de pacotes ICMP
    attack_cmd = build_attack_cmd(args.target, args.attack_interval, args.size)
    run_phase("ATAQUE", attack_cmd, args.attack)

    # Fase de recuperação: ping lento novamente
    run_phase("RECUPERAÇÃO", ["ping", "-i", "1", args.target], args.recovery)

    print("\n=== Gerador finalizado ===")


if __name__ == "__main__":
    main()
