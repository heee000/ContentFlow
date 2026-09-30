export type PublishIntent = Readonly<{
  content_item_id: string;
  channel_id: string;
  delivery_mode: "connector" | "script" | "manual_export";
  publish_now: boolean;
  scheduled_at?: string;
  request_id: string;
  preview_token: string;
}>;

const fields = ["content_item_id", "channel_id", "delivery_mode", "publish_now", "scheduled_at", "request_id", "preview_token"];

function validate(value: unknown): PublishIntent {
  const intent = value as PublishIntent | null;
  if (!intent || typeof intent !== "object" || Array.isArray(intent)
    || Object.keys(intent).some((key) => !fields.includes(key))
    || ![intent.content_item_id, intent.channel_id].every((id) => typeof id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(id))
    || !["connector", "script", "manual_export"].includes(intent.delivery_mode)
    || typeof intent.publish_now !== "boolean"
    || typeof intent.request_id !== "string" || !/^[A-Za-z0-9._:-]{8,80}$/.test(intent.request_id)
    || typeof intent.preview_token !== "string" || !/^[A-Za-z0-9._-]{64,1200}$/.test(intent.preview_token)
    || (intent.publish_now ? intent.scheduled_at !== undefined : (
      typeof intent.scheduled_at !== "string"
      || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(intent.scheduled_at)
      || !Number.isFinite(Date.parse(intent.scheduled_at))))) {
    throw new Error("发布请求记录损坏或不完整，请先核对原任务，不能直接重新发布");
  }
  return intent;
}

function identity(intent: PublishIntent): string {
  return JSON.stringify(fields.map((key) => intent[key as keyof PublishIntent] ?? null));
}

export function readPendingPublication(key: string): PublishIntent | null {
  if (typeof window === "undefined") return null;
  const raw = sessionStorage.getItem(key);
  return raw === null ? null : validate(JSON.parse(raw));
}

export function persistPublicationIntent(key: string, intent: PublishIntent): void {
  validate(intent);
  const current = readPendingPublication(key);
  if (current) {
    if (identity(current) !== identity(intent)) throw new Error("还有另一份发布请求待核对，不能覆盖原记录");
    return;
  }
  const raw = JSON.stringify(intent);
  sessionStorage.setItem(key, raw);
  if (sessionStorage.getItem(key) !== raw) throw new Error("无法确认发布请求已安全保存，本次未发送");
}

export function clearPublicationIntent(key: string, intent: PublishIntent): void {
  const current = readPendingPublication(key);
  // Another mounted lifetime can receive the same receipt first.
  if (!current) return;
  if (identity(current) !== identity(intent)) throw new Error("发布记录已变化，不能清除另一份请求，请重新读取回执");
  sessionStorage.removeItem(key);
  if (sessionStorage.getItem(key) !== null) throw new Error("已找到原任务，但清理回执失败，请保留原编号");
}

export function inspectPublicationIntent(key: string): { pending: PublishIntent | null; error: string; raw: string | null; unavailable: boolean } {
  if (typeof window === "undefined") return { pending: null, error: "", raw: null, unavailable: false };
  let raw: string | null;
  try { raw = sessionStorage.getItem(key); }
  catch { return { pending: null, error: "无法读取发布回执存储，请恢复浏览器会话存储后重新读取；本页不会发送新发布。", raw: null, unavailable: true }; }
  try { return { pending: raw === null ? null : validate(JSON.parse(raw)), error: "", raw, unavailable: false }; }
  catch { return { pending: null, error: "发布回执损坏或不完整。请先在发布列表及平台核对原操作，不要直接重新发布。", raw, unavailable: false }; }
}

export function discardInvalidPublication(key: string, expectedRaw: string): void {
  // Only after an explicit human reconciliation. Never discard a new valid
  // record or changed snapshot, including a late response from another mount.
  const snapshot = inspectPublicationIntent(key);
  if (snapshot.unavailable || !snapshot.error || snapshot.raw !== expectedRaw) throw new Error("回执已经变化或仍不可读，禁止清除，请重新读取核对");
  sessionStorage.removeItem(key);
  if (sessionStorage.getItem(key) !== null) throw new Error("损坏回执清理失败，请恢复存储后重新核对");
}
