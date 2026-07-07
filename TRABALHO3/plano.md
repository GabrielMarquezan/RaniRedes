# Plano de Implementação — Trabalho 4
## Controlador P4 com Decisão Automática de Tráfego

---

## 1. Objetivo

Evoluir o controlador do Trabalho 3 para que, a partir das métricas de telemetria recebidas de switches P4/BMv2, **tome decisões automáticas** e instale/ative regras no plano de dados.

Ciclo esperado:

```
switch mede → controlador decide → regra no switch → efeito no tráfego
```

---

## 2. Decisões de Projeto

| Aspecto | Decisão | Justificativa |
|---|---|---|
| Métrica de decisão | **pacotes/s** | Simples de calcular, varia rapidamente com flood e é fácil de demonstrar. |
| Ação no switch | **drop** por IP de origem | A tabela `drop_table` já existe no P4; ação de impacto visual imediato. |
| Alvo do drop | `10.0.0.1` (host h1) | O gerador de tráfego fará o flood partir de h1, tornando a atribuição correta. |
| Desbloqueio | **Automático** com histerese | Evita bloqueio permanente e demonstra o ciclo completo de controle. |
| Comunicação controlador → switch | `simple_switch_CLI` via Thrift | Mesmo mecanismo já usado pela topologia; não exige novas bibliotecas. |

---

## 3. Estado Atual e Gaps

O repositório do Trabalho 3 já possui:

- `telemetry.p4` com tabela de roteamento `ipv4_lpm` e uma `drop_table` parcialmente implementada.
- `controller_trabalho3.py` que recebe/decodifica telemetria e exibe dashboard.
- `p4_register_exporter.py` que lê registradores e envia UDP.
- `topo_trabalho3.py` com hosts h1, h2, h3 conectados a s1.

**Gaps críticos:**

1. `telemetry.p4`: a `drop_table` está declarada **depois** do bloco `apply` em `MyIngress`, o que provavelmente gera erro de compilação.
2. `controller_trabalho3.py`: calcula taxas? Não. Instala regras? Não.
3. Não existe gerador de tráfego que alterne entre condição normal e condição de ataque.
4. O dashboard não mostra decisões do controlador nem IPs bloqueados.
5. Não há relatório específico do Trabalho 4.

---

## 4. Arquitetura Final

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

## 5. Fases de Implementação

### Fase 1 — Correção do programa P4

**Arquivo:** `telemetry.p4`

**Problema:** a `drop_table` é usada no `apply` antes de ser declarada.

**Ação:** mover a declaração da `drop_table` para **antes** do bloco `apply`.

**Snippet esperado:**

```p4
control MyIngress(inout headers hdr,
                  inout metadata meta,
                  inout standard_metadata_t standard_metadata) {

    action forward(bit<9> port) {
        standard_metadata.egress_spec = port;
        meta.egress_port = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;

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
    }

    action drop() {
        mark_to_drop(standard_metadata);
    }

    /* ── Tabela de bloqueio populada pelo controlador ── */
    table drop_table {
        key = {
            hdr.ipv4.srcAddr: exact;
        }
        actions = {
            drop;
            NoAction;
        }
        size = 1024;
        default_action = NoAction();
    }

    /* ── Tabela de encaminhamento IPv4 ── */
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
        if (hdr.ipv4.isValid()) {
            switch (drop_table.apply().action_run) {
                drop: {
                    // pacote descartado; não encaminha
                }
                default: {
                    ipv4_lpm.apply();
                }
            }
        }
    }
}
```

**Verificação:**

```bash
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json
```

Deve compilar sem erros.

---

### Fase 2 — Controlador com Política de Decisão

**Arquivo:** `controller_trabalho4.py` (novo, baseado em `controller_trabalho3.py`)

**Objetivo:** calcular `pacotes/s` por switch e instalar/remover regras na `drop_table` automaticamente.

#### 2.1 Configurações da política

```python
# ─────────────────────────────────────────────────────────────
# Configurações da política de mitigação
# ─────────────────────────────────────────────────────────────

# Taxa que caracteriza ataque (pacotes por segundo)
LIMIT_PKTS_PER_SEC = 120

# IP que será bloqueado quando a taxa for ultrapassada
BLOCKED_SRC_IP = "10.0.0.1"
BLOCKED_SRC_IP_INT = 0x0A000001  # representação numérica para a tabela P4

# Histerese: amostras consecutivas acima/abaixo do limiar
SAMPLES_TO_BLOCK = 2
SAMPLES_TO_UNBLOCK = 5

# Porta Thrift do switch BMv2
SWITCH_THRIFT_PORT = 9090
```

