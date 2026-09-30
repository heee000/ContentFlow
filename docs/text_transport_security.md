# 文本模型 HTTP 传输边界

更新：2026-09-24。对应 A36，实际验收/部署状态见实施进度页。

## 已实施

OpenAI-compatible 文本适配器使用每次请求独立的 urllib opener，禁止所有 HTTP 重定向，包括同域。301/302/303/307/308 都返回受控 HTTP 错误，不发送第二跳，不向其他 Origin 或降级 HTTP 转发 Authorization。

不安装全局 opener，不改 Windows/Ubuntu 代理、DNS、TLS 校验或系统网络配置；正常的代理/TLS 行为保留。供应商给出的规范地址需要配置为最终 HTTPS 地址，不能依赖自动跳转。

异常只保留受控类别、合法数字状态和经过校验的请求 ID；不拼接响应正文/带凭据的 URL，也不通过异常链重新暴露底层错误。收到 HTTP 错误不等于供应商肯定没有执行或扣费，原调用账本和人工核对门禁继续保留，不因此开放盲重试。

## 测试

`tests/test_text_transport.py` 使用真实 urllib 跳转机制与离线 HTTP/HTTPS transport，虚构凭据、无 sockets。15 种组合覆盖五种状态码和同 HTTPS、跨 HTTPS、降级到回环 HTTP，断言只有第一条请求且格式化异常不含凭据。

旧 provider 错误、provenance、invocation 测试仅把 Mock 接缝改为受控 opener，保留原断言。它们和全套一起验证正常响应/失败记录，不调用真实供应商。

## 尚未完成

- 这项修复不是 A12 的输出 token、响应大小、总时间、预算和队列配额实现。
- 这项修复不是所有外部客户端已经具有统一 DNS/解析 IP/网络出口防护的证明。
- 仓库修复不等于 Ubuntu 已部署；历史凭据影响需要单独核查，不打印秘密、自动轮换或以旧授权重发收费请求。
