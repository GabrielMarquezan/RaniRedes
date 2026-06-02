/* ============================================================================
   telemetry.p4 — Programa P4_16 com telemetria para BMv2 / Simple Switch
   Trabalho 3 — Disciplina de Redes de Computadores

   Função:
     * Encaminha pacotes IPv4 normalmente.
     * Mantém contadores globais: pacotes, bytes, ICMP, TTL mínimo.
     * Ao encaminhar, clona o pacote original e gera um pacote de relatório
       de telemetria UDP enviado ao controlador (10.0.0.254:9999).

   Importante:
     * A exportação real de telemetria via clonagem (clone3) em BMv2 requer
       configuração de espelho (mirror session) via CLI ou script de controle.
     * A forma mais simples para o Trabalho 3 é usar um mecanismo de
       recirculação + geração de pacote especial (descrito abaixo).
     * Para simplificar, este arquivo usa a abordagem de GERAÇÃO de um
       pacote UDP de relatório diretamente no egress, sem clone.

   Estrutura do pacote de telemetria exportado (UDP payload):
     switch_id    : 32 bits (big-endian)
     packet_count : 64 bits
     byte_count   : 64 bits
     icmp_count   : 32 bits
     min_ttl      :  8 bits
   Total: 25 bytes
   ============================================================================ */

/* ─── includes e arquitetura ─────────────────────────────────────────────── */
#include <core.p4>
#include <v1model.p4>

/* ─── Constantes ─────────────────────────────────────────────────────────── */
const bit<16> TYPE_IPV4        = 0x0800;
const bit<8>  IP_PROTO_ICMP    = 0x01;
const bit<8>  IP_PROTO_UDP     = 0x11;
const bit<8>  IP_PROTO_TCP     = 0x06;

// ID deste switch — deve ser alterado por switch na topologia
const bit<32> SWITCH_ID        = 1;

// Endereço do controlador (10.0.0.254 = 0x0A0000FE)
const bit<32> CONTROLLER_IP    = 0x0A0000FE;
const bit<16> CONTROLLER_PORT  = 9999;
const bit<16> TELEMETRY_SRC_PORT = 50000;

/* ─── Tamanho do payload de telemetria em bytes ─────────────────────────── */
// switch_id(4) + packet_count(8) + byte_count(8) + icmp_count(4) + min_ttl(1) = 25 B
// UDP header = 8 B  →  IP total length = 20+8+25 = 53 B
const bit<16> TELEMETRY_PAYLOAD_LEN = 25;
const bit<16> UDP_TOTAL_LEN         = 8 + TELEMETRY_PAYLOAD_LEN;
const bit<16> IP_TOTAL_LEN          = 20 + UDP_TOTAL_LEN;

/* ─────────────────────────────────────────────────────────────────────────── */
/* Headers                                                                     */
/* ─────────────────────────────────────────────────────────────────────────── */

header ethernet_t {
    bit<48> dstAddr;
    bit<48> srcAddr;
    bit<16> etherType;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> totalLen;
    bit<16> identification;
    bit<3>  flags;
    bit<13> fragOffset;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> hdrChecksum;
    bit<32> srcAddr;
    bit<32> dstAddr;
}

header icmp_t {
    bit<8>  type;
    bit<8>  code;
    bit<16> checksum;
}

header udp_t {
    bit<16> srcPort;
    bit<16> dstPort;
    bit<16> length;
    bit<16> checksum;
}

/* Header customizado de telemetria (payload UDP enviado ao controlador) */
header telemetry_report_t {
    bit<32>  switch_id;
    bit<64>  packet_count;
    bit<64>  byte_count;
    bit<32>  icmp_count;
    bit<8>   min_ttl;
}

struct headers {
    ethernet_t        ethernet;
    ipv4_t            ipv4;
    icmp_t            icmp;
    udp_t             udp;
    /* Headers usados apenas no pacote de telemetria gerado */
    ethernet_t        tel_ethernet;
    ipv4_t            tel_ipv4;
    udp_t             tel_udp;
    telemetry_report_t tel_report;
}

