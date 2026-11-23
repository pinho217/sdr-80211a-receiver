# Análise do OFDM Sync Short (Week 2)

Fonte analisada: [`gr-ieee802-11/lib/sync_short.cc`](../gr-ieee802-11/lib/sync_short.cc) (commit `ad0598e`;
as linhas citadas são desse ficheiro). O cálculo do coeficiente de correlação está descrito em
[01_parametros_ofdm.md](01_parametros_ofdm.md) §5. Os números desta página vêm de um porte em Python
do bloco (`scripts/sdr80211.py`), conferido com os blocos reais (o CFO medido pelo porte, 23,4 kHz,
coincide com o `cfo_khz` de 23,35 kHz do CSV, e o `epsilon` do log do `frame_equalizer` dá o mesmo valor).

## 1. O que o bloco faz

O `sync_short` é uma "válvula": só deixa passar amostras depois de detetar o início de uma trama.
Tem três entradas alinhadas (`sync_short.cc`, `general_work`):

1. `in`: as amostras complexas (saída do `delay` de 16);
2. `in_abs`: o complexo `a[n]` da autocorrelação (saída do `moving_average_cc`), de onde tira a fase;
3. `in_cor`: o coeficiente normalizado `c[n]`, float (saída do `divide`).

Quando deteta uma trama, o bloco faz três coisas: **(a)** estima o CFO do STS, `arg(in_abs[i])/16`;
**(b)** põe a tag `wifi_start` à saída, com esse valor; **(c)** copia as amostras para a saída
multiplicadas por `exp(−j·CFO·n)`, o que remove a rotação causada pelo desvio de frequência.
O cálculo de `in_cor` **não** é feito neste bloco; vem dos oito blocos descritos em
[01_parametros_ofdm.md](01_parametros_ofdm.md) §5.

## 2. Máquina de estados

### SEARCH (linhas 74–83)

Cada item é comparado com `d_threshold` por uma desigualdade **estrita** (`in_cor[i] > d_threshold`).
Se for maior, `d_plateau` sobe até `MIN_PLATEAU`; se um valor ficar abaixo, o contador volta a zero.
Com `d_plateau == MIN_PLATEAU` e mais uma amostra acima do limiar, o bloco declara uma trama, ou seja,
dispara na **(`MIN_PLATEAU`+1)-ésima amostra consecutiva**. Com `min_plateau = 2` são três, como diz o
artigo ("three consecutive samples"). Ao disparar:

- passa a `COPY` e zera `d_copied`;
- calcula `d_freq_offset = arg(in_abs[i]) / 16` (linha 81);
- insere a tag `wifi_start` à saída, com esse valor (linha 83, `insert_tag`, definida em `:139-146`);
- a política de propagação de tags é `TPP_DONT` (linha 47), por isso só as tags criadas aqui seguem.

`arg(in_abs)` é a fase da correlação entre amostras separadas por 16; dividir por 16 dá a rotação por
amostra.

### COPY (linhas 99–124)

```text
out[o] = in[o] * exp(-j * d_freq_offset * d_copied)          (linha 119)
```

A cópia dura até `MAX_SAMPLES = 540 × 80` amostras (limite de segurança, linha 26). Enquanto copia, o
bloco continua a observar `in_cor`. Um novo patamar só dispara outra trama se `d_copied > MIN_GAP`, com
`MIN_GAP = 480` (linha 105); nesse caso estima o CFO de novo e insere uma nova tag (linhas 108–110).
Ao atingir `MAX_SAMPLES`, volta a `SEARCH`.

## 3. O que significam os limiares

**`threshold`** é um limiar sobre o coeficiente `in_cor`, não sobre a potência. O valor máximo do
coeficiente **não é 1**: como a janela de potência tem `N + 16` amostras e a da correlação `N`, o patamar
ideal do STS é `N/(N+16)`. Medido numa trama real (ACK de alta SNR):

| `N` (janela) | Patamar ideal | Patamar medido | Amostras acima de 90 % do ideal | Amostras acima de 0,56 |
|---:|---:|---:|---:|---:|
| 16 | 0,500 | 0,500 | 133 | 0 |
| 48 | 0,750 | 0,748 | 118 | 137 |
| 64 | 0,800 | 0,799 | 111 | 143 |

O limiar tem de ficar **abaixo** do patamar. O exemplo do repositório (`wifi_rx.grc`) usa
**0,56**, adequado a `N = 48`. Um limiar de 0,8 não dispara com `N = 48` e, com a comparação estrita,
um limiar de 0,5 também não dispara com `N = 16` (o patamar fica em 0,500).

