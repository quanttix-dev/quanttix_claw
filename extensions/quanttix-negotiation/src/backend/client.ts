import type { PluginLogger } from "../../api.js";

export type BackendClientOptions = {
  baseUrl: string;
  svcEmail: string;
  svcPassword: string;
  tenantId?: string;
  empresaId?: string;
  filialId?: string;
  logger: PluginLogger;
  fetchImpl?: typeof fetch;
};

export class BackendClientError extends Error {
  readonly status: number | null;
  readonly body: string;

  constructor(message: string, status: number | null = null, body = "") {
    super(message);
    this.name = "BackendClientError";
    this.status = status;
    this.body = body;
  }
}

type RequestArgs = {
  method: "GET" | "POST";
  path: string;
  query?: Record<string, string | number | undefined>;
  body?: unknown;
  idempotencyKey?: string;
};

// Mirrors the Python handoff_server BackendClient: JWT login, retry on 401, tenant headers,
// optional Idempotency-Key. Token is cached in memory for the lifetime of the BackendClient.
export class BackendClient {
  private _token: string | null = null;
  private readonly _fetch: typeof fetch;

  constructor(private readonly opts: BackendClientOptions) {
    this._fetch = opts.fetchImpl ?? fetch;
  }

  // ── Auth ──────────────────────────────────────────────────────────

  private async _login(): Promise<string> {
    if (!this.opts.svcEmail || !this.opts.svcPassword) {
      throw new BackendClientError("BACKEND_SVC_EMAIL/PASSWORD not configured");
    }
    const resp = await this._fetch(`${this.opts.baseUrl}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: this.opts.svcEmail,
        password: this.opts.svcPassword,
      }),
    });
    const text = await resp.text();
    if (!resp.ok) {
      throw new BackendClientError(
        `login failed: HTTP ${resp.status}`,
        resp.status,
        text.slice(0, 200),
      );
    }
    let payload: unknown;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new BackendClientError(`login: invalid JSON`, resp.status, text.slice(0, 200));
    }
    const inner = ((): Record<string, unknown> => {
      if (
        payload &&
        typeof payload === "object" &&
        "data" in (payload as Record<string, unknown>)
      ) {
        const data = (payload as Record<string, unknown>)["data"];
        if (data && typeof data === "object") return data as Record<string, unknown>;
      }
      return (payload as Record<string, unknown>) ?? {};
    })();
    const token = inner["access_token"];
    if (typeof token !== "string" || token.length === 0) {
      throw new BackendClientError("login: no access_token in response");
    }
    return token;
  }

  private async _headers(idempotencyKey?: string): Promise<Record<string, string>> {
    if (!this._token) {
      this._token = await this._login();
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this._token}`,
      "Content-Type": "application/json",
    };
    if (this.opts.tenantId) headers["X-Tenant-Id"] = this.opts.tenantId;
    if (this.opts.empresaId) headers["X-Empresa-Id"] = this.opts.empresaId;
    if (this.opts.filialId) headers["X-Filial-Id"] = this.opts.filialId;
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    return headers;
  }

  private async _request<T = unknown>(args: RequestArgs): Promise<T> {
    let url = `${this.opts.baseUrl}${args.path}`;
    if (args.query) {
      const usp = new URLSearchParams();
      for (const [k, v] of Object.entries(args.query)) {
        if (v !== undefined) usp.set(k, String(v));
      }
      const qs = usp.toString();
      if (qs.length > 0) url += `?${qs}`;
    }

    for (let attempt = 1; attempt <= 2; attempt++) {
      const headers = await this._headers(args.idempotencyKey);
      const init: RequestInit = { method: args.method, headers };
      if (args.body !== undefined) init.body = JSON.stringify(args.body);

      const resp = await this._fetch(url, init);
      if (resp.status === 401 && attempt === 1) {
        this._token = null; // force relogin
        continue;
      }
      const text = await resp.text();
      if (!resp.ok) {
        throw new BackendClientError(
          `${args.method} ${args.path}: HTTP ${resp.status}`,
          resp.status,
          text.slice(0, 300),
        );
      }
      if (text.length === 0) return null as T;
      let payload: unknown;
      try {
        payload = JSON.parse(text);
      } catch {
        return null as T;
      }
      if (
        payload &&
        typeof payload === "object" &&
        "data" in (payload as Record<string, unknown>)
      ) {
        return (payload as Record<string, unknown>)["data"] as T;
      }
      return payload as T;
    }
    throw new BackendClientError(`${args.method} ${args.path}: 401 after retry`, 401);
  }