#### 2.2 Estruturas de estado

```python
# Estado por switch para cálculo de taxa
# { switch_id -> {"last_packet_count": int, "last_time": float, "pkts_per_sec": float} }
rate_state: dict = {}

# Estado da decisão
# { switch_id -> {"consecutive_above": int, "consecutive_below": int, "blocked": bool, "handle": int|None} }
decision_state: dict = defaultdict(lambda: {
    "consecutive_above": 0,
    "consecutive_below": 0,
    "blocked": False,
    "handle": None,
})
```

#### 2.3 Cálculo da taxa

```python
def calculate_rate(switch_id: int, packet_count: int) -> float:
    now = time.time()
    state = rate_state.setdefault(switch_id, {
        "last_packet_count": packet_count,
        "last_time": now,
        "pkts_per_sec": 0.0,
    })

    delta_pkts = packet_count - state["last_packet_count"]
    delta_time = now - state["last_time"]

    if delta_time > 0:
        pkts_per_sec = delta_pkts / delta_time
    else:
        pkts_per_sec = 0.0

    state["last_packet_count"] = packet_count
    state["last_time"] = now
    state["pkts_per_sec"] = pkts_per_sec

    return pkts_per_sec
```

#### 2.4 Funções de instalação/remoção de regras

```python
import subprocess

def install_drop_rule(src_ip_int: int, thrift_port: int = SWITCH_THRIFT_PORT) -> int | None:
    """
    Adiciona entrada na drop_table. Retorna o handle da regra ou None.
    """
    cmd = (
        f"echo 'table_add MyIngress.drop_table MyIngress.drop {src_ip_int}' "
        f"| simple_switch_CLI --thrift-port {thrift_port}"
    )
    log.info("Instalando regra drop para IP 0x%08X na porta Thrift %d", src_ip_int, thrift_port)
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)
        decoded = out.decode()
        log.info("Saída CLI: %s", decoded.strip())
        # Exemplo de saída: "Entry has been added with handle 5"
        for line in decoded.splitlines():
            if "handle" in line.lower():
                parts = line.split()
                return int(parts[-1].rstrip("."))
    except subprocess.CalledProcessError as exc:
        log.error("Falha ao instalar regra: %s", exc.output.decode(errors="ignore"))
    except Exception as exc:
        log.error("Erro inesperado ao instalar regra: %s", exc)
    return None


def remove_drop_rule(handle: int, thrift_port: int = SWITCH_THRIFT_PORT) -> bool:
    """
    Remove entrada da drop_table pelo handle.
    """
    cmd = (
        f"echo 'table_delete MyIngress.drop_table {handle}' "
        f"| simple_switch_CLI --thrift-port {thrift_port}"
    )
    log.info("Removendo regra drop (handle %d) da porta Thrift %d", handle, thrift_port)
    try:
        subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=5)
        return True
    except subprocess.CalledProcessError as exc:
        log.error("Falha ao remover regra: %s", exc.output.decode(errors="ignore"))
    except Exception as exc:
        log.error("Erro inesperado ao remover regra: %s", exc)
    return False
```

#### 2.5 Loop de decisão

Chamado a cada nova amostra de telemetria:

```python
def evaluate_policy(switch_id: int, metrics: dict) -> dict:
    pkts_per_sec = calculate_rate(switch_id, metrics["packet_count"])
    state = decision_state[switch_id]

    action_taken = None

    if pkts_per_sec > LIMIT_PKTS_PER_SEC:
        state["consecutive_above"] += 1
        state["consecutive_below"] = 0
    else:
        state["consecutive_below"] += 1
        state["consecutive_above"] = 0

    # Bloqueia
    if not state["blocked"] and state["consecutive_above"] >= SAMPLES_TO_BLOCK:
        handle = install_drop_rule(BLOCKED_SRC_IP_INT, SWITCH_THRIFT_PORT)
        if handle is not None:
            state["blocked"] = True
            state["handle"] = handle
            action_taken = "block"
            log.warning(
                "SW%d BLOQUEADO: pkts/s=%.1f > limiar=%d",
                switch_id, pkts_per_sec, LIMIT_PKTS_PER_SEC
            )

    # Desbloqueia
    elif state["blocked"] and state["consecutive_below"] >= SAMPLES_TO_UNBLOCK:
        if state["handle"] is not None and remove_drop_rule(state["handle"], SWITCH_THRIFT_PORT):
            state["blocked"] = False
            state["handle"] = None
            action_taken = "unblock"
            log.info(
                "SW%d DESBLOQUEADO: pkts/s=%.1f voltou ao normal",
                switch_id, pkts_per_sec
            )

    return {
        "switch_id": switch_id,
        "pkts_per_sec": round(pkts_per_sec, 2),
        "blocked": state["blocked"],
        "action": action_taken,
        "limit": LIMIT_PKTS_PER_SEC,
    }
```

