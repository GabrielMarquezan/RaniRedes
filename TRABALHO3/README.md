# Trabalho 3 — Controlador de Telemetria P4

Dashboard em tempo real para switches P4 (BMv2 + Mininet)

---

## Estrutura do projeto

```
projeto/
├── controller_trabalho3.py    # Backend Flask + SocketIO + receptor UDP
├── telemetry_receiver.py      # Módulo de decodificação (standalone ou importado)
├── telemetry_simulator.py     # Simulador de switches P4 (testes sem hardware)
├── p4_register_exporter.py    # Exportador: lê registradores BMv2 e envia UDP
├── telemetry.p4               # Programa P4_16 (plano de dados)
├── topo_trabalho3.py          # Script Mininet (topologia + regras)
├── topo_trabalho3.json        # Topologia em JSON (referência)
├── requirements.txt
├── grupo.txt
├── templates/
│   └── index.html             # Dashboard HTML
└── static/
    ├── style.css              # Estilos
    └── dashboard.js           # Lógica SocketIO + Chart.js
```

---

## Arquitetura

```
┌─────────────────────────────────────────┐
│            PLANO DE DADOS               │
│  Mininet ─► BMv2 (telemetry.p4)         │
│  Registradores: packet_count, byte_count│
│               icmp_count, min_ttl       │
└──────────────────┬──────────────────────┘
                   │ leitura Thrift
                   ▼
┌──────────────────────────────────────────┐
│       p4_register_exporter.py            │
│  Lê registradores e empacota em UDP      │
└──────────────────┬───────────────────────┘
                   │ UDP:9999 (25 bytes)
                   ▼
┌──────────────────────────────────────────┐
│      controller_trabalho3.py             │
│  - udp_receiver() (thread)               │
│  - decode_telemetry() → dict             │
│  - histórico por switch_id               │
│  - Flask HTTP + SocketIO                 │
└──────────────────┬───────────────────────┘
                   │ WebSocket (SocketIO)
                   ▼
┌──────────────────────────────────────────┐
│      Browser — index.html                │
│  - Tabela de valores atuais              │
│  - Gráficos de linha (Chart.js)          │
│  - Tabela de histórico                   │
└──────────────────────────────────────────┘
```

---

## Fluxo dos dados

1. O switch BMv2 executa `telemetry.p4`, que incrementa registradores a cada pacote encaminhado.
2. `p4_register_exporter.py` polling via `simple_switch_CLI` (Thrift) e envia UDP ao controlador.
3. `controller_trabalho3.py` recebe o datagrama UDP, chama `decode_telemetry()` e atualiza o estado.
4. O estado é emitido via SocketIO para todos os browsers conectados.
5. `dashboard.js` recebe o evento e atualiza tabela, gráficos e histórico sem recarregar a página.

---

## Formato do pacote de telemetria (UDP payload — 25 bytes)

| Campo         | Tipo    | Tamanho | Byte-order |
|---------------|---------|---------|------------|
| switch_id     | uint32  | 4 bytes | big-endian |
| packet_count  | uint64  | 8 bytes | big-endian |
| byte_count    | uint64  | 8 bytes | big-endian |
| icmp_count    | uint32  | 4 bytes | big-endian |
| min_ttl       | uint8   | 1 byte  | —          |

Struct Python: `"!IQQIb"` (25 bytes total)

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

---

## Execução

### Modo simulado (sem switch P4 real)

Terminal 1 — Controlador:
```bash
python3 controller_trabalho3.py
```

Terminal 2 — Simulador:
```bash
# 1 switch, 1 pacote por segundo
python3 telemetry_simulator.py

# 3 switches, 500ms de intervalo
python3 telemetry_simulator.py --switches 3 --interval 0.5
```

Abrir no browser: http://localhost:5000

---

### Modo real (com Mininet + BMv2)

#### Passo 1 — Compilar o programa P4

```bash
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json
```

#### Passo 2 — Iniciar o controlador

```bash
# Em um terminal fora do Mininet
python3 controller_trabalho3.py
```

#### Passo 3 — Iniciar a topologia Mininet

```bash
sudo python3 topo_trabalho3.py
```

#### Passo 4 — Iniciar o exportador de registradores

Em outro terminal (fora do Mininet ou numa xterm do Mininet):
```bash
python3 p4_register_exporter.py \
    --thrift-port 9090 \
    --switch-id 1 \
    --controller 127.0.0.1 \
    --interval 1.0
```

#### Passo 5 — Gerar tráfego no Mininet

Dentro da CLI do Mininet:
```
mininet> h1 ping h2 -c 100
mininet> h1 ping h3 -c 100 &
mininet> h2 iperf -s &
mininet> h1 iperf -c 10.0.0.2 -t 30
```

---

## Testes com o receptor standalone

```bash
# Terminal 1 — receptor apenas (sem dashboard)
python3 telemetry_receiver.py --port 9999

# Terminal 2 — simulador
python3 telemetry_simulator.py
```

---

## Identificação do switch de origem

Cada pacote UDP contém o campo `switch_id` nos primeiros 4 bytes.
O controlador usa esse campo como chave nos dicionários `latest_metrics` e `history`.
Para múltiplos switches, basta que cada instância do exportador envie um `switch_id` diferente.
O dashboard criará automaticamente um card de gráfico e uma linha na tabela por switch.

---

## Adaptação para outros mecanismos de telemetria

| Mecanismo | O que mudar |
|-----------|-------------|
| Clone de pacotes (mirroring) | Receber com `AF_PACKET` ou `scapy`; remover headers Ethernet/IP antes de chamar `decode_telemetry()` |
| INT (In-band Network Telemetry) | Parsear o stack INT com scapy; extrair metadados de cada hop |
| gRPC / gNMI | Substituir `udp_receiver()` por um channel gRPC; deserializar protobuf |
| Kafka / NATS | Substituir `udp_receiver()` por um consumer do broker |

---

## Comandos úteis de diagnóstico

```bash
# Verificar se o controlador está escutando na porta UDP
ss -ulnp | grep 9999

# Enviar um pacote de telemetria de teste manual
python3 -c "
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = struct.pack('!IQQIb', 1, 500, 64000, 20, 62)
s.sendto(payload, ('127.0.0.1', 9999))
print('Enviado!')
"

# Ver logs do controlador em tempo real
tail -f /tmp/controller.log  # se redirecionar stdout
```

---

## Dependências de sistema (Ubuntu / Linux Mint)

```bash
sudo apt update
sudo apt install python3 python3-pip mininet
# p4lang/behavioral-model e p4c conforme tutorial oficial:
# https://github.com/jafingerhut/p4-guide/blob/master/bin/install-p4dev-v8.sh
```
