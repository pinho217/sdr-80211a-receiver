# Parâmetros OFDM do IEEE 802.11a (20 MHz)

## 1. Parâmetros fundamentais

O PHY do IEEE 802.11a usa OFDM com uma largura de banda nominal de 20 MHz. A
frequência de amostragem da implementação é, portanto, `20 Msps` (uma amostra
por cada 50 ns). A IFFT/FFT tem tamanho 64:

| Parâmetro | Valor |
|---|---:|
| Largura de banda de canal | 20 MHz |
| Espaçamento entre subportadoras, `Δf` | 312,5 kHz |
| Tamanho da IFFT/FFT | 64 |
| Subportadoras de dados | 48 |
| Subportadoras piloto | 4 |
| Subportadoras nulas (incluindo DC) | 12 |

O espaçamento resulta de `Δf = Fs/NFFT = 20 MHz/64 = 312,5 kHz`. Um símbolo
OFDM sem prefixo cíclico tem 64 amostras e dura `64/20 MHz = 3,2 µs`. O
prefixo cíclico (GI) acrescenta 16 amostras, ou `0,8 µs`, pelo que a duração
total transmitida de cada símbolo é 80 amostras, ou `4 µs`.

As 52 subportadoras ativas são:

- 48 de dados;
- 4 pilotos, em `k = -21, -7, +7, +21` (a convenção de índices é relativa à
  portadora DC).

As 12 restantes são nulas:

- `k = -32 ... -27`: seis subportadoras de guarda no extremo negativo;
- `k = 0`: portadora DC;
- `k = +27 ... +31`: cinco subportadoras de guarda no extremo positivo.

Esta contagem assimétrica nos extremos é consequência da representação
discreta de 64 pontos e, juntamente com a DC, totaliza 12 subportadoras nulas.
Na implementação de uma IFFT, os índices negativos são colocados nos bins
superiores (por exemplo, `-1` corresponde ao bin 63).

## 2. Por que existem subportadoras nulas?

As subportadoras de guarda deixam uma margem espectral entre o canal de 20 MHz
e os canais adjacentes. Isso reduz a energia fora da banda (spectral leakage)
e ajuda a cumprir a máscara espectral. A subportadora DC é anulada para evitar
que offset DC do conversor, leakage do oscilador local e outros componentes
contínuos dominem um bin da FFT. As subportadoras nulas não transportam bits,
logo não entram no mapeamento de 48 dados nem na decisão dos quatro pilotos.

## 3. Estrutura temporal

Cada símbolo de dados é formado por:

1. os últimos 16 samples do símbolo IFFT (prefixo cíclico);
2. os 64 samples da saída da IFFT.

O prefixo cíclico transforma a convolução do canal multipercurso numa
convolução circular, desde que o atraso do canal caiba nos 16 samples. Também
fornece uma região útil para sincronização e protege a FFT da transição entre
símbolos.

Antes do campo SIGNAL e dos dados existem dois treinos:

### Short Training Sequence (STS)

O STS tem 160 samples, isto é, 10 repetições de um padrão de 16 samples
(`10 × 0,8 µs = 8 µs`). A periodicidade de 16 samples permite calcular uma
autocorrelação de atraso 16. Assim, um recetor pode procurar simultaneamente
energia, estimar o início de uma trama e estimar o erro de frequência (CFO)
pela fase da autocorrelação. A repetição produz um patamar de correlação
durante o STS.

### Long Training Sequence (LTS)

Depois do STS vem o LTS: um intervalo de guarda de 32 samples seguido de duas
repetições de 64 samples (`32 + 64 + 64 = 160 samples`, ou 8 µs). Os dois
símbolos longos são usados para sincronização fina, estimação do canal por
subportadora e estimação mais precisa da frequência. O prefixo/intervalo de
guarda evita que a transição do início do treino contamine a FFT do primeiro
LTS.

## 4. Stream tags e message passing (Secção 2.1)

Em GNU Radio, uma stream é uma sequência contínua de itens, e cada item tem um
offset absoluto. Uma *stream tag* associa metadados a um offset sem interromper
o fluxo. No recetor, a tag `wifi_start` marca o offset de início de uma trama e
transporta o CFO estimado como valor `double`. Isto permite que blocos
posteriores, como o sincronizador longo e o equalizador, encontrem a mesma
trama sem depender de um número fixo de amostras ou de uma chamada de função
particular.

Tags são adequadas para eventos alinhados com samples: propagam a posição e
podem ser consumidas por blocos que continuam a processar a stream. No código
de `sync_short`, a política de propagação é desativada (`TPP_DONT`) porque o
bloco controla explicitamente a criação da tag e não deve copiar tags de
entrada potencialmente desalinhadas.

*Message passing* usa portas de mensagens (PMT) para objetos assíncronos e
discretos, por exemplo uma trama descodificada, uma indicação de erro ou uma
mensagem MAC. Ao contrário de uma stream, uma mensagem não representa uma
amostra em cada instante. A separação é útil: samples e tags transportam o
processamento temporal do PHY, enquanto mensagens transportam resultados e
eventos entre PHY, MAC e aplicações. O agendamento de GNU Radio pode chamar um
bloco muitas vezes e dividir uma trama por vários buffers; tags e mensagens
evitam assumir que uma fronteira de buffer coincide com uma fronteira de
trama.