**`MIN_PLATEAU`** é o número de amostras consecutivas acima do limiar que confirmam a deteção; rejeita um
pico isolado, mas não mede a duração do STS. Tem um efeito secundário: **atrasa o disparo** e, com ele, o
ponto onde se mede o CFO. No log com `--min-plateau 40` o disparo passou de 1010 para 1048
(+38 = 40 − 2 amostras), e o `sync_long` encontrou o início da trama 38 amostras mais cedo
(`frame start at 130` em vez de 168).

Distinção: `threshold` controla *quanto* de correlação é suficiente; `MIN_PLATEAU` controla *durante
quanto tempo seguido*.

## 4. Efeito de variar os limiares

Varrimento sobre as primeiras 10 M amostras da Sample1 (`scripts/sweep_threshold.py`, `min_plateau = 2`,
`SYNC_LENGTH = 320`, rácio mínimo de pico 5; "tramas" são disparos com par de LTS e rácio ≥ 5):

| `N` | Limiar | Disparos | Tramas | Falsos alarmes |
|---:|---:|---:|---:|---:|
| 48 | 0,56 | 1 244 | 10 | 1 234 |
| 48 | 0,7 | n/d | 8 | 873 |
| 48 | 0,8 | 0 | 0 | 0 |
| 16 | ≥ 0,5 | 0 | 0 | 0 |
| 16 | 0,4 | 2 046 | n/d | n/d |
| 16 | 0,3 | 7 157 | n/d | n/d |
| 64 | 0,56 | n/d | 12 | n/d |
| 64 | 0,8 | 120 | 5 | n/d |

Leitura: subir o limiar reduz os falsos alarmes mas perde tramas (de 10 para 8 em `N = 48`) e, ao
atingir o patamar, deixa de detetar qualquer coisa. Baixá-lo (`N = 16`, 0,3) dispara milhares de vezes em
ruído. Com `N = 64` o patamar (0,80) é igual ao limiar de 0,8, e a deteção fica marginal.
(`n/d` = não registado nesta execução.)

## 5. Limitações e casos em que falha

**1. Interferente periódico de banda estreita (falso alarme).** Um sinal periódico de 16 amostras
mantém o coeficiente alto sem ser uma trama. No excerto `sample1_4mb.bin` (500 000 amostras da Sample1) o
disparo 12 092 tem `c > 0,56` em 402 das 405 amostras seguintes (um STS verdadeiro dá cerca de 137),
com máximo 0,76. Como o patamar nunca desce, a regra do `MIN_GAP` volta a disparar logo que `d_copied` passa 480: nos primeiros
disparos, a intervalos de 481 a 488 amostras (12 092, 12 573, 13 061, 13 542, …): 229 disparos no excerto, 9 com par de LTS e nenhum com rácio de pico
≥ 5. O `sync_long` não encontra par e recorre a `d_frame_start = SYNC_LENGTH`. Figura:
`python3 scripts/plot_autocorrelation.py recordings/Sample1_20MHz_Channel36.bin --trigger 12092 --output wiki/figura_falso_alarme.png`.

**2. Proporção de falsos alarmes na gravação inteira.** Na Sample1 (200 M amostras, 10 s) houve 4 361
disparos, dos quais 3 502 (80 %) sem par de LTS; 3 346 deles (95 %) nas primeiras 26 M amostras. Foram
encontradas 859 tramas candidatas e 648 deram um PDU com CRC válido (cerca de 75 %).

**3. Segundo disparo dentro de uma trama.** O `MIN_GAP` impede disparos repetidos no preâmbulo, mas
uma região periódica *dentro* da trama (neste caso, um HT-STF de uma trama 802.11n) dispara outra
vez 574 amostras depois, e isso faz o `sync_long` abandonar a trama em curso. Detalhe em
[05_descodificacao_mac.md](05_descodificacao_mac.md) §9.

**4. Dependência da normalização.** O limiar útil depende de `N` e do atraso (patamar `N/(N+16)`); um
valor de outro fluxo não se transporta.

**5. Quantidade fixa de amostras.** O bloco copia até `MAX_SAMPLES`, independentemente do comprimento da
trama, e só aceita nova trama depois de `MIN_GAP`. O artigo já assinala que se perdem tramas muito próximas,
por exemplo um CTS logo a seguir a um RTS.

**6. Alcance do CFO.** Com atraso 16 e `Fs = 20 MHz`, a fase só é inequívoca para
`|CFO| < Fs/(2·16) = 625 kHz` (cálculo nosso). Em contrapartida, a autocorrelação não depende do CFO e custa
1 multiplicação por amostra; um filtro adaptado ao STS custaria 16 a 64 por amostra e seria sensível ao CFO.

Na prática, os dois parâmetros devem ser avaliados com vários níveis de SNR. Um limiar fixo não substitui
o controlo de ganho: *clipping*, desvios de DC e uma estimativa de potência muito baixa também alteram
`in_cor`.