#### 2.6 Integração com receptor UDP

No `udp_receiver()`, após decodificar a métrica:

```python
metrics = decode_telemetry(raw)
if metrics is None:
    continue

sid = metrics["switch_id"]

with data_lock:
    latest_metrics[sid] = metrics
    history[sid].append(metrics)

# Lógica de controle automático
action_result = evaluate_policy(sid, metrics)

# Notifica frontend
socketio.emit("telemetry_update", {
    "switch_id": sid,
    "metrics": metrics,
    "history": list(history[sid]),
    "policy": action_result,
})
```

#### 2.7 Estado inicial via SocketIO

```python
@socketio.on("connect")
def on_connect():
    log.info("Cliente conectado ao dashboard")
    with data_lock:
        snapshot = {
            sid: {
                "metrics": m,
                "history": list(history[sid]),
                "policy": {
                    "switch_id": sid,
                    "pkts_per_sec": rate_state.get(sid, {}).get("pkts_per_sec", 0.0),
                    "blocked": decision_state[sid]["blocked"],
                    "limit": LIMIT_PKTS_PER_SEC,
                },
            }
            for sid, m in latest_metrics.items()
        }
    emit("initial_state", snapshot)
```

---

### Fase 3 — Gerador de Tráfego

**Arquivo:** `traffic_generator.py` (novo)

**Objetivo:** alternar entre tráfego normal e tráfego de ataque a partir de `h1`.

**Estrutura:**

```python
#!/usr/bin/env python3
"""
traffic_generator.py — Gerador de tráfego para o Trabalho 4
Deve ser executado DENTRO do host h1 no Mininet:

    mininet> h1 python3 traffic_generator.py

Fases:
1. Normal: ping lento para h2.
2. Ataque: flood de ICMP (ping -f) para h2.
3. Recuperação: volta ao ping lento.
"""

import os
import time
import argparse

TARGET_IP = "10.0.0.2"
NORMAL_DURATION = 10      # segundos
ATTACK_DURATION = 15      # segundos
RECOVERY_DURATION = 10    # segundos


def run_phase(name: str, cmd: str, duration: int):
    print(f"\n=== FASE: {name} ({duration}s) ===")
    print(f"Comando: {cmd}")
    # Executa em background e aguarda
    os.system(f"{cmd} &")
    time.sleep(duration)
    # Encerra processos filhos típicos
    os.system("pkill -f 'ping' || true")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=TARGET_IP)
    parser.add_argument("--normal", type=int, default=NORMAL_DURATION)
    parser.add_argument("--attack", type=int, default=ATTACK_DURATION)
    parser.add_argument("--recovery", type=int, default=RECOVERY_DURATION)
    args = parser.parse_args()

    print("Iniciando gerador de tráfego do Trabalho 4")
    print(f"Alvo: {args.target}")

    # Fase normal
    run_phase("NORMAL", f"ping -i 1 {args.target}", args.normal)

    # Fase de ataque (flood de pacotes)
    run_phase("ATAQUE", f"ping -f {args.target}", args.attack)

    # Fase de recuperação
    run_phase("RECUPERAÇÃO", f"ping -i 1 {args.target}", args.recovery)

    print("\n=== Gerador finalizado ===")


if __name__ == "__main__":
    main()
```

**Observações:**
- O comando `ping -f` requer permissões de root, mas dentro do host do Mininet isso normalmente é permitido.
- Alternativa: usar `hping3 --icmp --flood -a 10.0.0.1 <target>` se `hping3` estiver instalado.
- O gerador deve ser executado em `h1` para que o IP de origem seja `10.0.0.1`.

