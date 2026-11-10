import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { FolderTree, Undo2 } from "lucide-react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { OwnershipCatalog, OwnershipRow, Repo } from "../api/types";
import { ORIGIN_HINT, ORIGIN_LABEL, normalizeOwner, ownerOptions, validOwner } from "../lib/ownership";
import { plural, repoShortName } from "../lib/utils";
import { Dialog } from "./Dialog";
import { OwnerCombobox } from "./OwnerCombobox";
import { useToast } from "./Toaster";
import { Alert, Badge, Button, Field, inputClass } from "./ui";

/**
 * Atribuição manual do dono (domínio/serviço do DDD) de um ou mais repos, ou de uma pasta de um repo
 * (monorepo). Vale na hora e vence o que a inferência e o .sdl dizem. Domínio/serviço fora do catálogo só
 * com confirmação explícita, para um erro de digitação não virar um domínio novo.
 */
export function OwnerDialog({
  open,
  repos,
  catalog,
  onClose,
  onDone,
}: {
  open: boolean;
  repos: Repo[];
  catalog: OwnershipCatalog | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { push } = useToast();
  const domainId = useId();
  const single = repos.length === 1 ? repos[0] : null;
  const [value, setValue] = useState("");
  const [path, setPath] = useState("");
  const [reason, setReason] = useState("");
  const [create, setCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<OwnershipRow[] | null>(null);

  // Ao abrir: limpa o formulário e, para um repo só, carrega o dono atual (com pastas e alternativas).
  useEffect(() => {
    if (!open) return;
    setValue(single?.owner_origin === "manual" ? single.owners[0] ?? "" : "");
    setPath("");
    setReason("");
    setCreate(false);
    setError(null);
    setRows(null);
    if (single) {
      api
        .ownership(single.name)
        .then(setRows)
        .catch(() => setRows([]));
    }
  }, [open, single]);

  const options = useMemo(() => ownerOptions(catalog), [catalog]);
  const known = useMemo(() => new Set(options.map((o) => o.value)), [options]);
  const normalized = normalizeOwner(value);
  const valid = validOwner(normalized);
  const isNew = valid && catalog !== null && !known.has(normalized);
  const whole = rows?.find((r) => r.path === null) ?? null;
  const folders = rows?.filter((r) => r.path !== null) ?? [];
  const manualCount = repos.filter((r) => r.owner_origin === "manual").length;
  const declaredCount = repos.filter((r) => r.owner_origin === "declared").length;
  /** Pasta digitada (só para um repo): a atribuição vai para ela, não para o repo inteiro. */
  const folder = single && path.trim() ? path.trim() : null;
  /** Atribuição no repo inteiro sobrepõe um dono declarado no .sdl (pasta, não). */
  const overwritesDeclared = declaredCount > 0 && folder === null;
  /** Um repo com vários donos manuais: o campo pré-preenche só o primeiro, então avisa. */
  const multiManual = single && single.owner_origin === "manual" && single.owners.length > 1 ? single : null;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!valid || (isNew && !create)) return;
    const plan = buildUndoPlan(repos, folder, rows);
    setBusy(true);
    setError(null);
    try {
      const res = await api.setOwner({
        repos: plan.repos,
        owner: normalized,
        path: folder,
        create: isNew,
        reason: reason.trim(),
      });
      push({
        tone: "success",
        title: `Domínio definido: ${res.owner}`,
        description: res.updated === 1 ? res.entities[0].replace(/^(repo|dir):/, "") : `${res.updated} ${plural(res.updated, "repositório", "repositórios")}`,
        action: { label: "Desfazer", onClick: () => void undo(plan) },
      });
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  /** Volta ao estado de antes da atribuição (capturado em `buildUndoPlan`, antes de salvar). */
  async function undo(plan: UndoPlan) {
    try {
      await undoAssignment(plan);
      push({
        tone: "success",
        title: "Atribuição desfeita",
        description: plan.folder !== null ? "A pasta voltou ao estado anterior." : "Os repositórios voltaram ao estado anterior.",
      });
      onDone();
    } catch (err) {
      push({ tone: "error", title: "Não consegui desfazer", description: describeError(err) });
    }
  }

  async function clear(folderPath: string | null) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.clearOwner({ repos: repos.map((r) => r.name), path: folderPath });
      push({
        tone: "success",
        title: "Atribuição manual removida",
        description: res.cleared ? "Volta a valer o SDL ou a inferência." : "Não havia atribuição manual.",
      });
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const title = single ? `Domínio de ${repoShortName(single.name, single.org)}` : `Domínio de ${repos.length} repositórios`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description="Define a que domínio (e serviço) do DDD o código pertence. Vale na hora e vence a inferência."
      size="lg"
      footer={
        <>
          {(single ? whole?.origin === "manual" : manualCount > 0) && (
            <Button variant="ghost" onClick={() => void clear(null)} disabled={busy} className="mr-auto">
              <Undo2 className="size-4" aria-hidden="true" />
              {single ? "Voltar ao inferido" : `Remover manual (${manualCount})`}
            </Button>
          )}
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="owner-form" loading={busy} disabled={!valid || (isNew && !create)}>
            Salvar
          </Button>
        </>
      }
    >
      <form id="owner-form" onSubmit={(e) => void submit(e)} className="space-y-5">
        {single && whole && <CurrentOwner row={whole} onPick={setValue} />}
        {single && rows !== null && !whole && (
          <p className="text-sm text-stone-500 dark:text-stone-400">
            Sem domínio hoje: nem SDL, nem repositório irmão, nem chamadas de API ou uso que indiquem o domínio.
          </p>
        )}
        {multiManual && (
          <p className="text-sm text-stone-600 dark:text-stone-300">
            Há {multiManual.owners.length} {plural(multiManual.owners.length, "domínio manual", "domínios manuais")}; o campo mostra só o primeiro. Confira a lista em &ldquo;Hoje&rdquo; antes
            de salvar.
          </p>
        )}
        {!single && <SelectedRepos repos={repos} manualCount={manualCount} />}
        {overwritesDeclared && (
          <p
            role="note"
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
          >
            {single
              ? "Este repositório tem domínio declarado no SDL. A atribuição manual vai prevalecer sobre o .sdl."
              : `${declaredCount} ${plural(declaredCount, "repositório tem", "repositórios têm")} domínio declarado no SDL. A atribuição manual vai prevalecer sobre o .sdl.`}
          </p>
        )}

        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={domainId} className="text-xs font-medium text-stone-500 dark:text-stone-400">
            Domínio ou domínio/serviço
          </label>
          <OwnerCombobox
            id={domainId}
            value={value}
            onChange={(next) => {
              setValue(next);
              setCreate(false);
            }}
            options={options}
            placeholder="ex.: platform/authentication, hcm, transversal"
            autoFocus
          />
        </div>
        {value.trim() && !valid && (
          <p className="-mt-3 text-xs text-rose-600 dark:text-rose-400">
            Use o nome técnico: letras minúsculas, dígitos e _, com uma barra entre domínio e serviço.
          </p>
        )}
        {isNew && (
          <label className="-mt-2 flex items-start gap-2.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200">
            <input
              type="checkbox"
              checked={create}
              onChange={(e) => setCreate(e.target.checked)}
              className="mt-0.5 size-4 accent-amber-600"
            />
            <span>
              <span className="font-mono">{normalized}</span> não existe no catálogo (nenhum .sdl declara). Marque
              para criar.
            </span>
          </label>
        )}

        {single && (
          <Field label="Pasta (opcional, para monorepo)">
            <input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              className={inputClass}
              placeholder="ex.: modulos/folha (vazio = o repositório inteiro)"
              spellCheck={false}
            />
          </Field>
        )}
        <Field label="Motivo (opcional)">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className={inputClass}
            maxLength={500}
            placeholder="ex.: confirmado com o time de RH"
          />
        </Field>

        {folders.length > 0 && (
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-stone-700 dark:text-stone-200">Pastas com domínio próprio</h3>
            <ul className="divide-y divide-stone-100 rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-800">
              {folders.map((f) => (
                <li key={f.path} className="flex items-center gap-3 px-3 py-2 text-sm">
                  <FolderTree className="size-4 shrink-0 text-stone-500 dark:text-stone-400" aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{f.path}/</span>
                  <span className="truncate">{f.owners.join(", ")}</span>
                  {f.origin && <OriginBadge origin={f.origin} />}
                  {f.origin === "manual" && (
                    <Button variant="ghost" size="sm" onClick={() => void clear(f.path)} disabled={busy}>
                      Remover
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {error && <Alert>{error}</Alert>}
      </form>
    </Dialog>
  );
}

/** O que é preciso para desfazer uma atribuição, capturado antes de salvar. */
interface UndoPlan {
  repos: string[];
  /** Pasta atribuída (só um repo); null = o repo inteiro. */
  folder: string | null;
  /** Dono manual que a pasta tinha antes; null = nenhum (então a pasta é limpa). */
  folderOwner: string | null;
  /** Repo inteiro: repos que tinham dono manual, agrupados pelo dono anterior (um setOwner por grupo). */
  restore: { owner: string; repos: string[] }[];
  /** Repo inteiro: repos que não tinham dono manual (clearOwner). */
  clear: string[];
}

function buildUndoPlan(repos: Repo[], folder: string | null, rows: OwnershipRow[] | null): UndoPlan {
  const names = repos.map((r) => r.name);
  if (folder !== null) {
    const row = rows?.find((r) => r.path === folder);
    const folderOwner = row?.origin === "manual" ? (row.owners[0] ?? null) : null;
    return { repos: names, folder, folderOwner, restore: [], clear: [] };
  }
  const byOwner = new Map<string, string[]>();
  const clear: string[] = [];
  for (const r of repos) {
    const previous = r.owner_origin === "manual" ? r.owners[0] : undefined;
    if (previous) byOwner.set(previous, [...(byOwner.get(previous) ?? []), r.name]);
    else clear.push(r.name);
  }
  const restore = [...byOwner].map(([owner, list]) => ({ owner, repos: list }));
  return { repos: names, folder: null, folderOwner: null, restore, clear };
}

async function undoAssignment(plan: UndoPlan): Promise<void> {
  if (plan.folder !== null) {
    if (plan.folderOwner) {
      await api.setOwner({ repos: plan.repos, owner: plan.folderOwner, path: plan.folder, create: false });
    } else {
      await api.clearOwner({ repos: plan.repos, path: plan.folder });
    }
    return;
  }
  for (const group of plan.restore) {
    await api.setOwner({ repos: group.repos, owner: group.owner, path: null, create: false });
  }
  if (plan.clear.length > 0) await api.clearOwner({ repos: plan.clear, path: null });
}

const CONFIDENCE: Record<string, string> = { alta: "alta", media: "média", baixa: "baixa" };

function OriginBadge({ origin }: { origin: keyof typeof ORIGIN_LABEL }) {
  return (
    <span title={ORIGIN_HINT[origin]}>
      <Badge tone={origin === "manual" ? "accent" : "neutral"}>{ORIGIN_LABEL[origin]}</Badge>
    </span>
  );
}

/** Repos selecionados em lista compacta e rolável: nome, dono atual e de onde ele veio. */
function SelectedRepos({ repos, manualCount }: { repos: Repo[]; manualCount: number }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
        <span className="font-medium text-stone-700 dark:text-stone-200">{repos.length} {plural(repos.length, "repositório selecionado", "repositórios selecionados")}</span>
        {manualCount > 0 && (
          <span className="text-xs text-stone-500 dark:text-stone-400">{manualCount} {plural(manualCount, "repositório com domínio manual (será substituído)", "repositórios com domínio manual (serão substituídos)")}</span>
        )}
      </div>
      <ul
        tabIndex={0}
        aria-label="Repositórios selecionados"
        className="max-h-48 divide-y divide-stone-100 overflow-y-auto rounded-lg border border-stone-200 outline-none focus-visible:ring-2 focus-visible:ring-accent-500 dark:divide-stone-800 dark:border-stone-800"
      >
        {repos.map((r) => (
          <li key={r.name} className="flex items-center gap-3 px-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate" title={r.name}>
              {repoShortName(r.name, r.org)}
            </span>
            {r.owners.length > 0 ? (
              <span
                className="max-w-[45%] truncate font-mono text-xs text-stone-600 dark:text-stone-300"
                title={r.owners.join(", ")}
              >
                {r.owners.join(", ")}
              </span>
            ) : (
              <span className="text-xs text-stone-500 dark:text-stone-400">sem domínio</span>
            )}
            {r.owner_origin && <OriginBadge origin={r.owner_origin} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Dono atual do repo inteiro, de onde veio e o que as origens mais fracas sugerem (um clique preenche). */
function CurrentOwner({ row, onPick }: { row: OwnershipRow; onPick: (value: string) => void }) {
  return (
    <div className="space-y-2 rounded-lg border border-stone-200 bg-stone-50 px-3.5 py-3 text-sm dark:border-stone-800 dark:bg-stone-950/40">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-stone-500 dark:text-stone-400">Hoje:</span>
        <span className="font-medium">{row.owners.join(", ")}</span>
        {row.origin && <OriginBadge origin={row.origin} />}
        {row.confidence && (
          <span className="text-xs text-stone-500 dark:text-stone-400">confiança {CONFIDENCE[row.confidence] ?? row.confidence}</span>
        )}
      </div>
      {row.alternatives.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-stone-500 dark:text-stone-400">Outras indicações:</span>
          {row.alternatives.map((alt) => (
            <button
              key={`${alt.owner}-${alt.origin}`}
              type="button"
              onClick={() => onPick(alt.owner)}
              className="rounded-md border border-stone-200 bg-sheet px-2 py-0.5 text-xs outline-none transition hover:border-accent-400 hover:text-accent-700 focus-visible:ring-2 focus-visible:ring-accent-500 dark:border-stone-700 dark:bg-stone-900 dark:hover:text-accent-300"
              title={`Usar ${alt.owner}`}
            >
              {alt.owner} <span className="text-stone-500 dark:text-stone-400">· {ORIGIN_LABEL[alt.origin]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
