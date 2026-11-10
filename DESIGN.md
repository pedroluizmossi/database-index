# Design

Como a interface web do code-index (`frontend/`) se parece e se move, e por quê. O que o produto é e para quem
está em `PRODUCT.md`; este arquivo diz como isso vira tela. Mudou algo visual? Siga as regras daqui ou atualize
este arquivo junto.

## A ideia: cartografia latente

O fundo da interface é a prancha *Latent Cartography* (`frontend/src/assets/latent-cartography.webp`): um mapa
imaginário de "o que está perto" (vetor) e "o que tem nome" (palavra-chave), fundidos por RRF. É exatamente o que o
índice faz. A filosofia da prancha vale para a interface inteira:

- **Levantamento, não decoração.** Cada marca registra algo. Nada existe só para enfeitar.
- **Papel, tinta e uma cor de sinal.** Papel cru, uma tinta quase preta em vários pesos, e o vermelhão só onde algo
  foi *encontrado*. Cor marca; nunca preenche um campo.
- **Multidão e instância.** Muitas marcas parecidas dão o ritmo (listas, tabelas); poucas quebram o ritmo com um
  anel, um fio ou uma cor (o item selecionado, o centro do mapa).
- **Texto que indexa, não explica.** Rótulos curtos, como etiquetas de espécime: *Fig. 1*, *n = 23*, *gravado*.
- **Espaço vazio é o papel do levantamento.** Margens generosas; densidade só onde há dado.

O conteúdo indexado é não confiável (`PRODUCT.md`): a interface nunca interpreta nem executa o que vem dele. Isso
também é design: o índice é dado, a moldura é nossa.

## Cor

Tokens em `frontend/src/index.css` (`@theme` e `:root`/`html.dark`). Use sempre o token, nunca um hexadecimal solto
num componente.

| Papel | Token / classe | Claro | Escuro | Onde |
|---|---|---|---|---|
| Fundo da página | `bg-paper` / `dark:bg-stone-950` | `#EDE8DF` | `#151412` | `body`; é o papel da prancha |
| Folha | `bg-sheet` / `dark:bg-stone-900` | `#FAF8F3` | `#24221E` | painéis, cartões, tabelas, campos, diálogos |
| Fundo sutil | `bg-stone-50` / `bg-stone-100` | `#F7F4EE` / `#F0EBE2` | `stone-800`/`stone-900` | cabeçalho de tabela, blocos de código, hover |
| Fio | `border-stone-200` / `dark:border-stone-800` | `#E3DCCF` | `#34312B` | bordas e divisórias (1 px) |
| Texto | `text-stone-900` / `dark:text-stone-100` | | | corpo |
| Texto secundário | `text-stone-500` / `dark:text-stone-400` | `#6B6458` | `#A8A091` | legendas, metadados (contraste ≥ 4,5:1 no papel e na folha) |
| Tinta (menu) | `bg-ink-950`, `text-ink-*` | `#1C1B19` | igual | menu lateral, barra de seleção |
| Acento | `accent-*` (cobalto) | | | ação, link, foco, seleção, progresso |
| Sinal | `--color-signal` | `#C5422B` | igual | só o "encontrado": centro do mapa do Raio X |
| Tipo: repositório | `kind-repo` | verde-azulado | mais claro | ícone e nós de repo/código |
| Tipo: documento | `kind-doc` | ocre | mais claro | ícone e nós de documento |
| Tipo: API | `kind-api` | violeta | mais claro | ícone e nós de API |
| Status | `emerald`, `rose`, `amber` | | | indexado, erro, aviso (sempre com rótulo ou ícone) |

Regras:

- **Neutros são quentes.** O `stone` do Tailwind foi substituído por uma escala afinada com o papel. Não use
  `zinc`, `gray`, `slate` nem `neutral`: eles são frios e brigam com o fundo.
- **Nunca branco puro em superfície.** Superfície é `bg-sheet`. Branco só em texto sobre cor (botão primário,
  menu) e no botão do `Switch`.
- **Um acento.** Cobalto é ação. Não use o cobalto para decorar nem para indicar tipo.
- **Uma cor por tipo, a mesma em todas as telas** (`components/kind.tsx`): `KindIcon` desenha o ícone do tipo num
  quadrado tingido; `KIND_META[kind].color` é a cor para SVG. Tipo novo entra em `KIND_META` e ganha `--kind-*` nos
  dois temas.
- **Vermelhão é raro.** Só para o que foi encontrado. Se aparecer em mais de um lugar por tela, deixa de significar.

## Superfícies

- Painel (`Panel` em `components/ui.tsx`): `bg-sheet`, fio de 1 px, `rounded-xl`, **sem sombra**. A prancha não
  tem sombras; o fio separa.