---

### Fase 4 — Atualização do Dashboard

**Arquivos:** `templates/index.html`, `static/dashboard.js`, `static/style.css`

#### 4.1 Novos elementos no `index.html`

Adicionar abaixo da tabela de valores atuais:

```html
<section id="policy-section" class="card hidden">
  <div class="card-header">
    <h2>▸ Decisões do Controlador</h2>
    <span class="card-hint">Ações automáticas baseadas em pacotes/s</span>
  </div>
  <div class="policy-grid">
    <div class="policy-metric">
      <span class="label">Limiar</span>
      <span class="value" id="policy-limit">—</span>
      <span class="unit">pkts/s</span>
    </div>
    <div class="policy-metric">
      <span class="label">Taxa Atual</span>
      <span class="value" id="policy-rate">—</span>
      <span class="unit">pkts/s</span>
    </div>
    <div class="policy-metric">
      <span class="label">Status</span>
      <span class="value" id="policy-status">Normal</span>
    </div>
  </div>
  <div class="blocked-ips">
    <h3>IPs Bloqueados</h3>
    <ul id="blocked-list">
      <li class="empty">Nenhum IP bloqueado</li>
    </ul>
  </div>
  <div class="action-log">
    <h3>Log de Ações</h3>
    <ul id="action-log-list"></ul>
  </div>
</section>
```

#### 4.2 Atualização do `dashboard.js`

Processar o campo `policy` nos eventos:

```javascript
function processPolicy(switchId, policy) {
  if (!policy) return;

  document.getElementById('policy-limit').textContent = policy.limit;
  document.getElementById('policy-rate').textContent = policy.pkts_per_sec;

  const statusEl = document.getElementById('policy-status');
  const blockedList = document.getElementById('blocked-list');
  const logList = document.getElementById('action-log-list');

  if (policy.blocked) {
    statusEl.textContent = 'Bloqueado';
    statusEl.className = 'status-blocked';
    blockedList.innerHTML = `<li>10.0.0.1 (SW${switchId})</li>`;
  } else {
    statusEl.textContent = 'Normal';
    statusEl.className = 'status-normal';
    blockedList.innerHTML = '<li class="empty">Nenhum IP bloqueado</li>';
  }

  if (policy.action) {
    const actionText = policy.action === 'block' ? 'BLOQUEIO' : 'DESBLOQUEIO';
    const li = document.createElement('li');
    li.textContent = `[${new Date().toLocaleTimeString()}] ${actionText} em SW${switchId} @ ${policy.pkts_per_sec} pkts/s`;
    logList.prepend(li);
  }
}

// Chamada dentro de socket.on('telemetry_update', ...)
socket.on('telemetry_update', (payload) => {
  processMetrics(payload.switch_id, payload.metrics);
  processPolicy(payload.switch_id, payload.policy);
});
```

#### 4.3 Estilos no `style.css`

Adicionar classes para status:

```css
.status-normal { color: #4cda9a; font-weight: bold; }
.status-blocked { color: #e05a5a; font-weight: bold; }

.policy-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 1rem;
}

.policy-metric {
  background: #111620;
  padding: 1rem;
  border-radius: 0.5rem;
  text-align: center;
}

.policy-metric .value {
  display: block;
  font-size: 1.5rem;
  color: #00e5c4;
  font-weight: bold;
}

.blocked-ips, .action-log {
  margin-top: 1rem;
}

.action-log ul {
  max-height: 150px;
  overflow-y: auto;
}
```

---

### Fase 5 — Topologia e Regras

**Arquivo:** `topo_trabalho3.py` (reutilizado sem alteração)

As regras de encaminhamento IPv4 continuam as mesmas:

```python
RULES = [
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.1/32', 'MyIngress.forward', ['1']),
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.2/32', 'MyIngress.forward', ['2']),
    ('s1', 'MyIngress.ipv4_lpm', '10.0.0.3/32', 'MyIngress.forward', ['3']),
]
```

**Novo arquivo:** `rules.txt` documentando todas as regras usadas:

```text
Regras estáticas (instaladas por topo_trabalho3.py):
  table_add MyIngress.ipv4_lpm MyIngress.forward 10.0.0.1/32 => 1
  table_add MyIngress.ipv4_lpm MyIngress.forward 10.0.0.2/32 => 2
  table_add MyIngress.ipv4_lpm MyIngress.forward 10.0.0.3/32 => 3

Regras dinâmicas (instaladas pelo controller_trabalho4.py):
  table_add MyIngress.drop_table MyIngress.drop 0x0A000001 =>
```