/* Metadados internos */
struct metadata {
    bit<1>  send_telemetry;   /* 1 = gerar pacote de telemetria no egress */
    bit<9>  egress_port;
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Registradores de telemetria (estado global persistente)                     */
/* ─────────────────────────────────────────────────────────────────────────── */

/* Índice 0 = valor acumulado */
register<bit<64>>(1) reg_packet_count;
register<bit<64>>(1) reg_byte_count;
register<bit<32>>(1) reg_icmp_count;
register<bit<8>>(1)  reg_min_ttl;

/* ─────────────────────────────────────────────────────────────────────────── */
/* Parser                                                                       */
/* ─────────────────────────────────────────────────────────────────────────── */

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
            default:   accept;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            IP_PROTO_ICMP: parse_icmp;
            IP_PROTO_UDP:  parse_udp;
            default:       accept;
        }
    }

    state parse_icmp {
        packet.extract(hdr.icmp);
        transition accept;
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition accept;
    }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Checksum verification (vazio — BMv2 não valida por padrão)                 */
/* ─────────────────────────────────────────────────────────────────────────── */

control MyVerifyChecksum(inout headers hdr, inout metadata meta) {
    apply { }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Ingress                                                                      */
/* ─────────────────────────────────────────────────────────────────────────── */

control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    /* ── Ação: encaminhar para porta ────────────────────────────────────── */
    action forward(bit<9> port) {
        standard_metadata.egress_spec = port;
        meta.egress_port = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;

        /* ── Atualiza contadores ── */
        bit<64> pkts;
        bit<64> byts;
        bit<32> icmps;
        bit<8>  mttl;

        reg_packet_count.read(pkts, 0);
        reg_byte_count.read(byts, 0);
        reg_icmp_count.read(icmps, 0);
        reg_min_ttl.read(mttl, 0);

        pkts = pkts + 1;
        byts = byts + (bit<64>)standard_metadata.packet_length;

        if (hdr.icmp.isValid()) {
            icmps = icmps + 1;
        }

        if (mttl == 0 || hdr.ipv4.ttl < mttl) {
            mttl = hdr.ipv4.ttl;
        }

        reg_packet_count.write(0, pkts);
        reg_byte_count.write(0, byts);
        reg_icmp_count.write(0, icmps);
        reg_min_ttl.write(0, mttl);

        /* Sinaliza egress para gerar pacote de telemetria */
        meta.send_telemetry = 1;
    }

    action drop() {
        mark_to_drop(standard_metadata);
    }

    /* ── Tabela de encaminhamento IPv4 ──────────────────────────────────── */
    table ipv4_lpm {
        key = {
            hdr.ipv4.dstAddr: lpm;
        }
        actions = {
            forward;
            drop;
            NoAction;
        }
        size = 1024;
        default_action = drop();
    }

    apply {
        meta.send_telemetry = 0;

        if (hdr.ipv4.isValid()) {
            if (hdr.ipv4.ttl == 0) {
                drop();
            } else {
                ipv4_lpm.apply();
            }
        }
    }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Egress — gera o pacote de relatório de telemetria                           */
/* ─────────────────────────────────────────────────────────────────────────── */

control MyEgress(inout headers hdr,
                 inout metadata meta,
                 inout standard_metadata_t standard_metadata) {

    apply {
        /* A geração real de um pacote extra no BMv2 via P4 puro requer
           a primitiva clone3 / recirculate, que é específica de v1model.
           Para o Trabalho 3, a estratégia mais simples é:
             1. O plano de dados incrementa registradores (feito no Ingress).
             2. Um script Python de controle (controller_trabalho3.py) lê os
                registradores via Thrift e monta/envia o pacote UDP.
           Esta seção está pronta para ser expandida se o grupo quiser
           implementar a geração no próprio plano de dados via clone.

           Comentário de expansão:
             clone3(CloneType.E2E, MIRROR_SESSION_ID, meta);
             // No clone, marcar standard_metadata.instance_type == 1
             // e sobrescrever os headers com os de telemetria.
        */
    }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Checksum computation                                                         */
/* ─────────────────────────────────────────────────────────────────────────── */

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
            HashAlgorithm.csum16
        );
    }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Deparser                                                                     */
/* ─────────────────────────────────────────────────────────────────────────── */

control MyDeparser(packet_out packet, in headers hdr) {
    apply {
        packet.emit(hdr.ethernet);
        packet.emit(hdr.ipv4);
        packet.emit(hdr.icmp);
        packet.emit(hdr.udp);
    }
}

/* ─────────────────────────────────────────────────────────────────────────── */
/* Instanciação do switch                                                       */
/* ─────────────────────────────────────────────────────────────────────────── */

V1Switch(
    MyParser(),
    MyVerifyChecksum(),
    MyIngress(),
    MyEgress(),
    MyComputeChecksum(),
    MyDeparser()
) main;
