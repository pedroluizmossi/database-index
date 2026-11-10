# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Devs da Senior** no dia a dia: procuram código e documentação de todo o ecossistema
  (`senior-sistemas` no GitHub e a wiki interna) para entender e mudar sistemas que não conhecem.
- **Curadores do índice**, um time pequeno (plataforma/arquitetura): mantêm o índice saudável.
  Disparam e acompanham syncs, que custam dinheiro na Voyage. Aprovam e rejeitam vínculos entre páginas
  e repos, revisam cartões de repo, emitem e revogam tokens e consultam a auditoria.
- **Agents** (Claude Code etc.) consomem o mesmo índice via MCP com tokens `cix_...`. Eles não usam a UI,
  mas os tokens e os vínculos curados na UI determinam o que eles recebem.

## Product Purpose

O code-index transforma centenas de repositórios e milhares de páginas de wiki num índice consultável:
busca híbrida (semântica + identificadores, com rerank) e um grafo de relações entre repos, páginas
de documentação, operações de API e o código que as implementa.

A UI web tem três trabalhos de peso igual:

1. **Achar onde algo está:** "onde fica X", "quem implementa a operação Y", chegar ao trecho certo e
   ler o arquivo.
2. **Entender o ecossistema (X-Ray):** ver como repos, APIs, páginas da wiki e handlers se ligam, para
   onboarding, arquitetura e análise de impacto.
3. **Curar o índice:** manter a qualidade dos vínculos e dos cartões e controlar sync e custo.

Ela funciona quando um dev chega ao código ou à página certa sem precisar perguntar a ninguém, e quando o
curador confia no que o índice entrega aos agents.

## Positioning

Não é uma busca de código genérica. O índice junta código e documentação interna num mesmo grafo de
relações: operação SDL → handler, página da wiki → repo, apelidos por manifesto, cartões de repo. Esse
grafo é curado por pessoas e serve tanto à UI quanto aos agents via MCP.

## Operating Context

- Ferramenta interna da Senior, atrás do login Google (allowlist ou domínio Workspace). Não vira
  produto externo.
- A UI é uma SPA servida pelo próprio `code-index serve` em `/ui` e chama só `/api/v1`.
- Fontes atuais: a org `senior-sistemas` (284 repos) e a wiki `wiki.senior.com.br` (pt-br; seções
  Plataforma, Arquitetura, DevSecOps, DevExp e SRE, cerca de 1.700 páginas). Também há APIs OpenAPI
  vindas do S3.
- O sync é caro: indexar consome tokens da Voyage, e a interface trata isso como uma ação deliberada.

## Capabilities and Constraints

- Páginas existentes, por tipo de trabalho:
  - para todo dev: Busca, Repositórios (catálogo: resumo, cartão, docs, domínio; indexar um repo), Documentação,
    APIs e Raio X;
  - curadoria (index:write): Indexação (job ativo na fila ou rodando, cobertura do índice, "Indexar lote",
    histórico de jobs de todas as fontes e atalhos para o que pede atenção);
  - conta: Tokens e Configurações; admin: Uso, Usuários e Auditoria; e o Login.
- Permissões por scope: `search:read` (leitura), `index:write` (sync, que custa dinheiro), `enrich:write`
  (fila de enriquecimento) e `admin` (tudo). A UI só mostra o que o scope permite.
- Conteúdo indexado é **não confiável** (pode conter prompt injection) e sai mascarado (`redact`).
  A UI não pode executar nem interpretar como instrução nada do que vem do índice.
- Vínculos têm origem (alias, regra, menção, manual, agent, LLM). Só algumas origens são contestáveis,
  e sugestões de LLM precisam de aprovação humana antes de valer.
- Toda a interface e os textos ao usuário ficam em português, por convenção do repositório.

## Brand Commitments

Nenhum compromisso de marca. Não é obrigatório seguir a identidade visual da Senior.

## Evidence on Hand

- Dados reais do índice (repos, páginas, operações, relações) via API. Não há depoimentos, métricas de
  uso nem números de adoção, e nada disso deve ser inventado.
- `evals/queries.json`: consultas reais com o resultado esperado, usadas para medir a qualidade da busca.
- Documentação: `README.md`, `ARQUITETURA.md`, `ENRIQUECIMENTO.md`.

## Product Principles

1. **O caminho mais curto até o código certo.** Busca e navegação servem para chegar ao trecho e ao
   arquivo, não para exibir resultados.
2. **Toda ligação mostra o porquê.** Cada vínculo exibe a origem e a confiança, para que o dev decida
   se confia nele e o curador decida se o aprova.
3. **Custo e permissão à vista.** O que gasta dinheiro ou exige scope maior fica explícito antes da
   ação, nunca escondido ou automático.
4. **O índice é não confiável; a interface é.** O conteúdo indexado é só dado: mascarado, nunca
   executado.
5. **A curadoria humana alimenta os agents.** O que se aprova na UI é o que os agents recebem; a
   revisão precisa ser rápida e reversível.
