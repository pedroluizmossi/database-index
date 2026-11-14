import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/endpoints";
import { describeError } from "../api/client";
import type { OrgSummary } from "../api/types";
import { Dialog } from "./Dialog";
import { useToast } from "./Toaster";
import { Alert, Button, Field, inputClass } from "./ui";

/** Cadastro de fontes (admin): repo avulso e org/conta do GitHub. Só cadastram; a indexação é separada. */

export function AddRepoDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { push } = useToast();
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.addRepo({
        source: source.trim(),
        name: name.trim() || null,
        branch: branch.trim() || null,
      });
      push({ tone: "success", title: "Repositório adicionado", description: `${created.name}: indexe quando quiser.` });
      setSource("");
      setName("");
      setBranch("");
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Adicionar repositório"
      description="URL git, org/repo do GitHub ou caminho de um diretório do servidor. Só o cadastro: a indexação é separada."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="add-repo-form" loading={busy} disabled={!source.trim()}>
            Adicionar
          </Button>
        </>
      }
    >
      <form id="add-repo-form" onSubmit={(e) => void submit(e)} className="space-y-4">
        <Field label="Origem">
          <input value={source} onChange={(e) => setSource(e.target.value)} className={inputClass} placeholder="org/repo ou https://…" />
        </Field>
        <Field label="Nome (opcional)">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} placeholder="gerado a partir da origem" />
        </Field>
        <Field label="Branch (opcional)">
          <input value={branch} onChange={(e) => setBranch(e.target.value)} className={inputClass} placeholder="padrão do remoto" />
        </Field>
        {error && <Alert>{error}</Alert>}
      </form>
    </Dialog>
  );
}

/** "a, b c" → ["a", "b", "c"]: globs e linguagens separados por vírgula ou espaço. */
function splitList(value: string): string[] {
  return value.split(/[\s,]+/).map((v) => v.trim()).filter(Boolean);
}

/** Nome da org/conta a partir do que a pessoa colou: "org", "@org" ou "https://github.com/org". */
function orgName(value: string): string {
  const v = value.trim().replace(/\/+$/, "");
  const m = v.match(/github\.com\/([^/?#]+)/i);
  return (m ? m[1] : v).replace(/^@/, "");
}

export function AddOrgDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const { push } = useToast();
  const [name, setName] = useState("");
  const [match, setMatch] = useState("");
  const [skip, setSkip] = useState("");
  const [languages, setLanguages] = useState("");
  const [maxSize, setMaxSize] = useState("");
  const [forks, setForks] = useState(false);
  const [archived, setArchived] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setMatch("");
    setSkip("");
    setLanguages("");
    setMaxSize("");
    setForks(false);
    setArchived(false);
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const org = orgName(name);
    const size = maxSize.trim() ? Number(maxSize) : null;
    if (size !== null && (!Number.isInteger(size) || size <= 0)) {
      setError("Tamanho máximo deve ser um número inteiro de MB maior que zero.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const found = await api.addOrg({
        name: org,
        match: splitList(match),
        skip: splitList(skip),
        languages: splitList(languages),
        include_forks: forks,
        include_archived: archived,
        max_size_mb: size,
      });
      push({
        tone: "success",
        title: "Org/conta adicionada",
        description: `${org}: ${found.total} repositório(s) descoberto(s). Indexe quando quiser.`,
      });
      reset();
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Adicionar org/conta"
      description="Org ou conta de usuário do GitHub. Descobre os repositórios (sem custo de embedding); a indexação é separada."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="add-org-form" loading={busy} disabled={!orgName(name)}>
            Adicionar
          </Button>
        </>
      }
    >
      <form id="add-org-form" onSubmit={(e) => void submit(e)} className="space-y-4">
        <Field label="Org ou conta">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} placeholder="minha-org ou https://github.com/usuario" />
        </Field>
        <Field label="Só repos que casam com (opcional)">
          <input value={match} onChange={(e) => setMatch(e.target.value)} className={inputClass} placeholder="api-* svc-*" />
        </Field>
        <Field label="Ignorar repos (opcional)">
          <input value={skip} onChange={(e) => setSkip(e.target.value)} className={inputClass} placeholder="*-legacy" />
        </Field>
        <Field label="Linguagens, em ordem de prioridade (opcional)">
          <input value={languages} onChange={(e) => setLanguages(e.target.value)} className={inputClass} placeholder="Python TypeScript" />
        </Field>
        <Field label="Tamanho máximo em MB (opcional)">
          <input value={maxSize} onChange={(e) => setMaxSize(e.target.value)} className={inputClass} inputMode="numeric" placeholder="sem limite" />
        </Field>
        <div className="space-y-2 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={forks} onChange={(e) => setForks(e.target.checked)} className="size-4 accent-accent-600" />
            Incluir forks
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} className="size-4 accent-accent-600" />
            Incluir arquivados
          </label>
        </div>
        {error && <Alert>{error}</Alert>}
      </form>
    </Dialog>
  );
}

