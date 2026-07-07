# Relatório Técnico — Trabalho 4
## Controlador P4 com Decisão Automática sobre o Tráfego

**Disciplina:** Redes de Computadores  
**Grupo:** [preencher no grupo.txt]  
**Data:** [preencher]

---

## 1. Introdução

O objetivo deste trabalho é evoluir o controlador desenvolvido no Trabalho 3 para que, além de receber e exibir telemetria de switches P4/BMv2, tome **decisões automáticas** sobre o tráfego e instale regras no plano de dados sem intervenção manual.

O ciclo de controle demonstrado é:

```
switch mede → controlador decide → regra no switch → efeito no tráfego
```

Quando a taxa de pacotes provenientes do host `h1` (IP `10.0.0.1`) ultrapassa um limiar configurado, o controlador instala uma regra de `drop` na tabela `drop_table` do switch. Quando a taxa retorna ao normal por um número suficiente de amostras, a regra é removida automaticamente.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│  Mininet + BMv2 (s1)                                        │
│  ┌──────────────┐   Thrift   ┌──────────────────────────┐   │
│  │ telemetry.p4 │◄───────────│ p4_register_exporter.py  │   │
│  │  registradores            │  lê regs e envia UDP     │   │
│  │  drop_table               └──────────┬───────────────┘   │
│  └──────────────┘                      UDP 9999             │
│         ▲                                  │                │
│         │  regras via CLI                  ▼                │
│  simple_switch_CLI                 ┌──────────────────┐     │
│                                    │ controller_t4.py │     │
│                                    │  - decodifica    │     │
│                                    │  - calcula taxas │     │
│                                    │  - decide drop   │     │
│                                    └────────┬─────────┘     │
└─────────────────────────────────────────────┼───────────────┘
                                              │ Socket.IO
                                              ▼
                                    ┌─────────────────────┐
                                    │  Browser (dashboard)│
                                    │  - métricas         │
                                    │  - taxas            │
                                    │  - ações/logs       │
                                    └─────────────────────┘
