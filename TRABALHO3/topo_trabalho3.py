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
from mininet.net import Mininet
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI

# Tenta importar P4Switch do repositório p4lang/tutorials
try:
    sys.path.insert(0, os.path.expanduser('~/tutorials/utils'))
    from p4_mininet import P4Switch, P4Host  # type: ignore
except ImportError:
    print("[ERRO] p4_mininet não encontrado.")
    print("       Clone https://github.com/p4lang/tutorials e ajuste o path.")
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
        s1 = self.addSwitch('s1',
            cls=P4Switch,
            sw_path='simple_switch',
            json_path='telemetry.json',
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

def install_rules(net):
    """Instala regras de encaminhamento via simple_switch_CLI."""
    import subprocess
    for sw_name, table, match, action, params in RULES:
        sw = net.get(sw_name)
        thrift_port = sw.thrift_port
        param_str = ' '.join(params)
        cmd = (
            f"echo 'table_add {table} {action} {match} => {param_str}' "
            f"| simple_switch_CLI --thrift-port {thrift_port}"
        )
        info(f"  [RULE] {sw_name}: {table} {match} → {action}({param_str})\n")
        subprocess.run(cmd, shell=True, check=False)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    setLogLevel('info')

    topo = TelemetryTopo()
    net  = Mininet(topo=topo, controller=None)
    net.start()

    info('\n*** Instalando regras de encaminhamento...\n')
    install_rules(net)

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
