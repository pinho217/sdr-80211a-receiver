# Análise do OFDM Sync Short

Fonte analisada: [`gr-ieee802-11/lib/sync_short.cc`](../gr-ieee802-11/lib/sync_short.cc).
O bloco recebe três streams alinhadas:

1. amostras complexas `in`;
2. uma stream complexa `in_abs`, usada para a fase da correlação;
3. uma stream float `in_cor`, o coeficiente de correlação normalizado.

O cálculo de `in_cor` não é feito neste bloco. Tipicamente vem de um bloco que
implementa a autocorrelação de atraso 16 do STS e a normaliza pela potência.
O `sync_short` toma a decisão temporal e aplica a correção de frequência às
amostras copiadas.

## Máquina de estados

### SEARCH

No estado `SEARCH`, cada item é comparado com `d_threshold`. Quando
`in_cor[i] > threshold`, `d_plateau` é incrementado. Se houver um valor abaixo
do limiar, o contador volta a zero. Depois de `MIN_PLATEAU` itens consecutivos
acima do limiar, o bloco declara uma trama:

- muda para `COPY`;
- zera `d_copied`;
- calcula `d_freq_offset = arg(in_abs[i]) / 16`;
- insere uma stream tag `wifi_start` no offset de saída atual;
- guarda o CFO estimado no valor da tag.

O `arg(in_abs[i])` é a fase da correlação entre amostras separadas por 16
períodos. Dividir por 16 converte a rotação medida no atraso em rotação por
sample, que é o parâmetro usado na exponencial de correção.

### COPY

No estado `COPY`, as amostras são produzidas enquanto há entrada e espaço de
saída:

```text
out[o] = in[o] * exp(-j * d_freq_offset * d_copied)
```

Isto remove progressivamente a rotação causada pelo CFO. O estado mantém a
cópia até `MAX_SAMPLES = 540 × 80` samples, um limite de segurança que evita
ficar indefinidamente numa trama ou num falso positivo.

Enquanto copia, o bloco continua a observar `in_cor`. Um novo patamar só pode
iniciar outra trama quando `d_copied > MIN_GAP`, onde `MIN_GAP = 480` samples
(6 símbolos OFDM). Assim, uma correlação alta dentro do mesmo preâmbulo não
gera tags repetidas. Depois do novo patamar, a posição é reiniciada, o CFO é
estimado novamente e uma nova tag é inserida.

Ao atingir `MAX_SAMPLES`, o estado regressa a `SEARCH`. O bloco consome sempre
o número de itens efetivamente examinados (`consume_each`); em `SEARCH` não
produz saída, pois primeiro está a procurar o início.

## Threshold e plateau

O `threshold` é um limiar do coeficiente `in_cor`, não um limiar de potência.
Com a normalização usual, um valor perto de 1 significa forte periodicidade
de 16 samples, enquanto ruído ou sinal não periódico tende a valores menores.
O valor GRC predefinido é `0,8`. Aumentá-lo reduz falsos positivos, mas pode
perder tramas fracas; baixá-lo melhora a sensibilidade, mas torna a deteção
mais vulnerável a ruído, interferência e picos isolados.

`MIN_PLATEAU` é o número mínimo de amostras consecutivas acima do limiar
necessário para confirmar a deteção. O valor GRC predefinido é 2. Este
contador não mede a duração total do STS: é apenas uma confirmação temporal
para rejeitar um pico único. Se o coeficiente cair abaixo do limiar, a
deteção é reiniciada. O STS real contém muitas repetições de 16 samples, pelo
que tende a produzir um patamar longo e estável.

Há uma distinção importante entre os dois parâmetros:

- `threshold` controla *quanto* de correlação é considerado suficiente;
- `MIN_PLATEAU` controla *durante quanto tempo consecutivo* essa condição tem
  de persistir.

Na prática, ambos devem ser avaliados com diferentes níveis de SNR. Um
threshold fixo não substitui controlo de ganho ou normalização: clipping,
offsets e uma estimativa de potência muito baixa também alteram `in_cor`.