```

**Componentes:**
- `telemetry.p4`: programa P4 com tabela de roteamento `ipv4_lpm` e tabela de bloqueio `drop_table`.
- `p4_register_exporter.py`: lê os registradores do BMv2 via Thrift e envia UDP ao controlador.
- `controller_trabalho4.py`: recebe telemetria, calcula taxas, decide e instala/remove regras.
- `traffic_generator.py`: gera tráfego normal → ataque → recuperação dentro do host `h1`.
- Dashboard web: mostra métricas, taxa atual, status de bloqueio e log de ações.

---

## 3. Política de Decisão

A política implementada no `controller_trabalho4.py` utiliza a métrica **pacotes/s** (`packet_count` recebido do switch).

| Parâmetro | Valor | Descrição |
|---|---|---|
| Métrica | `packet_count` | Contador acumulado de pacotes no switch |
| Taxa calculada | `pkts/s` | Variação do contador / tempo entre amostras |
| Limiar | `120 pkts/s` | Acima desse valor, o tráfego é considerado anômalo |
| IP bloqueado | `10.0.0.1` | Host `h1`, origem do flood de ICMP |
| Amostras para bloquear | `2` | Histerese: 2 amostras consecutivas acima do limiar |
| Amostras para desbloquear | `5` | Histerese: 5 amostras consecutivas abaixo do limiar |
| Ação no switch | `drop` | Descarta pacotes com origem `10.0.0.1` |

A histerese evita oscilações rápidas entre bloqueio e desbloqueio quando a taxa fica próxima do limiar.

---

## 4. Modificações no Plano de Dados

O arquivo `telemetry.p4` foi corrigido para declarar a tabela `drop_table` **antes** do bloco `apply` em `MyIngress`. O fluxo de processamento no ingresso é:

1. Se o pacote IPv4 for válido, aplica `drop_table`.
2. Se a ação executada for `drop`, o pacote é descartado.
3. Caso contrário, aplica `ipv4_lpm` para encaminhamento normal e atualiza os registradores de telemetria.

A tabela `drop_table` possui `default_action = NoAction()`, ou seja, por padrão todo tráfego segue normalmente até que o controlador instale uma regra específica.

---

## 5. Controlador

O `controller_trabalho4.py` estende o controlador do Trabalho 3 com as seguintes funções:

- `calculate_rate(switch_id, packet_count)`: calcula `pkts/s` a partir do contador acumulado.
- `install_drop_rule(src_ip_int, thrift_port)`: envia comando `table_add` via `simple_switch_CLI` e captura o `handle` da regra.
- `remove_drop_rule(handle, thrift_port)`: envia comando `table_delete` via `simple_switch_CLI`.
- `evaluate_policy(switch_id, metrics)`: mantém contadores de amostras consecutivas acima/abaixo do limiar e decide bloquear/desbloquear.

A cada datagrama UDP recebido, o controlador:
1. Decodifica a telemetria.
2. Calcula a taxa e avalia a política.
3. Atualiza as estruturas de estado.
4. Emite um evento SocketIO `telemetry_update` contendo métricas, histórico e resultado da política.

---

## 6. Gerador de Tráfego

O `traffic_generator.py` deve ser executado dentro do host `h1` no Mininet. Ele executa três fases sequenciais:

1. **Normal**: `ping -i 1 10.0.0.2` por 10 segundos.
2. **Ataque**: `ping -f 10.0.0.2` por 15 segundos (flood de ICMP).
3. **Recuperação**: `ping -i 1 10.0.0.2` por 10 segundos.

A fase de ataque eleva a taxa de pacotes acima do limiar, provocando o bloqueio automático. Na fase de recuperação, a taxa cai e o controlador remove a regra de `drop`.

---

## 7. Demonstração e Resultados

### 7.1 Compilação do programa P4

```bash
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json
```

### 7.2 Execução do controlador

```bash
python3 controller_trabalho4.py
```

Acesse o dashboard em: http://localhost:5000

### 7.3 Execução da topologia Mininet

```bash
sudo python3 topo_trabalho3.py
```

### 7.4 Início do exportador de registradores

O exportador deve ser executado **fora do Mininet**, em um terminal comum do host (root), pois precisa acessar a porta Thrift `9090` do BMv2 e enviar UDP ao controlador — ambos rodam no namespace do host, não dentro dos hosts `h1`, `h2` ou `h3`.

```bash
python3 p4_register_exporter.py --thrift-port 9090 --switch-id 1 --controller 127.0.0.1
```

### 7.5 Geração de tráfego

Dentro do Mininet, no host `h1`:

```
mininet> h1 python3 traffic_generator.py
```

### 7.6 Resultados esperados

- Durante a fase normal, o dashboard mostra taxa baixa e status **Normal**.
- Durante o ataque, assim que `pkts/s > 120` por 2 amostras consecutivas, o controlador instala a regra de `drop` para `10.0.0.1`.
- O dashboard mostra status **Bloqueado**, o IP bloqueado e um log de ação.
- O tráfego `h1 → h2` é interrompido, enquanto `h2 ↔ h3` continua funcionando.
- Durante a recuperação, após 5 amostras abaixo do limiar, a regra é removida e o status volta a **Normal**.

---

## 8. Conclusão

O trabalho demonstra com sucesso um ciclo fechado de controle em redes programáveis: o switch P4 coleta métricas, o controlador as interpreta e toma decisões, e o plano de dados é reconfigurado automaticamente. A política com histerese garante estabilidade na decisão, e o dashboard fornece visibilidade em tempo real das ações do controlador.

---

## Referências

- P4 Language Consortium. *P4_16 Language Specification*. https://p4.org/
- p4lang/behavioral-model. *BMv2 Simple Switch*. https://github.com/p4lang/behavioral-model
- Material da disciplina Redes de Computadores — UFSM.
