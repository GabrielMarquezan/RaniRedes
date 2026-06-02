# Relatório Técnico — Trabalho 3
## Controlador de Telemetria para Switches P4

**Disciplina:** Redes de Computadores  
**Grupo:** NN — [Nomes dos integrantes]  
**Data:** [Data de entrega]

---

## 1. Introdução

Este trabalho implementa um sistema de monitoramento em tempo real para switches P4 executando no ambiente BMv2 com Mininet. O objetivo é complementar o Trabalho 2 (telemetria no plano de dados) com um controlador capaz de receber, decodificar, armazenar e visualizar as métricas exportadas pelos switches.

A solução segue a arquitetura SDN (Software-Defined Networking): o plano de dados (switch P4) é separado do plano de controle (controlador Python), e as métricas fluem do switch ao controlador via protocolo UDP simples, com visualização em tempo real no browser via WebSockets.

---

## 2. Arquitetura da Solução

A solução é composta por três camadas:

**Plano de dados (P4 / BMv2):**  
O programa `telemetry.p4` encaminha pacotes IPv4 e mantém quatro registradores: `reg_packet_count`, `reg_byte_count`, `reg_icmp_count` e `reg_min_ttl`. Cada registrador é incrementado no bloco Ingress a cada pacote processado. O TTL mínimo é atualizado por comparação.

**Exportador de telemetria (`p4_register_exporter.py`):**  
Um processo Python em polling lê os registradores via interface Thrift do BMv2 (`simple_switch_CLI`) e serializa os valores em um datagrama UDP de 25 bytes, enviado ao controlador periodicamente (padrão: 1 segundo).

**Controlador e dashboard (`controller_trabalho3.py`):**  
Recebe os datagramas UDP, decodifica com `struct.unpack`, mantém o histórico por `switch_id` e notifica os browsers conectados via Flask-SocketIO. A interface web (`index.html`, `dashboard.js`, `style.css`) exibe uma tabela de valores atuais, gráficos de linha em tempo real (Chart.js) e uma tabela de histórico com filtragem por switch.

### Diagrama de fluxo

```
[BMv2 registradores] → [Thrift/CLI] → [p4_register_exporter]
       → UDP:9999 → [controller_trabalho3] → SocketIO → [Browser]
```

---

## 3. Formato do Pacote de Telemetria

O pacote UDP exportado tem 25 bytes, estruturado em big-endian conforme a tabela abaixo. Este formato foi escolhido por ser compatível com `struct.pack/unpack` do Python e de fácil extensão.

| Campo         | Tipo   | Tamanho | Descrição                         |
|---------------|--------|---------|-----------------------------------|
| switch_id     | uint32 | 4 bytes | Identificador único do switch     |
| packet_count  | uint64 | 8 bytes | Total de pacotes encaminhados     |
| byte_count    | uint64 | 8 bytes | Total de bytes encaminhados       |
| icmp_count    | uint32 | 4 bytes | Pacotes ICMP encaminhados         |
| min_ttl       | uint8  | 1 byte  | Menor TTL observado nos pacotes   |

A decodificação é realizada pela função `decode_telemetry()` em `telemetry_receiver.py`, utilizando a string de formato `"!IQQIb"` do módulo `struct`.

---

## 4. Identificação do Switch de Origem

O campo `switch_id` (primeiros 4 bytes do payload) é a chave de identificação. No Trabalho 3, o switch `s1` usa `switch_id = 1`. O exportador é configurado com `--switch-id` na linha de comando, permitindo que múltiplos exportadores (um por switch) enviem para o mesmo controlador sem ambiguidade.

O controlador mantém dois dicionários indexados por `switch_id`:

- `latest_metrics[sid]` — última leitura de cada switch
- `history[sid]` — deque com até 60 amostras históricas

O dashboard cria automaticamente um card de gráfico e uma linha na tabela para cada `switch_id` distinto recebido, sem necessidade de configuração prévia.

---

## 5. Implementação do Dashboard

O backend é um servidor Flask com Flask-SocketIO no modo `threading`. A cada pacote UDP decodificado, um evento `telemetry_update` é emitido via WebSocket para todos os browsers conectados. Ao conectar, o browser recebe um evento `initial_state` com todo o histórico acumulado.

O frontend usa Chart.js para renderizar gráficos de linha atualizáveis sem recarregar a página. Cada switch tem quatro gráficos (pacotes, bytes, ICMP, TTL mínimo), acessíveis por abas. A tabela de histórico suporta filtragem por switch e limpeza manual.

---

## 6. Testes e Resultados

Os testes foram realizados em dois cenários:

**Modo simulado:** O `telemetry_simulator.py` gerou tráfego sintético com rajadas aleatórias, validando o funcionamento do dashboard sem dependência do ambiente P4 real. O dashboard respondeu às atualizações em menos de 100 ms.

**Modo real (Mininet):** Com a topologia `topo_trabalho3.py`, tráfego ICMP e TCP entre h1, h2 e h3 foi gerado com `ping` e `iperf`. Os contadores do switch foram lidos a cada 1 segundo e exibidos no dashboard, com variação visível nos gráficos conforme o tipo de tráfego variava.

O campo `min_ttl` permitiu identificar claramente pacotes de hosts locais (TTL inicial 64) versus pacotes com TTL reduzido por múltiplos saltos em topologias maiores.

---

## 7. Conclusão

A solução implementa com sucesso todos os requisitos do Trabalho 3: recepção e decodificação de telemetria P4 via UDP, identificação por `switch_id`, histórico temporal, interface web em tempo real com tabela e gráficos, e suporte a múltiplos switches. O modo de simulação facilita o desenvolvimento e a demonstração independente do ambiente Mininet.

Como trabalho futuro, o mecanismo de exportação pode ser migrado para In-band Network Telemetry (INT) ou gRPC/gNMI, substituindo apenas o módulo `p4_register_exporter.py` sem alterar o controlador ou o dashboard.

---

## Referências

- P4 Language Specification v1.2 — https://p4.org/p4-spec/
- BMv2 Simple Switch — https://github.com/p4lang/behavioral-model
- Flask-SocketIO Documentation — https://flask-socketio.readthedocs.io
- Chart.js Documentation — https://www.chartjs.org/docs/
- p4lang/tutorials — https://github.com/p4lang/tutorials
