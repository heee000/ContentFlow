export type GenerationIntent = Readonly<{
  version: 1;
  requestId: string;
  campaignId: string;
  campaignName: string;
  body: Readonly<{ expected_campaign_updated_at: string }>;
}>;

export function generationScope(base: string, user: string, workspace: string): string {
  return `contentflow-generation:v1:${JSON.stringify([base, user, workspace])}`;
}

export function readGenerationIntent(key: string): GenerationIntent | null {
  const raw = sessionStorage.getItem(key); // Fail closed on unavailable storage.
  if (raw === null) return null;
  const value = JSON.parse(raw);
  if (value?.version !== 1 || typeof value.requestId !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(value.requestId)
    || typeof value.campaignId !== "string" || !value.campaignId
    || typeof value.campaignName !== "string" || !value.campaignName
    || typeof value.body?.expected_campaign_updated_at !== "string"
    || !/^\d{4}-\d{2}-\d{2}T/.test(value.body.expected_campaign_updated_at)
    || !Number.isFinite(Date.parse(value.body.expected_campaign_updated_at))
    || Object.keys(value.body).length !== 1) throw new Error("生成请求记录无法校验，请先核对已有任务，不能直接重复生成");
  return value;
}

export function persistGenerationIntent(key: string, intent: GenerationIntent): void {
  if (sessionStorage.getItem(key) !== null) throw new Error("还有生成请求待核对，请先处理原请求");
  const raw = JSON.stringify(intent);
  sessionStorage.setItem(key, raw);
  if (sessionStorage.getItem(key) !== raw) throw new Error("无法确认生成请求已安全保存，本次未发送");
}

export function clearGenerationIntent(key: string, intent: GenerationIntent): void {
  const current = readGenerationIntent(key);
  // Two mounted lifetimes can receive the same accepted receipt (e.g. leaving
  // and returning during a delayed POST). Already cleared is safe/idempotent.
  if (!current) return;
  if (JSON.stringify(current) !== JSON.stringify(intent)) throw new Error("生成请求记录已变化，请重新载入核对，不能清除其他请求");
  sessionStorage.removeItem(key);
  if (sessionStorage.getItem(key) !== null) throw new Error("无法清除已核对的生成请求，请保留原编号");
}
