# Plan — CGA domain lexicon (0.4.0)

Status: `proposta`, revision 2. Written 2026-07-31, revised after Planning Adversarial rounds 1 and 2.

> **Nada neste documento foi executado.** "Gap incorporado" abaixo significa *o texto do plano
> foi corrigido para levar o achado em conta* — nunca *o defeito foi consertado*. Estado real
> verificado em 2026-07-31: as 157 ocorrências de LaTeX seguem no corpus, o fixture de
> adjudicação não existe, e nenhum dos quatro scripts da Sequência (`build_oov_queue.py`,
> `measure_domain_coverage.py`, `propose_from_wordnet.py`, `build_adjudication_fixture.py`)
> foi escrito. O único arquivo que este planejamento alterou é este aqui.

## REPLAN-CONSUMED — rodada 1

- source-review-round: 1
- gaps-incorporados: B1 (`verify_official` rejeitado pelo schema), B2 (anti-meta impossível de
  violar), B3 (números não reproduzíveis, margem ~0), A1 (fila mistura ausente com ambíguo),
  A2 (lado EN sem medição), A3 (Lei Zero sem CILI/OpenWN-PT)
- plano-alterado-em: objetivo central, tabela-gate inteira, D1, D2, D3, D5 e D6 novos, Lei Zero,
  sequência inteira, seção de riscos
- decisao-atualizada: **o alvo deixa de ser "200 conceitos" e passa a ser um pipeline de
  adjudicação medido**; a estimativa de cobertura sai do plano e passa a ser derivada por script
  commitado antes de qualquer autoria

## REPLAN-CONSUMED — rodada 2

- source-review-round: 2
- gaps-incorporados: fonte de candidatos inexistente para o corpus CGA (o gate de precisão
  devolveria `"not_run"`); marcação LaTeX vazando como pseudo-termo; monossemia no WordNet
  confundida com pertinência de domínio
- plano-alterado-em: D7, D8 e D9 novas; passo 1 ganha limpeza de LaTeX; passo 3b novo; gatilho
  de abortar passa de 2 para 3 condições, com `"not_run"` explicitamente reprovando
- decisao-atualizada: o reuso de `evaluate_auto_matches` continua, mas fica declarado que só a
  **pontuação** é reuso — a **geração de candidatos** precisa de fonte nova

## REPLAN-CONSUMED — rodada 3

- source-review-round: 3
- gaps-incorporados: **`R$`/`US$` colidindo com o delimitador `$` de LaTeX** (bloqueante);
  `generate()` com dois caminhos de propriedades diferentes; lista de stopwords manual ainda
  vazando `para`/`com`/`por`/`uma`; gatilho referenciando curva que o passo 2 não emitia
- plano-alterado-em: D7 ganha regra normativa com regex e gate de contagem de moeda; D8 passa a
  usar união NLTK + lista local; D9 escolhe `--golden` com teto parametrizado e explica por quê;
  passo 2 passa a emitir `curve[]`
- decisao-atualizada: o achado do `R$` é o mais grave de todo o loop de planejamento — corromperia
  o corpus congelado no passo 1, antes de qualquer gate existir

### Status do gate de planejamento

`PLAN-ADVERSARIAL-LOOP: 3/3 rodadas, status: PENDENTE`.

As três rodadas retornaram `REPLANEJAR`. O contrato mantém a execução bloqueada.

**Padrão que precisa ser dito, porque ele é a informação mais útil deste documento:** as rodadas
acharam 6, 2 e 4 defeitos — e os das rodadas 2 e 3 estavam no texto escrito para corrigir a
rodada anterior. O loop está encontrando defeitos em prosa que descreve código futuro, e cada
correção cria prosa nova para revisar. O próprio revisor nomeou a tendência: 4 passos de
infraestrutura contra 2 de conteúdo, contra ~2 na revisão 1.

Isso não invalida o loop — os 12 defeitos eram reais, e o do `R$` teria destruído dado. Mas
sinaliza que o retorno agora está em executar os gates, não em revisar mais texto: o `R$` seria
pego por um teste de contagem de moeda em segundos, e o teto de 40 do `generate()` aparece na
primeira execução do passo 3b. Continuar revisando prosa é medir a descrição do remédio em vez
de tomá-lo.

