# Trabalho 4 — Controlador P4 com Decisão Automática de Tráfego

Evolução do Trabalho 3: o controlador recebe telemetria de switches P4/BMv2, calcula taxas de tráfego e toma decisões automáticas — instalando ou removendo regras no plano de dados para mitigar um fluxo anômalo.

Ciclo demonstrado:

```
switch mede → controlador decide → regra no switch → efeito no tráfego
```

---

## Estrutura do projeto

```
projeto/
├── controller_trabalho4.py    # Backend Flask + SocketIO + decisão automática
├── telemetry_receiver.py      # Módulo de decodificação standalone (reuso T3)
├── telemetry_simulator.py     # Simulador de switches P4 (testes sem hardware)
├── p4_register_exporter.py    # Exportador: lê registradores BMv2 e envia UDP
├── traffic_generator.py       # Gera tráfego normal/ataque/recuperação no h1
├── telemetry.p4               # Programa P4_16 com drop_table
├── topo_trabalho3.py          # Script Mininet (topologia + regras estáticas)
├── topo_trabalho3.json        # Topologia em JSON (referência)
├── rules.txt                  # Regras estáticas e dinâmicas usadas
├── RELATORIO_TRABALHO4.md     # Relatório do Trabalho 4
├── requirements.txt
├── grupo.txt
├── templates/
│   └── index.html             # Dashboard HTML
└── static/
    ├── style.css              # Estilos
    └── dashboard.js           # Lógica SocketIO + Chart.js + painel de decisões
```

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│  Mininet + BMv2 (s1)                                        │
│  ┌──────────────┐   Thrift   ┌──────────────────────────┐  │
│  │ telemetry.p4 │◄───────────│ p4_register_exporter.py  │  │
│  │  registradores            │  lê regs e envia UDP     │  │
│  │  drop_table               └──────────┬───────────────┘  │
│  └──────────────┘                      UDP 9999            │
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

---

## Fluxo dos dados

1. O switch BMv2 executa `telemetry.p4`, que incrementa registradores e consulta a `drop_table` antes do roteamento.
2. `p4_register_exporter.py` faz polling dos registradores via `simple_switch_CLI` (Thrift) e envia UDP ao controlador.
3. `controller_trabalho4.py` recebe o datagrama UDP, calcula `pkts/s` e avalia a política de bloqueio.
4. Se a taxa ultrapassar o limiar por 2 amostras consecutivas, o controlador instala uma regra `drop` para `10.0.0.1`.
5. Se a taxa ficar abaixo do limiar por 5 amostras consecutivas, a regra é removida.
6. O estado e as decisões são emitidos via SocketIO para o dashboard.

---

## Formato do pacote de telemetria (UDP payload — 25 bytes)

| Campo         | Tipo    | Tamanho | Byte-order |
|---------------|---------|---------|------------|
| switch_id     | uint32  | 4 bytes | big-endian |
| packet_count  | uint64  | 8 bytes | big-endian |
| byte_count    | uint64  | 8 bytes | big-endian |
| icmp_count    | uint32  | 4 bytes | big-endian |
| min_ttl       | uint8   | 1 byte  | —          |

Struct Python: `"!IQQIB"` (25 bytes total)

---

## Política de decisão

Configurada em `controller_trabalho4.py`:

| Parâmetro | Valor | Significado |
|---|---|---|
| `LIMIT_PKTS_PER_SEC` | `120` | Taxa que caracteriza ataque |
| `BLOCKED_SRC_IP` | `10.0.0.1` | IP bloqueado quando a taxa é ultrapassada |
| `SAMPLES_TO_BLOCK` | `2` | Amostras consecutivas acima do limiar para bloquear |
| `SAMPLES_TO_UNBLOCK` | `5` | Amostras consecutivas abaixo do limiar para desbloquear |
| `SWITCH_THRIFT_PORT` | `9090` | Porta Thrift do BMv2 |

---

## Instalação de dependências

```bash
# Python 3.12 + pip
pip install -r requirements.txt --break-system-packages

# Ou com virtual environment (recomendado)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Requisitos de sistema: BMv2 (`simple_switch`, `simple_switch_CLI`), `p4c`, `mininet`.

---

## Execução

### Modo simulado (sem switch P4 real)

Usado para validar o dashboard e a lógica de decisão sem levantar a topologia Mininet.

**Terminal 1 — Controlador:**
```bash
python3 controller_trabalho4.py
```

**Terminal 2 — Simulador:**
```bash
# 1 switch, 1 amostra por segundo
python3 telemetry_simulator.py

