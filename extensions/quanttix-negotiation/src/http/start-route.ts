import type { IncomingMessage, ServerResponse } from "node:http";
import type { PluginLogger } from "../../api.js";
import { BackendClient } from "../backend/client.js";
import { createBinding, type BindingStoreOptions } from "../state/binding-store.js";

export type StartRouteOptions = {
  mode: "disabled" | "shadow" | "active";
  backend: BackendClient;
  bindingStore: BindingStoreOptions;
  telegramBotToken: string;
  logger: PluginLogger;
  fetchImpl?: typeof fetch;
};

type StartRequestBody = {
  title_id: string;
  tipo_titulo: "AR" | "AP";
  valor_titulo: string;
  counterpart_doc: string;
  counterpart_nome: string;
  channel?: "telegram";
  chat_id: string;
  suggested_discount_pct?: string;
  instructions?: {
    tone?: "cordial" | "direto";
    max_desconto_pct_authorized?: string;
    expiracao_em_horas?: number;
    mensagem_inicial_sugerida?: string;
  };
};

type StartResponseBody = {
  ok: boolean;
  negociacao_id?: string;
  evento_id?: string;
  message_sent?: boolean;
  telegram_message_id?: number;
  mode?: string;
  error?: string;
};

const MAX_BODY_BYTES = 64 * 1024;

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  let received = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    received += buf.length;
    if (received > MAX_BODY_BYTES) {
      throw new Error(`request body exceeds ${MAX_BODY_BYTES} bytes`);
    }
    chunks.push(buf);
  }
  const raw = Buffer.concat(chunks).toString("utf8");
  if (raw.length === 0) return {};
  return JSON.parse(raw);
}

function writeJson(res: ServerResponse, status: number, body: StartResponseBody): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

