"use client";

import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/contentflow-api";
import { clearGenerationIntent, persistGenerationIntent, readGenerationIntent, type GenerationIntent } from "@/lib/generation-intents";

export function useGenerationRequest(scope: string, onAccepted: (runId: string, campaignId: string) => Promise<void>) {
  const [initial] = useState(() => {
    try { return { pending: readGenerationIntent(scope), error: "" }; }
    catch { return { pending: null, error: "无法读取生成回执存储，请先恢复浏览器会话存储并核对已有任务；本页不会发送新生成请求。" }; }
  });
  const [pending, setPending] = useState<GenerationIntent | null>(initial.pending);
  const [error, setError] = useState(initial.error);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  async function send(intent: GenerationIntent) {
    setBusy(true);
    try {
      const receipt = await api<{ id: string; campaign_id: string; request_json: { generation_request_id?: string } }>(
        `/campaigns/${encodeURIComponent(intent.campaignId)}/runs`, {
          method: "POST", headers: { "Idempotency-Key": intent.requestId }, body: intent.body,
          signal: AbortSignal.timeout(30_000),
        });
      if (!receipt.id || receipt.campaign_id !== intent.campaignId
        || receipt.request_json?.generation_request_id !== intent.requestId) throw new Error("生成回执不匹配，请保留原操作编号核对");
      clearGenerationIntent(scope, intent);
      setPending(null);
      try { await onAccepted(receipt.id, receipt.campaign_id); }
      catch { setError(`任务 ${receipt.id} 已确认入队，但页面刷新失败，请刷新数据；不要重复生成。`); }
    } catch (caught) {
      // This explicit code is emitted only after a locked lookup finds no
      // accepted receipt and the old Brief precondition no longer matches.
      if (caught instanceof ApiError && caught.code === "generation_precondition_failed") {
        try { clearGenerationIntent(scope, intent); setPending(null); }
        catch { /* Keep the original operation available if cleanup fails. */ }
      }
      setError(caught instanceof Error ? caught.message : "无法确认生成结果，请核对原请求");
    } finally { inFlight.current = false; setBusy(false); }
  }

  async function start(campaign: { id: string; name: string; updated_at: string }) {
    if (inFlight.current || pending || initial.error) return;
    inFlight.current = true;
    setError("");
    let intent: GenerationIntent;
    try {
      intent = { version: 1, requestId: crypto.randomUUID(),
        campaignId: campaign.id, campaignName: campaign.name,
        body: { expected_campaign_updated_at: campaign.updated_at } };
      persistGenerationIntent(scope, intent);
    }
    catch (caught) {
      inFlight.current = false;
      setError(caught instanceof Error ? caught.message : "无法保存生成请求，本次未发送");
      return;
    }
    setPending(intent);
    await send(intent);
  }

  async function replay() {
    if (!pending || inFlight.current || initial.error) return;
    inFlight.current = true;
    setError("");
    await send(pending);
  }

  return { pending, busy, error, blocked: Boolean(pending || busy || initial.error), start, replay };
}

export function GenerationRequestNotice({ request }: { request: ReturnType<typeof useGenerationRequest> }) {
  return <>
    {request.error ? <p role="alert" className="inline-error">{request.error}</p> : null}
    {request.pending ? <section className="panel form-panel" aria-label="待核对的生成请求">
      <h3>{request.busy ? "正在确认生成请求…" : "生成回执待核对"}</h3>
      <p>项目：{request.pending.campaignName} · {request.pending.campaignId}</p>
      <p>请求可能已经入队。请使用原编号核对或继续原请求，不要重新创建一份生成任务。刷新页面后此记录仍保留在本标签页。</p>
      <p>操作编号：<code>{request.pending.requestId}</code></p>
      <button type="button" disabled={request.busy} onClick={() => void request.replay()}>核对或继续原生成请求</button>
    </section> : null}
  </>;
}