## Central objective — o plano é verificado contra ISTO

> A normalização do corpus real do projeto deixa de ser majoritariamente cega, **sem** que a
> cobertura suba por afrouxamento de abstenção, e **com** o lado inglês de cada conceito
> ancorado em evidência em vez de inventado.

### Critério falsificável, fixado antes de qualquer trabalho

| # | Métrica | Baseline 0.3.0 | Alvo 0.4.0 | Por que essa linha existe |
|---|---|---:|---:|---|
| 1 | Ocorrências OOV resolvidas no corpus congelado | 0 % | **derivado no passo 2, não fixado aqui** | ver D6 |
| 2 | **Precisão de mapeamento** em amostra adjudicada às cegas | — | **≥ 95 %** | a linha que a revisão provou faltar |
| 3 | Sentenças `accepted` cujo conceito é errado na amostra | — | **0** | anti-meta real |
| 4 | Conceitos com `pref` EN sem evidência (nem ILI, nem apostila) | — | **0** | o lado EN da âncora |
| 5 | `validate-registry` | valid | valid | contrato |

**A linha 2 é a anti-meta de verdade.** A revisão provou que a anti-meta da revisão 1
(`accepted` com 0 conceitos permanece 0) é impossível de violar: `normalizer.py:579-583` só
emite `accepted` quando `events` é não-vazio, e `concepts` é derivado de `events` — logo
`accepted` com 0 conceitos não existe em nenhum caminho de código, com qualquer registro.
Ela media nada. Precisão em amostra adjudicada mede o que ela pretendia medir: se subir a
cobertura autorando `valor`/`forma`/`caso` como conceitos genéricos, a precisão cai.

**E ela não precisa ser construída — o maquinário já existe neste repositório.** Verificado
antes de planejar (Lei Zero aplicada ao próprio plano, que na revisão 1 ia reimplementá-la):

| Peça | Onde | O que faz |
|---|---|---|
| `evaluate_auto_matches` | `src/semantic_normalizer/evaluator.py:414` | conta TP/FP/FN/UNCERTAIN e emite `auto_match_precision`; ocorrência não adjudicada permanece `UNCERTAIN`, nunca vira acerto |
| `generate_auto_match_candidates.py` | `scripts/` | gera candidatos e o lote cego pendente |
| baseline adjudicado | `reports/dev-auto-match-evaluation-final.json` | TP=137, FP=0, FN=0, `selective_coverage` 89,5 % |

A linha 2 do gate é literalmente `auto_match_precision` desse módulo, com o mesmo protocolo de
adjudicação cega que já produziu o baseline de 137 ocorrências. Nada novo é escrito para medir
precisão; o passo 4 alimenta o gerador existente com as ocorrências do batch.

Consequência para o passo 3 do `Sequence`: o snapshot de adjudicação está hash-vinculado ao
registry 2.0.0 (`tests/test_o3_evaluator.py` faz `skipUnless` sobre isso). Mudar o registro para
2.2.0 exige **nova rodada de adjudicação cega**, não reaproveitar a antiga — o teste que hoje
pula é o mesmo que volta a valer quando a nova adjudicação existir.

## Context brief

### Lei Zero — refeita, com medição

A revisão apontou que `docs/data-governance.md:24-25` já nomeava CILI e OpenWN-PT/OMW como
candidatos e a tabela da revisão 1 os ignorava. Corrigido, e **o resultado mudou o plano**:

| Fonte | Verificado | Resultado |
|---|---|---|
| **OpenWN-PT (`own-pt:1.0.0`) + OWN-EN via ILI** | instalado e medido contra a fila real | **80 % do top-200** tem synset PT, e **100 % desses têm ILI** ligando ao inglês |
| Glossário ANBIMA | anbima.com.br/pt_br/autorregular/glossario-anbima.htm | descontinuado pela própria ANBIMA |
| Dicionário CVM | gov.br/cvm/…/dicionario-cvm | ~20 termos |
| Portal do Investidor | investidor.gov.br/glossario.html | 301 para a raiz, aposentado |
| Definições inline do corpus | regex sobre `cga-2026-markdown/` | 51 pares, extração ruidosa |

**Mas 80 % de cobertura não é 80 % de importabilidade.** Medido:

| Estrato do top-200 | Termos | Sentidos médios | Tratamento |
|---|---:|---:|---|
| Monossêmico no OpenWN-PT | **18** | 1 | import automático, ainda revisado |
| Polissêmico | **137** | **6,5** | **adjudicação de sentido, um a um** |
| Ausente | 45 | — | autoria com âncora da apostila |

Reproduzível: `scratchpad/repro_wordnet_strata.py`, com corpus glob, regra de sentença, filtro
de comprimento e lista de stopwords **explícitos no arquivo**. Saída: 1283 sentenças, 3880
termos OOV distintos, 18260 ocorrências, 3399 grupos, cobertura OpenWN-PT 77,5 %.

**Uma nota que pesa mais que os números:** a revisão 1 acusou que a tabela de dimensionamento
não reproduzia. Ao escrever o script com os parâmetros que eu havia *documentado*, os números
mudaram — 3880 em vez de 3957 termos, 18260 em vez de 17483 ocorrências, 18/137/45 em vez de
17/144/39, 77,5 % em vez de 80 %. A causa é banal e é exatamente a acusação: a primeira medição
usou filtro `60<len<240` e a documentada é `40<len<300`. **O revisor estava certo, e eu
reproduzi o erro contra mim mesmo.** É o argumento definitivo para D6: nenhum número de
dimensionamento pertence a este documento; todos vêm de script versionado.

O primeiro synset é sistematicamente errado para finanças. Medido, não suposto:

```
carteira -> bag          (7 sentidos)   deveria ser portfolio
fundo    -> deep         (10, adjetivo) deveria ser fund
retorno  -> homecoming   (6)            deveria ser return
ativo    -> active       (4, adjetivo)  deveria ser asset
risco    -> danger       (7)            deveria ser risk
título   -> claim        (8)            deveria ser bond
```

Um importador que pegue `synsets(t)[0]` envenena o registro com erros graves. **OpenWN-PT entra
como gerador de candidatos alimentando o fluxo de reconciliação que a skill já tem, não como
fonte de verdade.** Isso é reuso real — o inventário de sentidos e a ponte EN vêm de fora — sem
delegar a decisão semântica a uma fonte que não conhece o domínio.

### O defeito na própria fila, medido

A fila que dimensionou a revisão 1 foi construída com um lematizador de sufixo escrito na hora.
Ele produz não-palavras, e não em quantidade desprezível: `açõ`, `poi`, `podemo`, `temo`,
`diferent`, `ess`, `meno`, `doi`, `figur`, `vamo`, `dua`, `tai` aparecem **no top-200**. Cerca de
10 % da fila que eu ia autorar não são palavras do português.

A revisão também mostrou que os totais só se aproximavam dos declarados depois de uma lista de
stopwords que não existe em nenhum arquivo do repositório, e que a curva de cobertura reproduzida
de forma independente fica **4-8 pontos acima** em todo checkpoint. Com o alvo "≥40 %" a 0,3
ponto da própria estimativa e variação de metodologia de ≥7 pontos, o gate era auto-cumprimento.

### `deve`/`pode` não são vocabulário ausente

Verificado em `registry.jsonl`: `deve` já é forma de `modality.obligation` e
`modality.logical_necessity`; `pode` de `modality.capability`, `modality.permission` e
`modality.possibility`. Eles aparecem na fila OOV porque correspondência ambígua entra em
`unresolved` (`normalizer.py:316-322`), não porque falte conceito. Autorá-los seria trabalho
desperdiçado sobre um problema que já tem 5 conceitos concorrendo.

## Decisões

### D1 — Estado de verificação sem migração de schema *(reescrita: a anterior era inexecutável)*

A revisão provou empiricamente que `verify_official: pending` é rejeitado:
`registry.schema.json` tem `additionalProperties: false` e `registry.py:60-68` reforça com
igualdade exata de chaves; adicionar o campo produz
`ContractError: unexpected properties ['verify_official']`.

**Escolhido: usar os campos que já existem.** `authority` (≤160) e `source` (≤240) são strings
livres e obrigatórias. A verificação passa a ser codificada como prefixo estruturado:

```
authority: "apostila-cga-2026"                  -> definição ancorada, com heading + página
authority: "openwordnet-pt:1.0.0 ILI=i106686"   -> sentido adjudicado sobre candidato externo
authority: "project-authored:pending-review"    -> autorado, sem fonte normativa ainda
```

- **Bom:** `duration` cita `08-gestao-de-carteiras-de-renda-fixa.md#duration` + página física;
  `juro` cita o ILI adjudicado; nenhum schema muda, nenhum flag day nos 86 registros existentes.
- **Ruim:** convenção em string é mais fraca que enum — nada impede um valor mal formado.
- **Mitigação testável:** um teste valida o prefixo de `authority` contra a lista fechada de
  três formas, e falha em qualquer outro. Isso é o enum, implementado onde ele cabe hoje.
- **Não escolhido:** migrar o schema. `REGISTRY_VERSION` é hardcoded em `registry.py:15` e
  checado por igualdade em `:108`; migrar exige flag day sobre os 86 registros existentes, e
  esse custo não se justifica para um campo que uma convenção validada já cobre.

### D2 — Genericidade, não só polissemia *(reescrita)*

A defesa da revisão 1 (termo com sentidos não-relacionados vira 2 conceitos com
`policy: review`) é real e testada — mas só cobre polissemia. Não cobre um termo monossêmico e
genérico demais.

**Escolhido: gate por precisão, não por regra de corte.** Nenhum termo é excluído a priori por
"parecer genérico". Em vez disso, cada batch passa por adjudicação cega de uma amostra das
ocorrências que ele passou a casar, e a linha 2 da tabela-gate (≥95 %) decide.

- **Bom:** se `valor` for autorado com sentido financeiro e casar corretamente em 95 % das
  ocorrências amostradas, ele fica — decidido por dado, não por intuição minha.
- **Ruim:** adjudicar amostra custa tempo humano por batch.
- **Por que assim:** a alternativa é eu decidir sozinho se `valor`/`classe`/`renda` são
  "de domínio", que é exatamente o julgamento que a revisão apontou como o ponto mais frágil.

### D3 — Construção da fila *(nova, e é pré-requisito de tudo)*

**Escolhido: nenhuma lematização caseira.** A fila agrupa por forma de superfície e delega o
agrupamento ao `_closest`/`_stem_threshold` já endurecido em `migrate_v02_concepts.py`, ou não
agrupa. Stopwords e regra de sentença ficam **dentro do script versionado**, não na cabeça de
quem roda.

Justificativa medida: o lematizador da revisão 1 pôs 12 não-palavras no top-200. Um plano
dimensionado sobre uma fila com 10 % de lixo dimensiona errado.

### D4 — Fila estratificada em três, não uma *(nova)*

O passo 1 emite três listas separadas, porque exigem trabalho diferente:

1. **`unknown`** — nenhuma forma no registro. Autoria ou import.
2. **`ambiguous`** — já tem candidatos; entra em `unresolved` por ambiguidade
   (`deve`, `pode`, `base`). **Não é autoria: é adjudicação.**
3. **`function`** — palavra gramatical sem valor de dicionário.

Só (1) alimenta a autoria. Misturar (2) faz um executor gastar batch tentando "resolver" termo
que já tem 5 conceitos disputando.

### D5 — Lado inglês *(nova — a metade da âncora que a revisão 1 não orçava)*

O schema obriga bilinguidade: `bilingualLabels`, `bilingualLexicalForms` (`minItems: 1`) e
`bilingualExamples` exigem `en` e `pt-BR`. A revisão 1 não declarava que isso significa autorar
label, forma e dois exemplos EN para cada conceito, **sem corpus EN e sem gate**.

**Escolhido: EN vem do ILI adjudicado; quando não houver ILI, o conceito não entra no batch
automático.** Linha 4 da tabela-gate torna isso verificável: zero conceitos com `pref` EN sem
evidência. Para os 39 ausentes, o EN vem do próprio termo quando o corpus já usa o anglicismo
(`duration`, `swap`, `hedge`, `yield`, `asset` aparecem em português no corpus) e é declarado
como tal.

- **Bom:** `juro → interest` com `ILI=i106686` registrado em `authority`.
- **Ruim:** `carteira` só entra depois de alguém rejeitar `bag` e escolher `portfolio` — custo
  real, não eliminável.