function formatBrl(value: string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return `R$ ${value}`;
  const fixed = num.toFixed(2);
  const [intPart, decPart] = fixed.split(".");
  const intWithSep = (intPart ?? "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `R$ ${intWithSep},${decPart}`;
}

function defaultInitialMessage(body: StartRequestBody): string {
  if (body.instructions?.mensagem_inicial_sugerida) {
    return body.instructions.mensagem_inicial_sugerida;
  }
  const valorFmt = formatBrl(body.valor_titulo);
  if (body.tipo_titulo === "AR") {
    let opener =
      `Olá, ${body.counterpart_nome}.\n\n` +
      `Estou em contato em nome da Quanttix sobre o título ${body.title_id}, ` +
      `no valor de ${valorFmt}.`;
    if (body.suggested_discount_pct) {
      const pct = Number(body.suggested_discount_pct);
      const valor = Number(body.valor_titulo);
      const comDesconto =
        Number.isFinite(pct) && Number.isFinite(valor)
          ? formatBrl(String(valor * (1 - pct / 100)))
          : valorFmt;
      opener +=
        `\n\nGostaríamos de oferecer condições especiais para regularização: ` +
        `desconto de ${body.suggested_discount_pct}% por pagamento imediato, ` +
        `deixando o valor em ${comDesconto}.`;
    }
    opener += `\n\nVocê tem interesse em conversar sobre essa condição?`;
    return opener;
  }
  // AP
  let opener =
    `Olá, time financeiro da ${body.counterpart_nome}.\n\n` +
    `Aqui é da Quanttix tratando do título ${body.title_id} (${valorFmt}).`;
  if (body.suggested_discount_pct) {
    opener += `\n\nGostaríamos de propor antecipação do pagamento com desconto de ${body.suggested_discount_pct}%.`;
  }
  opener += `\n\nPodemos conversar sobre essa condição?`;
  return opener;
}

async function sendTelegramMessage(args: {
  botToken: string;
  chatId: string;
  text: string;
  fetchImpl: typeof fetch;
}): Promise<{ ok: boolean; messageId?: number; error?: string }> {
  const resp = await args.fetchImpl(`https://api.telegram.org/bot${args.botToken}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: args.chatId, text: args.text }),
  });
  let payload: { ok?: boolean; result?: { message_id?: number }; description?: string };
  try {
    payload = (await resp.json()) as typeof payload;
  } catch {
    return { ok: false, error: `invalid JSON from Telegram (HTTP ${resp.status})` };
  }
  if (!resp.ok || !payload.ok) {
    return { ok: false, error: payload.description ?? `HTTP ${resp.status}` };
  }
  return { ok: true, messageId: payload.result?.message_id };
}

function isStartRequestBody(value: unknown): value is StartRequestBody {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v["title_id"] === "string" &&
    (v["tipo_titulo"] === "AR" || v["tipo_titulo"] === "AP") &&
    typeof v["valor_titulo"] === "string" &&
    typeof v["counterpart_doc"] === "string" &&
    typeof v["counterpart_nome"] === "string" &&
    typeof v["chat_id"] === "string"
  );
}

export function createStartRouteHandler(opts: StartRouteOptions) {
  const fetchImpl = opts.fetchImpl ?? fetch;

  return async function startRouteHandler(
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> {
    if (req.method !== "POST") {
      writeJson(res, 405, { ok: false, error: "method not allowed" });
      return;
    }

    let body: unknown;
    try {
      body = await readJsonBody(req);
    } catch (err) {
      writeJson(res, 400, { ok: false, error: `invalid body: ${String(err)}` });
      return;
    }

    if (!isStartRequestBody(body)) {
      writeJson(res, 400, { ok: false, error: "missing required fields" });
      return;
    }

    const idem = `handoff:${body.title_id}:${body.chat_id}`;
    opts.logger.info(
      `[quanttix-negotiation] /start mode=${opts.mode} title=${body.title_id} tipo=${body.tipo_titulo} chat=${body.chat_id}`,
    );

    try {
      const evento = await opts.backend.proposeNegotiation({
        titleId: body.title_id,
        counterpartDoc: body.counterpart_doc,
        tipoTitulo: body.tipo_titulo,
        valorTitulo: body.valor_titulo,
        descontoPct: body.suggested_discount_pct,
        mensagemAgente: "proposta inicial — plugin quanttix-negotiation",
        idempotencyKey: idem,
      });

      const negociacaoId = String(evento["negociacao_id"] ?? "");
      const eventoId = String(evento["evento_id"] ?? "");
      if (!negociacaoId) {
        writeJson(res, 502, { ok: false, error: "backend returned no negociacao_id" });
        return;
      }

      await createBinding(opts.bindingStore, {
        chatId: body.chat_id,
        negociacaoId,
        counterpartDoc: body.counterpart_doc,
        counterpartNome: body.counterpart_nome,
        tipoTitulo: body.tipo_titulo,
        titleId: body.title_id,
      });

      if (opts.mode === "shadow") {
        opts.logger.info(
          `[quanttix-negotiation][shadow] would send Telegram to chat=${body.chat_id} for neg=${negociacaoId}`,
        );
        writeJson(res, 200, {
          ok: true,
          mode: "shadow",
          negociacao_id: negociacaoId,
          evento_id: eventoId,
          message_sent: false,
        });
        return;
      }

      const text = defaultInitialMessage(body);
      const send = await sendTelegramMessage({
        botToken: opts.telegramBotToken,
        chatId: body.chat_id,
        text,
        fetchImpl,
      });

      writeJson(res, 200, {
        ok: true,
        mode: opts.mode,
        negociacao_id: negociacaoId,
        evento_id: eventoId,
        message_sent: send.ok,
        telegram_message_id: send.messageId,
        error: send.ok ? undefined : send.error,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      opts.logger.warn(`[quanttix-negotiation] /start failed: ${msg}`);
      writeJson(res, 500, { ok: false, error: msg });
    }
  };
}
