# Week 5 — Correção de fase residual (Secção 2.5) e equalização (Secção 2.6)

Código: [`frame_equalizer_impl.cc`](https://github.com/bastibl/gr-ieee802-11/blob/ad0598e/lib/frame_equalizer_impl.cc)
e [`lib/equalizer/`](https://github.com/bastibl/gr-ieee802-11/tree/ad0598e/lib/equalizer)
(`base`, `ls`, `lms`, `comb`, `sta`), commit `ad0598e`. As linhas citadas são desse commit.
Os números de EVM e SNR vêm de [`scripts/metricas_*.csv`](../scripts/) (monitor v2,
execuções com o estimador por defeito do `wifi_rx_headless.py`, `--chan-est 0` = LS).
Os valores de `epsilon` vêm do log `--debug` de `frames_1248k.bin`.

O guião divide o tema: as perguntas da **Week 5** (fase) respondem-se nas §1–3; as da
**Week 6** (módulo *OFDM Equalize Symbols*) nas §4–6. A pergunta "o que faz o *Stream to
Vector*" pertence à Week 4 (§5 de [`03_sync_long_alinhamento.md`](03_sync_long_alinhamento.md)).

## 0. Onde este bloco fica no fluxo

`sync_long` (sem CP) → `Stream to Vector (64)` → `FFT 64` → **`frame_equalizer`** → `decode_mac`.
No `wifi_phy_hier.py` a FFT é `fft.fft_vcc(64, True, window.rectangular(64), True, 1)`:
direta, janela retangular e `shift=True`, portanto o DC fica no índice 32 e
`i − 32` é o índice de subportadora (−32…+31). O `frame_equalizer` recebe itens de
`64 × complex` e produz itens de **48 bytes** (`frame_equalizer_impl.cc:41-42`), um por
símbolo OFDM de dados.

A ordem dos símbolos que o bloco vê é fixa: `d_current_symbol` = 0 e 1 são os dois LTS
(`T1`, `T2`), 2 é o campo SIGNAL e ≥ 3 são os dados (`frame_equalizer_impl.cc:141,
171, 205, 216, 250`). O contador volta a 0 quando chega a etiqueta `wifi_start`
(`:139-151`).

## 1. Porque é preciso corrigir a fase? (Secção 2.5)

Depois da correção de CFO (Weeks 2–3) e do alinhamento (Week 4) ficam três efeitos
que rodam as subportadoras:

- **Erro de fase comum** a todas as subportadoras de um símbolo: CFO residual e
  ruído de fase do oscilador. É o termo constante.
- **Sampling-frequency offset (SFO)**: os relógios de amostragem do emissor e do
  recetor não estão sincronizados (o artigo diz-o explicitamente). A janela da FFT
  desliza de símbolo para símbolo e o deslocamento temporal `τ` produz uma fase
  `2π·k·τ/64` na subportadora `k`, ou seja, **linear com a frequência** e a crescer
  ao longo da trama.
- **Alinhamento imperfeito do símbolo** (Week 4): um erro fixo de `τ` amostras dá a
  mesma rampa linear, constante entre símbolos.

Modelo por subportadora: `Y[k] = H[k]·X[k]·exp(j(β + α·k))`, com `β` o termo comum
e `α` a inclinação.
Sem correção, as constelações rodam e, para 16-QAM, em que a decisão é por limiares
fixos (`constellations_impl.cc:116-128`), uma rotação deixa os pontos nas regiões de
decisão erradas.

## 2. Onde está o código que estima o desfasamento?

Tudo em `frame_equalizer_impl.cc::general_work`, por cada símbolo recebido:

| Passo | Linhas | O que faz |
|---|---|---|
| Inicializar `epsilon0` | `:139-151` | Na etiqueta `wifi_start`: `d_epsilon0 = tag·bw/(2π·freq)`; `d_er = 0` |
| Rampa de fase (SFO) | `:161-166` | multiplica `current_symbol[i]` por `exp(j·2π·n·80·(ε0+er)·(i−32)/64)` |
| Piloto de referência | `:168` | `p = POLARITY[(n−2) % 127]` |
| Fase comum `beta` | `:170-178` | `arg` da soma dos 4 pilotos já multiplicados pelo sinal esperado |
| Erro `er` | `:180-185` | `arg` de Σ conj(piloto anterior)·piloto atual; depois `er *= bw/(2π·freq·80)` |
| Guardar pilotos | `:187-197` | `d_prev_pilots` para o símbolo seguinte |
| Aplicar `beta` | `:199-202` | multiplica os 64 bins por `exp(−j·beta)` |
| Atualizar `d_er` | `:204-209` | média exponencial, `α = 0,1` (só para `n ≥ 2`) |

**Não há regressão linear.** O artigo (§2.5) fala em estimar a fase "por regressão linear"
a partir dos quatro pilotos; esta versão não o faz (não existe nenhuma regressão em
`lib/` nem em `include/`). A reta de fase é construída com duas peças separadas: o termo
constante vem de `beta` (estimado em cada símbolo) e a inclinação vem de `ε0 + d_er`
(estimada ao longo da trama). `d_er` **não é um LMS**: é uma média exponencial
`d_er ← 0,9·d_er + 0,1·er`.

**Pilotos e polaridade.** Os 4 pilotos estão nos bins `11, 25, 39, 53` (subportadoras
−21, −7, +7, +21). Nos símbolos de dados o valor esperado é `p·[+1, +1, +1, −1]`, com
`p = POLARITY[(n−2) % 127]` (127 valores, `equalizer/base.cc:30-37`). Por isso o
quarto piloto entra com `−p` (`:177`, `:183`). Nos **dois LTS** (`n < 2`) o padrão
é outro: `current_symbol[11] − current_symbol[25] + current_symbol[39] + current_symbol[53]`
(`:172`), que corresponde a `[+1, −1, +1, +1]`; confirma-se com a tabela `LONG` de
`equalizer/base.cc:24-28`, onde `LONG[11], LONG[25], LONG[39], LONG[53] = 1, −1, 1, 1`.

**Conversão de `er` em `epsilon`.** Uma variação de fase `Δφ` entre símbolos
consecutivos (80 amostras) corresponde a `Δf = Δφ·bw/(2π·80)`; dividindo por `freq` fica
um erro **relativo**, `er = Δφ·bw/(2π·freq·80)` (`:185`). O código trata assim o CFO
como se viesse da mesma referência que o relógio de amostragem, e usa `ε` tanto para a
fase como para a rampa (inferência nossa a partir da fórmula; o código não o comenta).
Por isso `freq` e `bw` têm de estar certos: o `wifi_rx_headless.py` passa
`freq = 5,18e9` e `bw = samp_rate = 20e6` à `frame_equalizer`
(`wifi_rx_headless.py:99`); o valor por defeito do `wifi_phy_hier.py` é `bandwidth = 10e6`
(802.11p).

### Exemplo medido

No log `--debug` de `frames_1248k.bin` (`freq = 5,18 GHz`) aparecem três linhas
`epsilon:`, uma por etiqueta `wifi_start`. A última, `4,50828e-06`, multiplicada por
`5,18e9` dá **23,35 kHz**, igual ao `cfo_khz` da linha correspondente (ACK) em
`metricas_frames_1248k.csv`. A primeira, `1,64569e-06`, dá 8,52 kHz, o CFO total que o
porte Python mede para a trama 1 248 010 (Week 4).

Cálculo de ordem de grandeza (não é uma medição): com `ε = 4,508e-6`, no último símbolo de uma trama de 27 símbolos de dados (`n = 29`) o deslocamento
acumulado é `29·80·4,508e-6 ≈ 0,0105` amostras, o que dá na subportadora mais externa
(`|i−32| = 26`) uma fase `2π·26·0,0105/64 ≈ 0,027 rad ≈ 1,5°`.

## 3. Como é corrigida a fase: que valores são alterados?

Dois ajustes sucessivos sobre o vetor `current_symbol[64]` do símbolo atual (que é
alterado **no sítio**), antes de qualquer equalização:

1. **Rampa de SFO** (`:162-166`): cada bin `i` é multiplicado por uma fase proporcional
   a `(i − 32)` e ao número de símbolos já passados (`n·80`).
2. **Fase comum** (`:200-202`): os 64 bins são multiplicados por `exp(−j·beta)`.

Passam também por estes dois ajustes os símbolos `n = 0` e `n = 1` (os LTS), por isso a
estimativa de canal da §4 já é feita sobre símbolos com a fase comum removida. Além dos
64 bins, mudam dois estados internos: `d_prev_pilots` (`:187-197`) e `d_er` (`:208`). O
valor `beta` do símbolo SIGNAL segue ainda na etiqueta `beta` publicada com o SIGNAL (`:232`).

## 4. O que faz o módulo *OFDM Equalize Symbols* no domínio da frequência? (Secção 2.6)

Recebe a FFT de cada símbolo, já com fase corrigida, e:

1. **Estima o canal `H[k]`** a partir dos LTS e/ou dos pilotos (consoante o estimador, §5).
2. **Equaliza**: `X̂[k] = Y[k] / H[k]` para cada subportadora de dados.
3. **Decide**: `decision_maker` da constelação ativa converte `X̂[k]` numa palavra de bits.

Como o canal é estimado a partir dos LTS que passaram pela mesma FFT (não normalizada,
janela retangular), a escala absoluta da FFT cancela-se na divisão `Y/H`.

## 5. Porque o artigo dizia BPSK/QPSK, e o que mudou no código atual

O artigo (Secção 2.6) diz que a implementação **assumia a magnitude das portadoras com
forma de sinc** e corrigia com base nisso, que essa forma **dependia também do emissor**,
e que isso "restringe o recetor a BPSK e QPSK". A razão é que em BPSK/QPSK a informação
está só na fase, por isso um erro de magnitude é tolerável. Em 16-QAM/64-QAM a amplitude
transporta bits e as decisões são por limiares de magnitude (em 16-QAM, `0` e `±2/√10`;
`constellations_impl.cc:89, 116-128`).

**No código deste repositório essa correção por sinc já não existe** (a pesquisa por
`sinc` em `lib/` e `include/` não devolve nada). Cada subportadora tem a sua `H[k]`,
estimada por um de quatro estimadores, escolhidos por `set_algorithm`
(`frame_equalizer_impl.cc:70-96`; enum `LS=0, LMS=1, COMB=2, STA=3`):

| Estimador | Como estima `H[k]` | Atualiza durante a trama? | Ficheiro |
|---|---|---|---|
| `ls` | `(LTS₁+LTS₂)/(2·LONG[k])` (`d_H[i] += in[i]; d_H[i] /= LONG[i]·2`) | Não | `equalizer/ls.cc:34-45` |
| `lms` | como `ls`, depois, por decisão: `d_H ← 0,5·d_H + 0,5·in/ponto` | Sim, `α = 0,5` | `equalizer/lms.cc:49-64`, `lms.h:41` |
| `comb` | interpolação linear entre os 4 pilotos de cada símbolo; `d_H ← 0,8·d_H + 0,2·H` | Sim, `α = 0,2` | `equalizer/comb.cc:29-69`, `comb.h:38` |
| `sta` | como `ls`, depois média espectral de `in/ponto` numa janela de ±2 subportadoras, `α = 0,5` | Sim | `equalizer/sta.cc:51-95`, `sta.h:41-42` |

No estimador `ls` a SNR vem dos dois LTS: `d_snr = 10·log10(Σ|H₁+H₂|² / Σ|H₁−H₂|² / 2)`
(`ls.cc:41-47`). O `comb::get_snr()` devolve a **constante 42** (`comb.cc`, última linha),
por isso o `snr_ls_db` dos CSVs só tem significado com `ls`, `lms` ou `sta`.

**Evidência de que a versão atual descodifica 16-QAM.** Nas nossas gravações
(estimador LS), todas estas linhas são PDUs com CRC válido, a 24 Mb/s = 16-QAM 1/2
(`encoding = 4`):

| Gravação | Modulação | PDUs | com associação fiável¹ | EVM mediano | SNR_LS mediano |
|---|---|---|---|---|---|
| Sample1 (5,18 GHz) | BPSK 6 Mb/s | 336 | 223 | 0,130 | 28,8 dB |
| | QPSK 12 Mb/s | 17 | 9 | 0,126 | 30,2 dB |
| | **16-QAM 24 Mb/s** | **295** | 169 | 0,117 | 27,9 dB |
| Sample2 (2,437 GHz) | BPSK 6 Mb/s | 88 | 61 | 0,647² | 10,6 dB |
| | QPSK 12 Mb/s | 34 | 21 | 0,386² | 12,4 dB |
| | **16-QAM 24 Mb/s** | **50** | 36 | 0,103 | 26,7 dB |
| Sample3 (5,5 GHz) | BPSK 6 Mb/s | 6 | 4 | 0,863² | 4,9 dB |
| | QPSK 12 / 18 Mb/s | 6 / 88 | 6 / 68 | 0,383 / 0,326 | 14,2 / 18,0 dB |
| | **16-QAM 24 Mb/s** | **311** | 174 | 0,249 | 20,0 dB |

¹ Associação `exact` ou `tail` e EVM ≤ 1 (ver [`06_bloco_qualidade_snr.md`](06_bloco_qualidade_snr.md)).
² Valores altos de EVM com CRC válido indicam que a associação símbolos↔PDU falha nessas
linhas (problema em aberto); **não os usamos como evidência**. O número de PDUs com CRC
válido é independente disso.

A maior parte das tramas 16-QAM são de controlo curtas (ACK, CTS, RTS, Block-ACK, 14–32 bytes,
2–3 símbolos). Mas há também tramas longas: na Sample3 há 138 *beacons* a 16-QAM, com PSDU
até 327 bytes (por exemplo 318 bytes em 27 símbolos, 52 ocorrências), e as 98 linhas com mais
de 2 símbolos e associação fiável têm EVM mediano 0,247. Em Sample1, as 100 linhas com mais de 2 símbolos e associação
fiável têm EVM mediano 0,118, que corresponde a 18,6 dB. Os dois
estimadores medem coisas diferentes (o LS usa só o par de LTS; o EVM usa os símbolos de dados),
por isso não têm de coincidir. Não há linhas 64-QAM nestas gravações, por isso **não
temos evidência de 64-QAM**.

A limitação do artigo era, portanto, a correção de magnitude por sinc; a atual substitui-a por
uma estimativa por subportadora a partir do LTS conhecido. Uma consequência verificável no
código: o `ls` não atualiza `H[k]` depois dos LTS, logo assume um canal constante durante
toda a trama (`ls.cc:49-61` só lê `d_H`).

## 6. Que outras funções tem o bloco?

- **Remover DC, guardas e pilotos: 64 → 48 subportadoras.** Em `ls`, `lms` e `sta`, o ciclo salta `i < 6`, `i > 58`,
  `i = 32` (DC) e `i ∈ {11, 25, 39, 53}` (pilotos), ficando 48 bins por símbolo
  (`ls.cc:51-61`). Os 52 bins ocupados (48 + 4 pilotos) são também os que `get_csi()` devolve
  (`base.cc:39-50`).
- **Decodificar o campo SIGNAL.** No símbolo `n = 2`, `decode_signal_field`
  (chamada em `frame_equalizer_impl.cc:218`, definida em `:266`) desentrelaça e descodifica
  o campo; `parse_signal` (`:285-367`) lê 4 bits de débito, 12 de comprimento e verifica a paridade (`:303`). Se for
  válido, define a modulação (`d_frame_mod`), `d_frame_bytes` e `d_frame_symbols`.
  Se a paridade ou o débito falham, o resto da trama é ignorado (`:154`).
- **Etiquetas** (`:220-248`, publicadas quando o SIGNAL é válido): `frame bytes`, `encoding`, `snr`,
  `nominal frequency`, `frequency offset`, `beta` e `csi`. O `decode_mac` e o monitor de qualidade
  usam-nas.
- **Publicar a constelação.** Para cada símbolo de dados (`n > 2`) envia na mensagem
  `symbols` os 48 pontos equalizados (`:250-255`), que é o que o monitor usa para calcular o EVM.
- **Suportar 4 modulações.** `parse_signal` escolhe BPSK, QPSK, 16-QAM ou 64-QAM conforme
  o débito do SIGNAL (`:309-358`) e guarda o índice `encoding` (0–7). As mensagens de debug desse `switch`
  (`dout << "Encoding: …"`) não são fiáveis, por isso a referência é o `encoding` e a tabela do
  standard (ver `05_descodificacao_mac.md`), não essas mensagens.
