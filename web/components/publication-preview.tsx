"use client";
/* eslint-disable @next/next/no-img-element -- Authenticated verified blob URLs cannot use image optimization. */

import { useCallback, useEffect, useState } from "react";
import { apiBlob } from "@/lib/contentflow-api";

export type PublishIntent = {
  content_item_id: string;
  channel_id: string;
  delivery_mode: string;
  publish_now: boolean;
  scheduled_at?: string;
  request_id: string;
  preview_token: string;
};

export type PublicationPreviewData = {
  fingerprint: string; preview_token: string; content_version: number;
  content_id: string; campaign_id: string; title: string; platform: string;
  channel_name: string; delivery_mode: string; publish_timing: string;
  scheduled_at: string | null; layout: Record<string, unknown>;
  document: { format: string; text: string; behavior: string; fields: Record<string, unknown> };
  assets: { id: string; kind: string; mime_type: string; size_bytes: number; checksum: string; used_in_delivery: boolean }[];
};

export function readPendingPublication(key: string): PublishIntent | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    if (value && typeof value.request_id === "string" && typeof value.preview_token === "string"
      && typeof value.content_item_id === "string" && typeof value.channel_id === "string") return value;
  } catch { /* No usable saved receipt; normal submission persists before sending. */ }
  return null;
}

function VerifiedAsset({ asset, onState }: {
  asset: PublicationPreviewData["assets"][number];
  onState: (id: string, ready: boolean) => void;
}) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const image = ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(asset.mime_type);
  const video = asset.mime_type.startsWith("video/");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const abort = new AbortController();
    apiBlob(`/publishing/preview-assets/${asset.id}?checksum=${asset.checksum}`, abort.signal)
      .then(async (blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
        if (!image && !video) {
          const source = asset.mime_type === "application/json" ? await blob.text() : "";
          if (active) { setText(source); onState(asset.id, true); }
        }
      }).catch((caught) => {
        if (active) { setError(caught instanceof Error ? caught.message : "素材加载失败"); onState(asset.id, false); }
      });
    return () => { active = false; abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [asset.id, asset.checksum, asset.mime_type, image, video, onState]);
  return <article className="publication-preview-asset">
    <strong>{image ? "封面图片" : video ? "发布视频" : "投放素材"}</strong>
    {!url && !error ? <p role="status">正在加载并校验素材…</p> : null}
    {error ? <p role="alert" className="inline-error">{error}。请重新获取预览。</p> : null}
    {url && image ? <img src={url} alt="此次发布使用的封面" onLoad={() => onState(asset.id, true)}
      onError={() => { setError("图片无法显示"); onState(asset.id, false); }} /> : null}
    {url && video ? <video src={url} controls preload="metadata" onLoadedMetadata={() => onState(asset.id, true)}
      onError={() => { setError("视频无法显示"); onState(asset.id, false); }} /> : null}
    {text ? <pre>{text}</pre> : null}
    {url ? <a href={url} download={`publication-${asset.id}`}>下载核对原文件</a> : null}
    <small>SHA-256：{asset.checksum}</small>
  </article>;
}

export function PublicationPreview({ preview, busy, onConfirm }: {
  preview: PublicationPreviewData; busy: boolean; onConfirm: () => void;
}) {
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const [acknowledged, setAcknowledged] = useState(false);
  const onState = useCallback((id: string, value: boolean) => {
    setReady((previous) => previous[id] === value ? previous : { ...previous, [id]: value });
  }, []);
  const usedAssets = preview.assets.filter((asset) => asset.used_in_delivery);
  const allLoaded = usedAssets.length > 0 && usedAssets.every((asset) => ready[asset.id]);
  return <section className="publication-preview" aria-label="最终发布确认">
    <h3>核对本次实际发布物 · v{preview.content_version}</h3>
    <p><strong>{preview.channel_name}</strong> · {preview.platform}</p>
    <p className="safe-notice">{preview.document.behavior}</p>
    <p>{preview.publish_timing === "immediate" ? "确认后立即进入队列" : `计划执行：${new Date(preview.scheduled_at!).toLocaleString()}`}</p>
    <h4>{preview.title}</h4>
    <pre className="publication-preview-text">{preview.document.text}</pre>
    {preview.document.format === "wechat_plain_text" ? <p>
      作者：{String(preview.document.fields.author || "未填写")} · 摘要：{String(preview.document.fields.digest || "")}
    </p> : null}
    <div className="publication-preview-assets">{usedAssets.map((asset) =>
      <VerifiedAsset key={`${asset.id}:${asset.checksum}`} asset={asset} onState={onState} />)}</div>
    {preview.assets.some((asset) => !asset.used_in_delivery) ? <p>本渠道只使用上方所示主素材；其余已准备素材不会由该官方接口发送。</p> : null}
    <details><summary>查看平台排版与精确字段</summary><pre>{JSON.stringify({ fields: preview.document.fields, layout: preview.layout }, null, 2)}</pre></details>
    <small>这是应用提交内容预览，不是平台排版截图。确认指纹：{preview.fingerprint.slice(0, 16)}；15 分钟内有效。</small>
    <label className="publication-acknowledgement"><input type="checkbox" checked={acknowledged}
      onChange={(event) => setAcknowledged(event.target.checked)} disabled={busy || !allLoaded} />
      我已核对正文、素材、目标账号和执行方式，同意本次提交
    </label>
    <button type="button" className="button button-primary" disabled={busy || !acknowledged || !allLoaded}
      onClick={onConfirm}>{busy ? "正在确认…" : "确认这份发布物"}</button>
  </section>;
}
