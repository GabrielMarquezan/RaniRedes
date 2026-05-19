// SPDX-License-Identifier: Apache-2.0
/* -*- P4_16 -*- */
#include <core.p4>
#include <v1model.p4>

const bit<16> TYPE_IPV4 = 0x0800;
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

struct metadata {
}

struct headers {
    ethernet_t ethernet;
    ipv4_t     ipv4;
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
        transition select(hdr.ethernet.etherType) { // verifica etherType
            TYPE_IPV4: parse_ipv4;                  // se for IPv4, vai pro estado parse_ipv4
            default: accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);                   // manda os bytes do IPv4 do pacote pro hdr.ipv4
        transition accept;
    }
}

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }                                       // nada
}

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    action drop() {
        mark_to_drop(standard_metadata);
    }

    action ipv4_forward(macAddr_t dstAddr, egressSpec_t port) {     // aqui fazemos o encaminhamento do pacote IPv4
        standard_metadata.egress_spec = port;                       // sobrescreve o campo egress_spec do standard_metadata com a porta de saída
        hdr.ethernet.srcAddr = hdr.ethernet.dstAddr;                // sobrescreve o campo srcAddr do cabeçalho Ethernet com o valor do campo dstAddr do mesmo cabeçalho
        hdr.ethernet.dstAddr = dstAddr;                             // sobrescreve o campo dstAddr do cabeçalho Ethernet com o valor do parâmetro dstAddr da ação
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;                            // decrementa o campo ttl do cabeçalho IPv4 em 1 (para evitar loops de encaminhamento)
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
            if (hdr.ipv4.protocol == IP_PROTO_ICMP && hdr.ipv4.ttl > 1) {   // verifica se o protocolo é ICMP e se o TTL é maior que 1 (para evitar encaminhamento de pacotes com TTL expirado)
                ipv4_lpm.apply();                                           // aplica a regra de encaminhamento IPv4 
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
    apply { }                                                               // nada 
}

control MyComputeChecksum(inout headers hdr, inout metadata meta) {
    apply {
        update_checksum(                                  // atualiza o campo hdrChecksum do cabeçalho IPv4
            hdr.ipv4.isValid(),                           // só atualiza se o cabeçalho IPv4 for válido
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
              hdr.ipv4.dstAddr },                          // os campos do cabeçalho IPv4 que são usados para calcular o checksum
            hdr.ipv4.hdrChecksum,                          // o campo do cabeçalho IPv4 onde o checksum calculado será armazenado
            HashAlgorithm.csum16);                         // o algoritmo de hash usado para calcular o checksum (nesse caso, checksum de 16 bits)
    }
}

control MyDeparser(packet_out packet, in headers hdr) {    // aqui é onde o pacote é reconstruído para ser enviado para a porta de saída
    apply {
        packet.emit(hdr.ethernet);
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