- **Declarado:** não existe corpus EN neste projeto. A qualidade do lado EN é medida por
  adjudicação de sentido, nunca por cobertura — e isso é uma limitação, não um detalhe.

### D6 — O alvo numérico sai do plano *(nova)*

A revisão 1 imprimia "200 conceitos → 40,3 %" e depois estabelecia a meta em "≥40 %". Isso é
reafirmar a própria aritmética.

**Escolhido: o passo 2 escreve e commita `measure_domain_coverage.py`, roda contra o baseline
0.3.0, e o número-alvo é fixado a partir do output real desse script, num commit separado,
antes de qualquer conceito existir.** O plano não carrega mais número de cobertura.

Amarração contra ajustar o medidor depois (o furo que a revisão apontou): o Adversarial
Verification Loop da execução recebe instrução explícita de rodar
`git diff <commit-do-passo-2>..HEAD -- scripts/measure_domain_coverage.py` e tratar qualquer
mudança não justificada como achado BLOQUEANTE.

### D7 — Limpeza de marcação matemática antes de qualquer contagem *(nova, rodada 2)*

Os pseudo-termos chegam à fila: `text` (10), `frac` (6), `underline` (6), `sigma` (6),
`times` (5), `mathrm` (4), `sqrt` (1).

**Sobre a magnitude, três contagens diferentes circularam e todas estavam certas para o que
mediam** — 223 (ocorrências, 5 padrões), 157 (ocorrências, 4 padrões), 115 (LINHAS, `grep -c`).
Isso é a terceira vez nesta sessão que um número meu não reproduz por diferença de método, e é
a razão pela qual **este plano não fixa a magnitude**. O que ele fixa é o universo a remover,
medido por construção sintática e não por lista de comandos:

| Construção | Ocorrências |
|---|---:|
| Blocos `$$…$$` | 102 |
| Inline `$…$` | 139 |
| Comandos `\xxx` | **437** |

O número que importa é 437, não 223 — uma lista de 4 ou 5 comandos subconta o problema por um
fator de ~2. A limpeza do passo 1 é definida por essas três construções, não por enumeração de
comandos, e o gate do passo 1 é `zero token começando com \ na fila`, verificável sem depender
de qual lista alguém escreveu.

#### A regra exata, porque a intenção não bastava

**`R$` usa o mesmo caractere que delimita LaTeX inline.** O corpus tem **256 ocorrências** de
`R$`/`US$`. A leitura literal de "remover LaTeX inline" apaga dinheiro:

```python
re.sub(r'\$[^\$]*\$', '', "…avaliada em R$ 12.000,00 no início do mês. …de R$ 18.250,00.…")
# -> "…avaliada em R 18.250,00.…"        75 caracteres de conteúdo financeiro real, apagados
```

Isso aconteceria no **passo 1**, corrompendo o corpus congelado que todo o resto do plano
consome por hash. Nenhum gate posterior pegaria — o dado já teria sumido antes de existir fila.

A regra é normativa, não ilustrativa:

```python
DELIM = re.compile(r'(?<![A-Za-z])\$([^\$\n]{1,120}?)\$')   # $ precedido de letra não é delimitador
TEXISH = re.compile(r'\\[a-zA-Z]|_\{|\^\{|_[0-9]')          # só remove se houver conteúdo TeX dentro
```

Verificado nos dois sentidos ao mesmo tempo: preserva `R$ 12.000,00` **e** remove `$CF_0$` e
`$\Delta y$`. **Gate adicional do passo 1:** a contagem de `R$`/`US$` antes e depois da limpeza
tem de ser idêntica (256 = 256). Um teste, não uma promessa.

**Escolhido: remover blocos `$$…$$` e LaTeX inline no passo 1, antes da extração de sentenças.**
D3 proíbe lematizador caseiro, mas isso é ruído de *formato*, não de morfologia — nenhuma regra
morfológica salva de `\frac`. Sem essa limpeza, vagas de batch são gastas adjudicando comandos
LaTeX. A remoção é registrada no manifesto da fila (quantos blocos, quantos caracteres), para
que ninguém confunda "limpeza" com "descarte silencioso de conteúdo".

### D8 — Estrato `function` aplicado antes do WordNet *(nova, rodada 2)*

