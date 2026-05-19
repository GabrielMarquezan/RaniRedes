from scapy.all import *
from scapy.layers.l2 import Ether
from datetime import datetime

TYPE_TELEMETRY = 0x88B5

class Telemetry(Packet):
    name = "TelemetryHeader"
    fields = [
        IntField("packet_count", 0),
        IntField("byte_count", 0),
        IntField("icmp_count", 0),
        ByteField("min_ttl", 0)
    ]

bind_layers(Ether, Telemetry, type=TYPE_TELEMETRY)

def handle_pkt(pkt):
    # Confirma dupla verificação se a camada existe no pacote
    if Telemetry in pkt:
        telemetry_layer = pkt[Telemetry]
        now = datetime.now().strftime("%H:%M:%S")
        
        print(f"[{now}] Métricas de tráfego de rede:")
        print(f"    |- Total de pacotes     : {telemetry_layer.packet_count}")
        print(f"    |- Total de bytes       : {telemetry_layer.byte_count}")
        print(f"    |- Pacotes ICMP         : {telemetry_layer.icmp_count}")
        print(f"    -- TTL mínimo           : {telemetry_layer.min_ttl}")
        print("-" * 50)

def main():
    interface = "h3-eth0" 
    print(f"Iniciando coletor de telemetria na interface {interface}.")
    print(f"Aguardando pacotes com EtherType {hex(TYPE_TELEMETRY)}.\n")
    print("-" * 50)
    
    sniff(
        iface=interface, 
        prn=handle_pkt, 
        filter=f"ether proto {TYPE_TELEMETRY}", 
        store=False
    )

if __name__ == '__main__':
    main()