# 3 switches, 500ms de intervalo
python3 telemetry_simulator.py --switches 3 --interval 0.5
```

Abrir no browser: http://localhost:5000

> **Nota:** o `telemetry_simulator.py` gera deltas aleatórios de pacotes. Dependendo dos valores, a taxa pode ou não ultrapassar o limiar de 120 pkts/s. Para forçar o disparo da política, use o modo real com `traffic_generator.py` ou ajuste o simulador.

---

### Modo real (com Mininet + BMv2)

#### Passo 1 — Compilar o programa P4

```bash
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json
```

#### Passo 2 — Iniciar o controlador

```bash
# Em um terminal fora do Mininet
python3 controller_trabalho4.py
```

Abrir no browser: http://localhost:5000

#### Passo 3 — Iniciar a topologia Mininet

```bash
sudo python3 topo_trabalho3.py
```

#### Passo 4 — Iniciar o exportador de registradores

Em um **terminal fora do Mininet** (host root), execute o exportador. Ele precisa rodar no mesmo namespace do BMv2 e do controlador para acessar a porta Thrift `9090` e enviar UDP para `127.0.0.1:9999`:

```bash
python3 p4_register_exporter.py --thrift-port 9090 --switch-id 1 --controller 127.0.0.1 --interval 1.0
```

> **Importante:** os hosts `h1`, `h2`, `h3` são namespaces de rede isolados. Dentro deles, `127.0.0.1` é o próprio host e a porta Thrift `9090` do BMv2 não é acessível. Por isso, o exportador **não** deve ser executado via `h1 xterm` ou `h1 python3`.

#### Passo 5 — Gerar tráfego normal/ataque/recuperação

Ainda dentro do Mininet, execute o gerador no host `h1`:

```
mininet> h1 python3 traffic_generator.py
```

O gerador executa:
1. **Normal**: `ping -i 1 10.0.0.2` por 10s.
2. **Ataque**: `ping -f 10.0.0.2` por 15s.
3. **Recuperação**: `ping -i 1 10.0.0.2` por 10s.

#### Passo 6 — Observar a demonstração

No dashboard e nos logs do controlador você deve ver:
- Fase normal: taxa baixa, status **Normal**.
- Fase de ataque: taxa ultrapassa 120 pkts/s e, após 2 amostras, o controlador instala a regra de `drop` para `10.0.0.1`.
- Durante o bloqueio: `h1` não consegue mais pingar `h2`, mas `h2` e `h3` continuam se comunicando.
- Fase de recuperação: após 5 amostras abaixo do limiar, a regra é removida e o status volta a **Normal**.

---

## Comandos úteis de diagnóstico

```bash
# Verificar se o controlador está escutando na porta UDP
ss -ulnp | grep 9999

# Verificar regras instaladas no switch
simple_switch_CLI --thrift-port 9090
table_dump MyIngress.drop_table
table_dump MyIngress.ipv4_lpm

# Enviar um pacote de telemetria de teste manual
python3 -c "
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = struct.pack('!IQQIB', 1, 500, 64000, 20, 62)
s.sendto(payload, ('127.0.0.1', 9999))
print('Enviado!')
"

# Ver logs do controlador em tempo real
# (redirecione a saída para um arquivo, se desejado)
tail -f /tmp/controller_t4.log
```

---

## Critérios de aceitação

- [ ] `telemetry.p4` compila sem erros.
- [ ] Controlador recebe telemetria e calcula `pkts/s`.
- [ ] Quando `pkts/s > 120` por 2 amostras, regra de `drop` para `10.0.0.1` é instalada.
- [ ] Tráfego de `h1` para `h2` é interrompido após o bloqueio.
- [ ] Tráfego entre `h2` e `h3` continua funcionando.
- [ ] Quando `pkts/s < 120` por 5 amostras, a regra é removida.
- [ ] Dashboard mostra taxa, status e log de ações.

---

## Dependências de sistema (Ubuntu / Linux Mint)

```bash
sudo apt update
sudo apt install python3 python3-pip mininet
# p4lang/behavioral-model e p4c conforme tutorial oficial:
# https://github.com/jafingerhut/p4-guide/blob/master/bin/install-p4dev-v8.sh
```