Achado da revisão: `também`, `às`, `seja`, `porém` são **monossêmicos no WordNet** — têm uma
entrada lexicográfica só. O balde "monossêmico → import automático" herdaria palavra gramatical
como se fosse termo de domínio validado.

**Escolhido: a partição do D4 roda antes da consulta ao WordNet.** Monossemia no WordNet é fato
lexicográfico, não atestado de que o termo pertence ao domínio. A ordem correta é
`function` → fora; só o que sobra consulta o WordNet.

**E a lista de stopwords não é escrita à mão.** A revisão mostrou que a minha, com 122 palavras,
ainda deixava `para` (332 ocorrências), `com` (275), `por` (244), `uma` (238) e `que` de fora —
seis termos gramaticais chegavam ao balde de import automático. Lei Zero aplicada à própria
lista.

Medido: NLTK `stopwords.words('portuguese')` tem **207** palavras, das quais **169** não estão
na minha. Mas a minha tem **84** que a NLTK não tem (`assim`, `apenas`, `cada`, `maior`,
`menor`, `através`…). **Escolhido: a união das duas, não a substituição de uma pela outra** —
a base validada cobre o núcleo gramatical, a lista local cobre o que apareceu neste corpus.
`nltk` entra como dependência de *build*, nunca de runtime; o runtime segue sem dependência,
como `docs/data-governance.md` exige.

### D9 — Fonte de candidatos para a adjudicação *(nova, rodada 2 — o furo do reuso)*

A revisão 1 me levou a reusar `evaluate_auto_matches` em vez de reimplementar. Correto — mas a
revisão 2 mostrou que só metade do maquinário serve.

Medido: `generate_auto_match_candidates.py::generate()` (Linhas 138-155) varre exclusivamente
`golden.jsonl` filtrado a `g01`-`g40` e `dev_retrieval.json`. `grep -c` de vocabulário
financeiro nesses fixtures: **0** e **1**. Rodado como o passo 4 descrevia, `automatic == 0`, e
`evaluator.py:463-466` devolve a **string `"not_run"`** — não um número comparável a "≥95 %".
O gate de precisão seria inavaliável.

**Escolhido: passo 3b gera um fixture de ocorrências reais do corpus CGA no shape que
`generate()` espera.** A pontuação (`evaluate_auto_matches`) continua reuso puro; só a geração
de candidatos ganha uma fonte. Não é arquitetura nova — é o encanamento que faltava.