- Sombra só no que flutua sobre o conteúdo: diálogo, toast, menu suspenso, barra de seleção.
- Menu lateral: tinta quente. Item ativo: texto branco e barra fina cor de papel na borda esquerda (não azul).
- **Estado ativo ou em andamento marca, não pinta.** Faixa de status (ex.: "Indexação em andamento") e item
  selecionado de lista ficam na folha (ou num cinza sutil) com uma barra de acento de 3 px à esquerda
  (`shadow-[inset_3px_0_0_var(--color-accent-500)]`). Nada de área inteira em cobalto claro.

## Tipografia

- **Inter** (`font-sans`) para tudo que se lê; **JetBrains Mono** (`font-mono`) para caminhos, código, identificadores
  e etiquetas.
- **Etiqueta de espécime**: mono, 10 px, maiúsculas, `tracking-[0.14em]`–`[0.18em]`, cinza secundário. Usada em
  cabeçalhos de tabela (`tableHeadClass` em `ui.tsx`), na legenda e no título do mapa do Raio X. Só para indexar
  ("Repositório", "n = 12"), nunca para frases.
- **Serifa itálica** (`font-serif`, *Instrument Serif*): só em momentos de assinatura, como o nome da prancha. Hoje:
  título do Login e título dos estados vazios (`EmptyState`). Nunca em título de página, botão, tabela ou texto
  corrido. Se aparecer em todo lugar, deixa de ser assinatura.
- Títulos de página: `text-2xl font-semibold tracking-tight`. Números: `tabular-nums` sempre que podem mudar ou ser
  comparados.
- Português em todo texto ao usuário.

## Movimento

Movimento explica uma mudança de estado. Duração curta, curva `cubic-bezier(0.16, 1, 0.3, 1)` (saída suave), e o
bloco `prefers-reduced-motion` do `index.css` desliga tudo.

| Onde | Como | Arquivo |
|---|---|---|
| Diálogo abre | `animate-pop` (escala 0,96 → 1) e fundo em `fade-in` | `components/Dialog.tsx` |
| Troca de aba | View Transition: só o conteúdo (`view-transition-name: page`) some em 120 ms e entra subindo 8 px | `App.tsx`, `index.css` |
| Listas | `.stagger` no contêiner: filhos sobem um a um, 30 ms entre eles, até 300 ms | `index.css` |
| Raio X | o vizinho clicado desliza para o centro (500 ms); linhas e rótulos novos aparecem depois | `components/XrayMap.tsx` |
| Indexação em andamento | ponto pulsante e barra no menu lateral | `components/IndexingIndicator.tsx` |
| Prancha de fundo | aparece em 1,2 s ao carregar | `index.css` |

Não use: parallax, gradiente animado, confete, grafo com simulação de forças (instável e pesado com milhares de
nós), animação em tabelas densas (Uso, Auditoria, Repositórios), nem movimento contínuo sem estado por trás.

## Componentes que carregam a identidade

- **Prancha de fundo** (`body::before` em `index.css`): fixa, inteira (`contain`), centrada na área de conteúdo
  (desconta o menu). O papel foi tirado da imagem (só a tinta, com alfa): no claro ela assenta sobre o mesmo papel; no
  escuro a tinta é invertida e o vermelhão continua vermelho. Opacidade 0,6 (claro) e 0,4 (escuro). É textura: todo
  conteúdo fica em superfícies opacas.
- **Mapa do Raio X** (`XrayMap.tsx`): aro graduado, isolinhas pontilhadas, item no centro em vermelhão, um setor fixo
  por tipo (posições determinísticas). Vínculos diretos no anel de fora com rótulo radial; inferidos no de dentro,
  com rótulo só no hover. O traço diz a origem: **cheio** = gravado (manual, agent, LLM aprovado), **tracejado** =
  automático, **pontilhado** = inferido. Até 40 nós; o resto vira "+N" e fica nas listas abaixo.
- **Escolha de páginas da wiki** (`DocSelectionDialog.tsx`): árvore com caixa parcial por pasta e, sempre à vista,
  o que entra (vai para a Voyage) e o que sai do índice. Custo à vista antes da ação (`PRODUCT.md`).
- **Prévia antes de gastar** (`SyncDialog.tsx`): toda indexação passa por uma prévia sem custo.
- **Estado vazio** (`EmptyState` em `ui.tsx`): uma mini-constelação (pontos e fios ao redor de um anel vazio, no
  traço do mapa) com o ícone do contexto no anel, e o título em serifa itálica. O anel vazio é o lugar onde o
  resultado estaria.

## Escrevendo uma tela nova

1. Superfície `Panel`; tabela com `tableHeadClass`; campos com `inputClass`.
2. Entidade do índice na tela? `KindIcon` ao lado do nome.
3. Lista que carrega de uma vez? `stagger` no contêiner.
4. Ação que custa dinheiro? Prévia sem custo e o custo escrito antes do botão.
5. Ação de admin ou de escopo maior? Só aparece para quem tem o scope (`hasScope`).
6. Confira claro e escuro, e com movimento reduzido.
