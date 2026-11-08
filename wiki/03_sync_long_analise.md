# Análise do OFDM Sync Long

Fonte principal: [`gr-ieee802-11/lib/sync_long.cc`](../gr-ieee802-11/lib/sync_long.cc),
com a interface em [`include/ieee802_11/sync_long.h`](../gr-ieee802-11/include/ieee802_11/sync_long.h)
e os parâmetros GRC em [`grc/ieee802_11_sync_long.block.yml`](../gr-ieee802-11/grc/ieee802_11_sync_long.block.yml).

O bloco recebe a stream complexa normal e uma cópia atrasada. A entrada
`in_delayed` é ligada, no grafo PHY, a um atraso de `SYNC_LENGTH` amostras. O
valor predefinido do bloco GRC é 240 samples (o exemplo hierárquico usa 320).

## 1. Frequency Offset Correction (Secção 2.3)

O oscilador local do emissor e o do recetor são independentes. As diferenças
de frequência de cristal, tolerância, temperatura, envelhecimento e Doppler
fazem com que o recetor veja uma portadora deslocada relativamente à sua
referência. Esse carrier-frequency offset (CFO) causa uma rotação de fase
progressiva:

`r[n] = s[n] exp(j 2π Δf n / Fs) + w[n]`.

### Estimativa (Equação 4)

O LTS contém duas cópias consecutivas do mesmo símbolo de 64 samples. Se
`l[n]` e `l[n+64]` são as duas cópias, a soma de produtos é:

`R = Σ(n=0..63) l[n] conj(l[n+64])`.

Na presença de CFO, `arg(R)` é a rotação acumulada em 64 samples. Assim, na
forma usada pelo artigo:

`df = arg(R) / 64`.

No código, depois de filtrar, `search_frame_start()` seleciona os picos da
correlação, procura dois picos separados por 64 (aceitando 63 ou 65 por
discretização) e calcula `d_freq_offset = arg(first * conj(second)) / diff`.
Isto é a mesma Equação (4), com `diff` igual a 64, 63 ou 65.

Somar os produtos complexos antes de obter o argumento é mais robusto do que
calcular e depois fazer a média de `arg(l[n] conj(l[n+64]))`. Amostras com
ruído podem ter magnitude pequena e argumentos praticamente aleatórios; a
soma vetorial dá mais peso às amostras com sinal forte e cancela parcialmente
o ruído de fase. A média direta de ângulos também tem o problema adicional da
descontinuidade em `-π/π`.

### Correção (Equação 5)

Cada sample copiado é multiplicado por:

`r_corr[n] = r[n] exp(-j n df)`.

No estado `COPY`, isto aparece como
`in_delayed[i] * exp(gr_complex(0, d_offset * d_freq_offset))`. O sinal e a
referência de índice correspondem à convenção da implementação e removem a
rotação estimada. A tag `wifi_start` contém o residual
`d_freq_offset_short - d_freq_offset`, combinando a estimativa grosseira do
`sync_short` com a estimativa fina do LTS.

`SYNC_LENGTH` é configurável pela API `sync_long::make()` e pelo parâmetro GRC.
Controla quantas amostras são acumuladas no vetor de correlação antes de
procurar os picos. O FIR usa o vetor `LONG` de 64 amostras definido no fim de
`sync_long.cc`; esse é o filtro adaptado do LTS.

### Papel da entrada atrasada

Durante a procura, `in` fornece a janela na qual o FIR é calculado. Quando a
posição do início é conhecida, o bloco precisa emitir samples desde esse
ponto, mas já consumiu a janela de procura. `in_delayed` fornece exatamente
essa história temporal (normalmente 240 samples atrás), permitindo copiar a
amostra alinhada sem reintroduzir uma latência irregular. A correção é
aplicada ao sinal atrasado. A saída também remove o intervalo que não
pertence ao preâmbulo/dados: copia os primeiros 128 samples a partir do
início e, depois, rejeita os 16 samples de prefixo de cada símbolo de 80.

