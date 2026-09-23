"use client";

import { useRef, useState } from "react";
import { api, ApiError } from "@/lib/contentflow-api";
import { clearPublicationIntent, discardInvalidPublication, inspectPublicationIntent, persistPublicationIntent, type PublishIntent } from "@/lib/publication-intents";

type Receipt = { id: string; request_id: string | null; content_item_id: string; channel_id: string };

export function usePublicationRequest(key: string, onAccepted: (id: string) => Promise<void>, onRejected: () => void) {
  const [state, setState] = useState(() => inspectPublicationIntent(key));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);

  function reload() {
    if (inFlight.current) return;
    setState(inspectPublicationIntent(key)); setError("");
  }

  async function send(intent: PublishIntent, readonly = false) {
    if (inFlight.current || state.error) return;
    inFlight.current = true; setBusy(true); setError("");
    const previouslyPending = state.pending !== null;
    let persisted = false;
    try {
      persistPublicationIntent(key, intent);
      persisted = true;
      setState(inspectPublicationIntent(key));
      const receipt = await api<Receipt>(readonly
        ? `/publishing/intents/${encodeURIComponent(intent.request_id)}` : "/publishing/jobs", {
        ...(readonly ? {} : { method: "POST", body: intent }), signal: AbortSignal.timeout(30_000),
      });
      if (typeof receipt.id !== "string" || !receipt.id
        || receipt.request_id !== intent.request_id || receipt.content_item_id !== intent.content_item_id
        || receipt.channel_id !== intent.channel_id) throw new Error("发布回执身份不匹配，请保留原编号核对，不要重新创建发布");
      clearPublicationIntent(key, intent);
      setState(inspectPublicationIntent(key));
      try { await onAccepted(receipt.id); }
      catch { setError(`发布任务 ${receipt.id} 已确认，但页面刷新失败，请刷新数据；不要重复发布。`); }
    } catch (caught) {
      if (persisted && !readonly && !previouslyPending && caught instanceof ApiError
        && caught.status >= 400 && caught.status < 500 && ![408, 429].includes(caught.status)
        && !["publish_intent_conflict", "publish_receipt_incomplete"].includes(caught.code ?? "")) {
        try { clearPublicationIntent(key, intent); onRejected(); }
        catch { /* Preserve a changed or unreadable receipt. */ }
      }
      const next = inspectPublicationIntent(key);
      setState(!persisted ? { ...next, error: next.error || "发布回执未能安全保存，本次未发送。请恢复存储后重新读取。" } : next);
      setError(caught instanceof Error ? caught.message : "无法确认发布结果，请保留原编号核对");
    } finally { inFlight.current = false; setBusy(false); }
  }

  function manuallyResolved() {
    if (inFlight.current || state.unavailable) return;
    if (!window.confirm("清除本地回执不会取消任何服务器任务。请先核对下方任务编号及平台记录；原请求可能仍在处理中。只有已人工确认不会重复发布时才能重新开始。你已完成核对吗？")) return;
    try {
      if (state.pending) clearPublicationIntent(key, state.pending);
      else if (state.raw !== null) discardInvalidPublication(key, state.raw);
      else return;
      onRejected();
      setState(inspectPublicationIntent(key)); setError("");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "回执清理失败，请保留原记录"); }
  }

  return { pending: state.pending, busy, blocked: Boolean(state.pending || state.error || busy),
    error, storageError: state.error, canDiscard: !state.unavailable && (state.pending !== null || state.raw !== null),
    reload, send, lookup: () => state.pending && send(state.pending, true), manuallyResolved };
}

export function PublicationRequestNotice({ request }: { request: ReturnType<typeof usePublicationRequest> }) {
  return <>
    {request.error ? <p role="alert" className="inline-error">{request.error}</p> : null}
    {request.storageError ? <section className="panel safe-notice" aria-label="发布回执存储异常">
      <p role="alert">{request.storageError}</p>
      <button type="button" disabled={request.busy} onClick={request.reload}>重新读取回执</button>
      {request.canDiscard ? <button type="button" disabled={request.busy} onClick={request.manuallyResolved}>已核对平台及任务，清除损坏回执</button> : null}
    </section> : null}
    {request.pending ? <section className="panel safe-notice" aria-label="待核对的发布回执">
      <p>{request.busy ? "正在确认原发布请求…" : "请求可能已经接受。请先查询原编号，不要重新创建发布任务。"}</p>
      <p>操作编号：<code>{request.pending.request_id}</code></p>
      <small>稿件：{request.pending.content_item_id} · 渠道：{request.pending.channel_id}</small>
      <button type="button" disabled={request.busy || !!request.storageError} onClick={() => void request.lookup()}>只读查询原任务</button>
      <button type="button" disabled={request.busy || !!request.storageError} onClick={() => void request.send(request.pending!)}>重试获取原任务</button>
      <button type="button" disabled={request.busy} onClick={request.manuallyResolved}>已人工核对，重新开始</button>
    </section> : null}
  </>;
}