---

### Fase 6 — Relatório do Trabalho 4

**Arquivo:** `RELATORIO_TRABALHO4.md` (novo, não alterar `RELATORIO.md`)

Estrutura sugerida:

```markdown
# Relatório Técnico — Trabalho 4
## Controlador P4 com Decisão Automática sobre o Tráfego

**Disciplina:** Redes de Computadores  
**Grupo:** [preencher]  
**Data:** [preencher]

## 1. Introdução
Breve descrição do objetivo do Trabalho 4 e da ideia do ciclo de controle.

## 2. Arquitetura
Diagrama e explicação dos componentes.

## 3. Política de Decisão
- Métrica: pacotes/s
- Limiar: 120 pkts/s
- Ação: drop do IP 10.0.0.1
- Histerese: 2 amostras para bloquear, 5 para desbloquear

## 4. Modificações no Plano de Dados
Explicação da `drop_table` e das correções em `telemetry.p4`.

## 5. Controlador
Explicação do cálculo de taxa, decisão e instalação/remoção de regras via CLI.

## 6. Gerador de Tráfego
Descrição das fases normal/ataque/recuperação.

## 7. Demonstração e Resultados
Comandos de execução, prints/logs mostrando o bloqueio e desbloqueio automático.

## 8. Conclusão

## Referências
```

---

### Fase 7 — Testes e Validação

#### 7.1 Teste do P4

```bash
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json
```

#### 7.2 Teste do controlador em modo simulado

```bash
# Terminal 1
python3 controller_trabalho4.py

# Terminal 2
python3 telemetry_simulator.py --switches 1 --interval 1.0
```

Observar no dashboard/log se as taxas são calculadas e se ações são tomadas.

#### 7.3 Teste completo com Mininet

```bash
# Terminal 1 — compilar P4 (se ainda não compilado)
p4c --target bmv2 --arch v1model telemetry.p4 -o telemetry.json

# Terminal 2 — controlador
python3 controller_trabalho4.py

# Terminal 3 — topologia
sudo python3 topo_trabalho3.py

# Fora do Mininet (host root), iniciar exportador
python3 p4_register_exporter.py --thrift-port 9090 --switch-id 1 --controller 127.0.0.1

# Dentro do Mininet, no host h1, iniciar gerador de tráfego
mininet> h1 python3 traffic_generator.py
```

#### 7.4 Critérios de aceitação

- [ ] `telemetry.p4` compila sem erros.
- [ ] Controlador recebe telemetria e calcula `pkts/s`.
- [ ] Quando `pkts/s > 120` por 2 amostras, regra de drop para `10.0.0.1` é instalada.
- [ ] Tráfego de `h1` para `h2` é interrompido após o bloqueio.
- [ ] Tráfego entre `h2` e `h3` continua funcionando.
- [ ] Quando `pkts/s < 120` por 5 amostras, a regra é removida.
- [ ] Dashboard mostra taxa, status e log de ações.

---

## 6. Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| `simple_switch_CLI` não está no PATH do controlador | Verificar instalação do BMv2; usar caminho absoluto se necessário. |
| Handle da regra não é capturado corretamente | Fazer parsing robusto da saída do CLI; logar saída completa. |
| `ping -f` não gera taxa suficiente | Usar `hping3 --icmp --flood` como alternativa. |
| Taxa flutua muito, causando bloqueios/desbloqueios rápidos | Ajustar histerese e limiar durante os testes. |
| A `drop_table` é consultada antes do roteamento, mas a regra não entra | Confirmar que a tabela está declarada antes do `apply` e que o CLI usa o nome correto. |

---

## 7. Checklist de Entregáveis

- [ ] `telemetry.p4` corrigido e compilável
- [ ] `controller_trabalho4.py` implementado
- [ ] `traffic_generator.py` implementado
- [ ] `topo_trabalho3.py` reutilizado
- [ ] `p4_register_exporter.py` reutilizado
- [ ] Dashboard atualizado
- [ ] `rules.txt` criado
- [ ] `RELATORIO_TRABALHO4.md` criado
- [ ] `README.md` atualizado
- [ ] `grupo.txt` preenchido pelo usuário
