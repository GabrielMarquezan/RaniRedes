#!/usr/bin/env python3
"""
topo_trabalho3.py — Script Mininet para o Trabalho 3
Cria a topologia: h1, h2, h3 conectados a s1 (BMv2 Simple Switch com P4).

Uso (como root ou com sudo):
    sudo python3 topo_trabalho3.py

Requisitos:
    * p4lang/behavioral-model (simple_switch) instalado
    * p4lang/p4c compilador instalado
    * Mininet instalado
    * telemetry.p4 compilado para telemetry.json
"""

import os
import sys
import json
import time
import shutil
import subprocess
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI

# Tenta importar P4Switch do repositório p4lang/tutorials
import pwd

sudo_user = os.environ.get('SUDO_USER')
if sudo_user:
    home = pwd.getpwnam(sudo_user).pw_dir
else:
    home = os.path.expanduser('~')
sys.path.insert(0, os.path.join(home, 'tutorials', 'utils'))

try:
    from p4_mininet import P4Switch, P4Host
except ImportError as exc:
    print("[ERRO] p4_mininet não encontrado:", exc)
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Topologia
# ─────────────────────────────────────────────────────────────────────────────

class TelemetryTopo(Topo):
    """Topologia: h1, h2, h3 → s1 (BMv2)"""

    def build(self, **opts):
        # Hosts
        h1 = self.addHost('h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
        h2 = self.addHost('h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02')
        h3 = self.addHost('h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03')

        # Switch P4 (BMv2)
        # O p4c pode gerar um arquivo telemetry.json ou um diretorio
        # telemetry.json/ contendo o arquivo de mesmo nome. Tenta ambos.
        json_path = 'telemetry.json'
        if os.path.isdir(json_path):
            json_path = os.path.join(json_path, 'telemetry.json')

        s1 = self.addSwitch('s1',
            cls=P4Switch,
            sw_path='simple_switch',
            json_path=json_path,
            thrift_port=9090,
            pcap_dump=False,
            log_console=True,
        )

        # Links
        self.addLink(h1, s1, port2=1)
        self.addLink(h2, s1, port2=2)
        self.addLink(h3, s1, port2=3)


# ─────────────────────────────────────────────────────────────────────────────
# Tabelas de encaminhamento
# ─────────────────────────────────────────────────────────────────────────────

RULES = [
    # switch, tabela, match (LPM), ação, parâmetros
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.1/32', 'MyIngress.forward', ['1']),
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.2/32', 'MyIngress.forward', ['2']),
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.3/32', 'MyIngress.forward', ['3']),
]

def find_cli():
    """Retorna o caminho do simple_switch_CLI ou None se não encontrado."""
    return shutil.which("simple_switch_CLI")


def wait_for_switch(thrift_port: int, timeout: int = 30) -> bool:
    """
    Aguarda o BMv2 ficar pronto para receber comandos via Thrift.
    Retorna True quando a conexão é estabelecida.
    """
    cli = find_cli()
    if not cli:
        info("  [WAIT] simple_switch_CLI não encontrado no PATH\n")
        return False

    cmd = f"echo 'show_version' | {cli} --thrift-port {thrift_port}"
    info(f"  [WAIT] Aguardando BMv2 na porta {thrift_port} (timeout {timeout}s)...\n")
    for i in range(timeout):
        try:
            result = subprocess.run(
                cmd, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=3, check=False
            )
            stdout = result.stdout.decode(errors="replace")
            if result.returncode == 0 and "RuntimeCmd" in stdout:
                info(f"  [WAIT] BMv2 pronto após {i+1}s\n")
                return True
        except Exception:
            pass
        time.sleep(1)
    info("  [WAIT] Timeout aguardando BMv2\n")
    return False


def install_rules(net):
    """Instala regras de encaminhamento via simple_switch_CLI."""
    cli = find_cli()
    if not cli:
        info("  [RULE] ERRO: simple_switch_CLI não encontrado no PATH\n")
        return

    for sw_name, table, match, action, params in RULES:
        sw = net.get(sw_name)
        thrift_port = sw.thrift_port
        param_str = ' '.join(params)
        cmd = (
            f"echo 'table_add {table} {action} {match} => {param_str}' "
            f"| {cli} --thrift-port {thrift_port}"
        )
        info(f"  [RULE] {sw_name}: {table} {match} → {action}({param_str})\n")

        installed = False
        for attempt in range(3):
            try:
                result = subprocess.run(
                    cmd, shell=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=5, check=False
                )
                stdout = result.stdout.decode(errors="replace").strip()
                stderr = result.stderr.decode(errors="replace").strip()
                if result.returncode != 0 or "Error" in stdout or "Invalid" in stdout:
                    info(f"  [RULE ERROR] tentativa {attempt+1}: rc={result.returncode} stderr={stderr} stdout={stdout}\n")
                    time.sleep(0.5)
                else:
                    info(f"  [RULE OK] {sw_name}: {stdout.splitlines()[-1] if stdout else 'ok'}\n")
                    installed = True
                    break
            except Exception as exc:
                info(f"  [RULE EXCEPTION] tentativa {attempt+1}: {exc}\n")
                time.sleep(0.5)

        if not installed:
            info(f"  [RULE FAIL] {sw_name}: não foi possível instalar {match}\n")


def configure_static_arp(net):
    """
    Configura tabelas ARP estáticas nos hosts.

    O programa P4 não encaminha broadcasts ARP (etherType 0x0806), então os
    hosts não conseguem resolver endereços MAC dinamicamente. Com ARP estático
    o ping funciona imediatamente.
    """
    arp_table = {
        'h1': [('10.0.0.2', '00:00:00:00:00:02'),
               ('10.0.0.3', '00:00:00:00:00:03')],
        'h2': [('10.0.0.1', '00:00:00:00:00:01'),
               ('10.0.0.3', '00:00:00:00:00:03')],
        'h3': [('10.0.0.1', '00:00:00:00:00:01'),
               ('10.0.0.2', '00:00:00:00:00:02')],
    }
    for host_name, entries in arp_table.items():
        host = net.get(host_name)
        for ip, mac in entries:
            host.cmd(f'arp -s {ip} {mac}')
            info(f'  [ARP] {host_name}: {ip} -> {mac}\n')


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    setLogLevel('info')

    topo = TelemetryTopo()
    net  = Mininet(topo=topo, controller=None)
    net.start()

    info('\n*** Aguardando BMv2 ficar pronto...\n')
    if not wait_for_switch(9090):
        info('  [AVISO] BMv2 não respondeu; tentando instalar regras mesmo assim...\n')

    info('\n*** Instalando regras de encaminhamento...\n')
    install_rules(net)

    info('\n*** Configurando ARP estático nos hosts...\n')
    configure_static_arp(net)

    info('\n*** Topologia iniciada. Hosts:\n')
    for h in ['h1', 'h2', 'h3']:
        host = net.get(h)
        info(f'    {h}: {host.IP()}\n')

    info('\n*** Para gerar tráfego de teste:\n')
    info('    h1 ping h2\n')
    info('    h1 ping h3 -c 100\n')
    info('    h2 iperf -s &  h1 iperf -c 10.0.0.2\n')
    info('\n*** CLI Mininet disponível. Digite "exit" para sair.\n\n')

    CLI(net)
    net.stop()


if __name__ == '__main__':
    main()
