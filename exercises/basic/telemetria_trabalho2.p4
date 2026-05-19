#include <core.p4>
#include <v1model.p4>

const bit<16> TYPE_IPV4 = 0x0800;
const bit<16> TYPE_TELEMETRY = 0x88B5; // EtherType customizado para o coletor
const bit<8>  IP_PROTO_ICMP = 1;

typedef bit<9>  egressSpec_t;
typedef bit<48> macAddr_t;
typedef bit<32> ip4Addr_t;

header ethernet_t {
    macAddr_t dstAddr;
    macAddr_t srcAddr;
    bit<16>   etherType;
}

header ipv4_t {
    bit<4>    version;
    bit<4>    ihl;
    bit<8>    diffserv;
    bit<16>   totalLen;
    bit<16>   identification;
    bit<3>    flags;
    bit<13>   fragOffset;
    bit<8>    ttl;
    bit<8>    protocol;
    bit<16>   hdrChecksum;
    ip4Addr_t srcAddr;
    ip4Addr_t dstAddr;
}

// Cabeçalho customizado de telemetria
header telemetry_t {
    bit<32> packet_count;
    bit<32> byte_count;
    bit<32> icmp_count;
    bit<8>  min_ttl;
}

// Metadados para transportar o estado do Ingress para o Egress no pacote clonado
struct metadata {
    bit<32> telemetry_pkt_count;
    bit<32> telemetry_byte_count;
    bit<32> telemetry_icmp_count;
    bit<8>  telemetry_min_ttl;
}

struct headers {
    ethernet_t ethernet;
    ipv4_t     ipv4;
    telemetry_t telemetry;
}

parser MyParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) { 
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition accept;
    }
}

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { } 
}

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    // Registos de estado globais (tamanho 1 para manter o valor atual da janela)
    register<bit<32>>(1) reg_pkt_count;
    register<bit<32>>(1) reg_byte_count;
    register<bit<32>>(1) reg_icmp_count;
    register<bit<8>>(1)  reg_min_ttl;
   
    action drop() {
        mark_to_drop(standard_metadata);
    }

    action ipv4_forward(macAddr_t dstAddr, egressSpec_t port) {     
        standard_metadata.egress_spec = port;
        hdr.ethernet.srcAddr = hdr.ethernet.dstAddr;
        hdr.ethernet.dstAddr = dstAddr;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    table ipv4_lpm {
        key = {
            hdr.ipv4.dstAddr: lpm;
        }
        actions = {
            ipv4_forward;
            drop;
        }
        size = 1024;
        default_action = drop();
    }

    apply {  
        if (hdr.ipv4.isValid()) {
            // Variáveis locais para manipular o estado
            bit<32> pkt_count;
            bit<32> byte_count;
            bit<32> icmp_count;
            bit<8> min_ttl;

            // 1. Leitura do estado atual
            reg_pkt_count.read(pkt_count, 0);
            reg_byte_count.read(byte_count, 0);
            reg_icmp_count.read(icmp_count, 0);
            reg_min_ttl.read(min_ttl, 0);

            // 2. Atualização das métricas
            pkt_count = pkt_count + 1;
            byte_count = byte_count + (bit<32>)standard_metadata.packet_length;
            
            if (hdr.ipv4.protocol == IP_PROTO_ICMP) {
                icmp_count = icmp_count + 1;
            }

            // O TTL mínimo começa a 0 ou é atualizado se for menor que o valor guardado
            if (min_ttl == 0 || hdr.ipv4.ttl < min_ttl) {
                min_ttl = hdr.ipv4.ttl;
            }

            // 3. Verificação da Janela (N = 10 pacotes)
            if (pkt_count == 10) {
                // Prepara os metadados para serem passados ao clone
                meta.telemetry_pkt_count = pkt_count;
                meta.telemetry_byte_count = byte_count;
                meta.telemetry_icmp_count = icmp_count;
                meta.telemetry_min_ttl = min_ttl;

                // Aciona a clonagem de Ingress para Egress na sessão 100
                clone(CloneType.I2E, 100);

                // Reset da janela de estado
                reg_pkt_count.write(0, 0);
                reg_byte_count.write(0, 0);
                reg_icmp_count.write(0, 0);
                reg_min_ttl.write(0, 0);
            } else {
                // Guarda o estado atualizado para o próximo pacote
                reg_pkt_count.write(0, pkt_count);
                reg_byte_count.write(0, byte_count);
                reg_icmp_count.write(0, icmp_count);
                reg_min_ttl.write(0, min_ttl);
            }
                                           
            // 4. Encaminhamento padrão (modificado para permitir qualquer tráfego IPv4)
            if (hdr.ipv4.ttl > 1) { 
                ipv4_lpm.apply();
            } else {
                drop();
            }
        } else {
            drop();
        }
    }
}

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {
    apply {
        // Verifica se é um pacote clonado (instance_type == 1 representa ingress-to-egress clone)
        if (standard_metadata.instance_type == 1) {
            // Torna o cabeçalho de telemetria válido
            hdr.telemetry.setValid();
            
            // Preenche o cabeçalho com os dados obtidos no momento da clonagem
            hdr.telemetry.packet_count = meta.telemetry_pkt_count;
            hdr.telemetry.byte_count = meta.telemetry_byte_count;
            hdr.telemetry.icmp_count = meta.telemetry_icmp_count;
            hdr.telemetry.min_ttl = meta.telemetry_min_ttl;
            
            // Modifica o EtherType para que o coletor no h3 identifique o pacote de telemetria
            hdr.ethernet.etherType = TYPE_TELEMETRY;
        }
    }                                                
}

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply {
        update_checksum(
            hdr.ipv4.isValid(),          
            { hdr.ipv4.version,                           
              hdr.ipv4.ihl,
              hdr.ipv4.diffserv,
              hdr.ipv4.totalLen,
              hdr.ipv4.identification,
              hdr.ipv4.flags,
              hdr.ipv4.fragOffset,
              hdr.ipv4.ttl,
              hdr.ipv4.protocol,
              hdr.ipv4.srcAddr,
              hdr.ipv4.dstAddr },
            hdr.ipv4.hdrChecksum,
            HashAlgorithm.csum16);
    }
}

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        // Emite os cabeçalhos. O cabeçalho de telemetria só será emitido nos clones onde foi feito setValid().
        packet.emit(hdr.ethernet);
        packet.emit(hdr.telemetry);
        packet.emit(hdr.ipv4);
    }
}

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;