  // ── Read endpoints ────────────────────────────────────────────────

  getTitle(titleId: string): Promise<Record<string, unknown>> {
    return this._request({
      method: "GET",
      path: `/api/v1/tesouraria/negotiation/titles/${encodeURIComponent(titleId)}`,
    });
  }

  getCounterpart(documento: string, tipoTitulo: "AR" | "AP"): Promise<Record<string, unknown>> {
    return this._request({
      method: "GET",
      path: `/api/v1/tesouraria/negotiation/counterparts/${encodeURIComponent(documento)}`,
      query: { tipo_titulo: tipoTitulo },
    });
  }

  getPolicy(titleId: string, counterpartDoc: string): Promise<Record<string, unknown>> {
    return this._request({
      method: "GET",
      path: `/api/v1/tesouraria/negotiation/policy`,
      query: { title_id: titleId, counterpart_doc: counterpartDoc },
    });
  }

  listOpenNegotiations(counterpartDoc: string): Promise<Record<string, unknown>> {
    return this._request({
      method: "GET",
      path: `/api/v1/tesouraria/negotiation`,
      query: { counterpart_doc: counterpartDoc, status: "open" },
    });
  }

  // ── Write endpoints (idempotent via Idempotency-Key) ──────────────

  proposeNegotiation(args: {
    titleId: string;
    counterpartDoc: string;
    tipoTitulo: "AR" | "AP";
    valorTitulo: string;
    descontoPct?: string;
    valorAcordado?: string;
    novoVencimento?: string;
    mensagemAgente?: string;
    idempotencyKey?: string;
  }): Promise<Record<string, unknown>> {
    return this._request({
      method: "POST",
      path: `/api/v1/tesouraria/negotiation/propose`,
      body: {
        title_id: args.titleId,
        counterpart_doc: args.counterpartDoc,
        tipo_titulo: args.tipoTitulo,
        valor_titulo: args.valorTitulo,
        terms: {
          desconto_pct: args.descontoPct ?? null,
          valor_acordado: args.valorAcordado ?? null,
          num_parcelas: 1,
          novo_vencimento: args.novoVencimento ?? null,
          mensagem_agente: args.mensagemAgente ?? "",
        },
      },
      idempotencyKey: args.idempotencyKey,
    });
  }

  counterpropose(args: {
    negociacaoId: string;
    descontoPct?: string;
    valorAcordado?: string;
    mensagemAgente?: string;
    idempotencyKey?: string;
  }): Promise<Record<string, unknown>> {
    return this._request({
      method: "POST",
      path: `/api/v1/tesouraria/negotiation/${encodeURIComponent(args.negociacaoId)}/counterproposal`,
      body: {
        terms: {
          desconto_pct: args.descontoPct ?? null,
          valor_acordado: args.valorAcordado ?? null,
          num_parcelas: 1,
          mensagem_agente: args.mensagemAgente ?? "",
        },
      },
      idempotencyKey: args.idempotencyKey,
    });
  }

  acceptNegotiation(args: {
    negociacaoId: string;
    idempotencyKey?: string;
  }): Promise<Record<string, unknown>> {
    return this._request({
      method: "POST",
      path: `/api/v1/tesouraria/negotiation/${encodeURIComponent(args.negociacaoId)}/accept`,
      body: {},
      idempotencyKey: args.idempotencyKey,
    });
  }

  rejectNegotiation(args: {
    negociacaoId: string;
    reason: string;
    mensagemAgente?: string;
    idempotencyKey?: string;
  }): Promise<Record<string, unknown>> {
    return this._request({
      method: "POST",
      path: `/api/v1/tesouraria/negotiation/${encodeURIComponent(args.negociacaoId)}/reject`,
      body: {
        reason: args.reason,
        mensagem_agente: args.mensagemAgente ?? "",
      },
      idempotencyKey: args.idempotencyKey,
    });
  }

  escalateNegotiation(args: {
    negociacaoId: string;
    reason: string;
    context?: string;
    idempotencyKey?: string;
  }): Promise<Record<string, unknown>> {
    return this._request({
      method: "POST",
      path: `/api/v1/tesouraria/negotiation/${encodeURIComponent(args.negociacaoId)}/escalate`,
      body: {
        reason: args.reason,
        context: args.context ?? "",
      },
      idempotencyKey: args.idempotencyKey,
    });
  }
}
