/** Erro de uma chamada à API. `status` 0 significa que o servidor nem respondeu. */
export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: string | null;

  constructor(status: number, message: string, retryAfter: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  /** Token explícito (usado no login, antes da sessão existir). */
  token?: string;
}

let sessionToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function setSessionToken(token: string | null): void {
  sessionToken = token;
}

/** Chamado quando uma requisição de sessão volta 401 (token expirou/foi revogado, ou cookie expirou). */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  }

  const bearer = options.token ?? sessionToken;
  const headers: Record<string, string> = {};
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(url, {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "same-origin", // envia o cookie de sessão do login no navegador
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Não consegui falar com o servidor. Confira se ele está no ar.");
  }

  if (!res.ok) {
    const message = await errorMessage(res);
    // 401 numa chamada de sessão (token colado ou cookie de sessão). Chamadas com token explícito
    // (tentativa de login) não derrubam sessão alguma.
    if (res.status === 401 && !options.token) unauthorizedHandler?.();
    throw new ApiError(res.status, message, res.headers.get("Retry-After"));
  }
  return res;
}

export async function getJson<T>(path: string, options?: RequestOptions): Promise<T> {
  const res = await send(path, options);
  return (await res.json()) as T;
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await send(path, { method: "POST", body });
  return (await res.json()) as T;
}

/** Requisição com corpo JSON opcional e método explícito (POST/PUT/DELETE). */
export async function sendJson<T>(
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  options: Omit<RequestOptions, "method"> = {},
): Promise<T> {
  const res = await send(path, { ...options, method });
  return (await res.json()) as T;
}

export async function getText(path: string, options?: RequestOptions): Promise<string> {
  const res = await send(path, options);
  return res.text();
}

async function errorMessage(res: Response): Promise<string> {
  const raw = await res.text();
  try {
    const detail: unknown = (JSON.parse(raw) as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ");
    }
  } catch {
    // corpo não é JSON: usa o texto cru abaixo
  }
  return raw.slice(0, 300) || res.statusText || `HTTP ${res.status}`;
}

/** Mensagem curta para mostrar à pessoa. */
export function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Algo inesperado aconteceu.";
  if (err.status === 0) return err.message;
  if (err.status === 403) return `Sem permissão: ${err.message}`;
  if (err.status === 429) return `Muitas requisições. Tente de novo em ${err.retryAfter ?? "alguns"} s.`;
  return err.message;
}
