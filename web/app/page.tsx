import type { Metadata } from "next";
import { ContentFlowApp } from "./contentflow-app";

export const metadata: Metadata = {
  title: "ContentFlow 内容运营工作台",
  description: "从知识检索、内容生成、审核到分发复盘的 AI 内容营销工作台。",
};

export default function Home() {
  return <ContentFlowApp />;
}