/** Edita os filtros de descoberta de uma org (e redescobre). Forks/arquivados e exclusões extras só pela CLI. */
export function EditOrgDialog({
  org,
  onClose,
  onDone,
}: {
  org: OrgSummary | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const { push } = useToast();
  const [match, setMatch] = useState("");
  const [skip, setSkip] = useState("");
  const [languages, setLanguages] = useState("");
  const [maxSize, setMaxSize] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!org) return;
    const f = org.filters;
    setMatch((f.match ?? []).join(" "));
    setSkip((f.skip ?? []).join(" "));
    setLanguages((f.languages ?? []).join(" "));
    setMaxSize(f.max_size_mb ? String(f.max_size_mb) : "");
    setError(null);
  }, [org]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!org) return;
    const size = maxSize.trim() ? Number(maxSize) : 0;
    if (!Number.isInteger(size) || size < 0) {
      setError("Tamanho máximo deve ser um número inteiro de MB (vazio = sem limite).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const found = await api.setOrg(org.name, {
        match: splitList(match),
        skip: splitList(skip),
        languages: splitList(languages),
        max_size_mb: size,
      });
      push({
        tone: "success",
        title: "Filtros atualizados",
        description: `${org.name}: ${found.total} repositório(s), ${found.new} novo(s), ${found.removed} fora.`,
      });
      onDone();
      onClose();
    } catch (err) {
      setError(describeError(err));
      onDone(); // a configuração pode ter sido gravada mesmo se a redescoberta falhar
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={org !== null}
      onClose={onClose}
      title={`Filtros de ${org?.name ?? ""}`}
      description="Ao salvar, os repositórios são redescobertos: os que deixam de casar com os filtros saem do catálogo."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </Button>
          <Button type="submit" form="edit-org-form" loading={busy}>
            Salvar
          </Button>
        </>
      }
    >
      <form id="edit-org-form" onSubmit={(e) => void submit(e)} className="space-y-4">
        <Field label="Só repos que casam com">
          <input value={match} onChange={(e) => setMatch(e.target.value)} className={inputClass} placeholder="vazio = todos" />
        </Field>
        <Field label="Ignorar repos">
          <input value={skip} onChange={(e) => setSkip(e.target.value)} className={inputClass} placeholder="*-legacy" />
        </Field>
        <Field label="Linguagens, em ordem de prioridade">
          <input value={languages} onChange={(e) => setLanguages(e.target.value)} className={inputClass} placeholder="vazio = todas" />
        </Field>
        <Field label="Tamanho máximo em MB">
          <input value={maxSize} onChange={(e) => setMaxSize(e.target.value)} className={inputClass} inputMode="numeric" placeholder="sem limite" />
        </Field>
        {error && <Alert>{error}</Alert>}
      </form>
    </Dialog>
  );
}