**Shape exato, lido em `generate_auto_match_candidates.py:148-153`** (o plano dizia "no shape que
`generate()` espera", o que era hand-waving; aqui está a especificação):

```json
{"id": "gNN", "type": "normalize", "kind": "text",
 "input": "<sentença do corpus CGA>",
 "expected": {"contains_concepts": ["<concept_id>"]}}
```

O filtro é `type == "normalize"`, `id` começando com `g`, e `int(id[1:3])` entre 1 e 40.

**São dois caminhos, não um — e o plano tem de escolher.** A revisão 3 mostrou que
`generate()` aceita `--golden` e `--dev` com propriedades diferentes:

| Caminho | Teto | Auto-seed |
|---|---|---|
| `--golden` (JSONL) | **40 casos, codificado na função**; `g41` é descartado em silêncio, e `g100` colide com `g10` porque `[1:3]` lê 2 caracteres | sim — `expected.concept_ids` credita automaticamente |
| `--dev` (JSON) | sem teto | **não** — 100 % das ocorrências vão para adjudicação manual |

**Escolhido: `--golden` com o teto parametrizado** (`case_range=(1, 40)` como default). O
auto-seed é o que torna a adjudicação viável: sem ele, cada batch exige julgar manualmente
todas as ocorrências, e a seção de riscos já diz que adjudicação é o custo dominante — o
caminho `--dev` multiplicaria esse custo sem o plano ter dito.

**Consequência que muda o passo 4 e que eu não tinha visto:** o filtro é **hard-capped em 40
casos**. Um batch de 50 conceitos não cabe numa rodada de `generate()` sem editar a função.
Duas saídas, e o plano escolhe a segunda:

- *Amostra de 40 por batch* — cabe sem tocar código, mas amostra 40 ocorrências para julgar 50
  conceitos, e conceito sem ocorrência amostrada fica sem evidência de precisão.
- *Escolhida: parametrizar o range em `generate()`* — uma mudança de assinatura
  (`case_range=(1, 40)` como default) preserva o comportamento atual, mantém os testes
  existentes verdes, e permite amostra proporcional ao batch. É a menor mudança que torna o
  gate do passo 4 avaliável para todos os conceitos do batch.

**E `"not_run"` conta como abortar**, não como indeterminado: se a amostra não produzir número,
o batch não passou. Isso vai explícito no gatilho abaixo.

## Sequência

Um commit por passo; o seguinte não começa sem `make deliver` verde.

| # | Passo | Saída verificável | Gate |
|---|---|---|---|
| 1 | `scripts/build_oov_queue.py` — corpus congelado por SHA-256, stopwords e regra de sentença **no script**, sem lematizador caseiro, saída em três estratos (D4). **Inclui remoção de marcação matemática antes da extração** (D7) | `reports/oov-queue.json` | mesmo corpus → arquivo byte-idêntico; zero pseudo-termo LaTeX na saída |
| 2 | `scripts/measure_domain_coverage.py` + baseline 0.3.0 + **fixação do alvo numérico**. Emite também a **curva cumulativa por N** (frequência acumulada dos top-N do estrato `unknown` ÷ total do estrato), sem a qual o gatilho de abortar não tem previsão de batch para comparar | `reports/coverage-baseline.json` com `target` e `curve[]` | commit separado, anterior a qualquer conceito |
| 3 | `scripts/propose_from_wordnet.py` — candidatos do OpenWN-PT com **todos** os sentidos e o ILI de cada, nunca `synsets[0]`. Estrato `function` do D4 aplicado **antes** da consulta ao WordNet (D8) | `reports/wordnet-candidates.json` | nenhum sentido escolhido automaticamente para termo polissêmico |
| 3b | `scripts/build_adjudication_fixture.py` — fixture de ocorrências reais do corpus CGA no shape especificado em D9, **mais** parametrizar `case_range` em `generate()` mantendo `(1, 40)` como default | `tests/fixtures/cga_adjudication.jsonl` | `generate()` retorna candidatos > 0 **e** `test_o3_evaluator.py` segue verde com o default |
| 4 | Batch 1: 50 conceitos, estrato `unknown`, sentido adjudicado. Amostra medida por `generate_auto_match_candidates.py` + `evaluate_auto_matches` **já existentes** | registro 2.2.0 + adjudicação cega nova | `auto_match_precision` ≥95 % |
| 5 | Batches 2-4, mesma disciplina | — | `auto_match_precision` ≥95 % por batch |
| 6 | Re-medição + confronto com as 5 linhas | `reports/coverage-current.json` (o arquivo anterior foi renomeado para `reports/coverage-at-2.6.0.json`: o nome afirmava finalidade sobre conteúdo de 257 conceitos) | passa ou falha declarada |
| 7 | Adversarial Verification Loop | veredito | — |

**Gatilho objetivo de abortar** (a revisão 1 apontou que "roughly ~23 %" era racionalizável). O
plano para e volta para revisão se **qualquer** destes ocorrer no batch 1:

1. `auto_match_precision` < 0,90 na amostra cega;
2. `auto_match_precision` == `"not_run"` — sem número não há aprovação; ausência de medição
   conta como reprovação, nunca como indeterminado;
3. ganho de cobertura < metade do previsto pelo script do passo 2.

Três condições com limiar fixado antes de existir qualquer conceito. Nenhuma depende de
julgamento meu no momento do resultado.

## Riscos que este plano aceita, declarados

- **Adjudicação de sentido é o custo dominante e não é eliminável.** 144 dos 200 termos têm 6,6
  sentidos em média. Não existe atalho automático que a própria arquitetura da skill não
  proíba.
- **Cobertura na fila OOV não é melhora de recuperação.** G4 (qrels, queries held-out) continua
  aberto e este plano não o fecha.
- **Não existe corpus EN.** O lado inglês é validado por adjudicação de sentido, nunca por
  cobertura medida.
- **O corpus é uma apostila.** Cobertura medida nele não transfere sem re-medição.
- **200 conceitos não é o "dicionário completo" da âncora.** A âncora segue gap declarado.
