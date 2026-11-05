---
target: página de Repositórios
total_score: 26
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:/home/pedro.mossi/projetos/database-index/frontend/src/pages/ReposPage.tsx"
target_fingerprint: "sha256:846e7cea21c8395b996d15043e90ac97de36e132dfea08d05ef1b37ed27c0143"
target_path: /home/pedro.mossi/projetos/database-index/frontend/src/pages/ReposPage.tsx
timestamp: 2026-11-05T20-59-43Z
slug: frontend-src-pages-repospage-tsx
---
Method: dual-agent (A: design review · B: detector + browser)

## Nota de saúde do design: 26/40 (Aceitável)
| # | Heurística | Nota | Problema principal |
|---|---|---|---|
| 1 | Visibilidade do estado | 3 | Seleção entre páginas invisível |
| 2 | Mundo real | 3 | "Chunks", "repo(s) selecionado(s)" |
| 3 | Controle e liberdade | 3 | Sem desfazer na atribuição; sem limpar filtros |
| 4 | Consistência | 2 | domínio/dono/serviço; legenda de origem morta; foco inconsistente |
| 5 | Prevenção de erros | 3 | Lote sobrescreve SDL sem aviso |
| 6 | Reconhecer vs. lembrar | 2 | Ações só ícone; lote não lista repos; dicas só em title |
| 7 | Flexibilidade | 2 | Sem shift-clique, filtros fora da URL, sem filtro de origem/confiança |
| 8 | Estética | 3 | Links repetidos por linha |
| 9 | Recuperação de erros | 3 | Erro da linha cortado |
| 10 | Ajuda | 2 | Dicas só em title |

## Especificidade
Conteúdo próprio do produto (origem do dono, outras indicações, progresso por repo); casca visual de admin Tailwind genérico.
Detector CLI: 0 achados. Navegador: "sem dono" 2,6:1 (claro) e 3,7:1 (escuro); abas inativas 4,35:1; placeholders 2,6:1; palavra sozinha na descrição do diálogo.

## Problemas prioritários
1. [P1] Coluna "Ações" escondida por rolagem horizontal com nomes longos (tabela 1225px em ~1100px a 1280/1440). Correção: ações sticky à direita, nome com largura limitada, max-w-7xl. (/impeccable adapt)
2. [P1] Atribuição em lote sobrescreve dono declarado no SDL sem listar repos nem desfazer. Correção: listar repos com dono/origem, aviso de SDL sobrescrito, Desfazer no toast. (/impeccable harden)
3. [P1] "sem dono" com contraste 2,62:1 (claro) / 3,67:1 (escuro); abas inativas 4,35:1. Correção: badge vazado ≥4,5:1 clicável; zinc-600 nas abas. (/impeccable colorize)
4. [P2] Diálogos sem nome acessível e foco inicial em Fechar; datalist; radiogroup sem setas; sem aria-sort; 41 dialogs montados. (/impeccable audit)
5. [P2] Sem fila de revisão por origem/confiança; contagens de origem não clicáveis; confiança baixa invisível. (/impeccable shape)

## Personas
Alex: sem shift-clique, filtros fora da URL, seleção invisível antes de indexar (custo).
Sam: ações fora da tela no Tab, diálogo sem nome, 5 paradas no radiogroup, sem aria-sort, barra de seleção sem anúncio.
Marina (curadora): filtro de status persiste sem aviso, sem triagem por origem/confiança, não vê o que sobrescreve, sem desfazer.

## Menores
Placeholder cortado; select de domínio sem rótulo; paginação com pointer-events em vez de disabled; alvos 16px no mobile; barra de seleção cobre cartões no mobile; âmbar com dois sentidos; subtítulo e toast desatualizados; diálogo preenche só o primeiro dono.

## Perguntas
1. Curadoria como fila própria? 2. Permitir sobrescrever SDL ou mandar corrigir na fonte? 3. Destaque do topo: % indexado ou o que pede ação?
