# 浏览器会话与工作区上下文契约

适用：本批 A37/A08 候选；具体源码 SHA、测试和 CI 签收见 `IMPLEMENTATION_PROGRESS.md`。本文件不是部署证明。

## 1. 要保证什么

标签页显示 A 身份/工作区时，不能因为另一个标签页已经切到 B、浏览器共享 Cookie 更新，就把原页面操作解释成 B 的操作。身份变化后的旧响应不能覆盖页面，分页不能中途继承新身份；页面中尚未保存的文字不得因此自动消失。

同一受支持前端来源的标签页应串行处理刷新、登录、切工作区和退出等 Cookie 变更，不把正常双页续期当成旧刷新令牌重放。但真正的历史 refresh token 重放仍撤销会话，不能宽泛接受旧令牌来掩盖竞争。

## 2. 服务端前置条件

- `GET /auth/session` 返回 `context`，由服务端对登录会话 ID、用户 ID 和工作区 ID 做 HMAC。登录或换工作区会变化；同一会话单纯续期不变化。它不是登录凭据，只有 context 没有有效 Cookie/Bearer 不能认证。
- Cookie 鉴权的受保护 API 必须带 `X-ContentFlow-Context`；不能靠省略 `X-ContentFlow-Session-Mode` 绕过。缺少为 **428 / session_context_required**，不匹配为 **409 / session_context_changed**；响应不泄露新 context。
- 唯一读取例外是无 context 的 `GET /auth/session`，供初次页面恢复当前 Cookie 身份。若提供 context，就必须匹配。业务集合、素材下载、上传、发布预览和确认没有相同例外。
- `POST /auth/bootstrap` 在 access Cookie 过期、refresh Cookie 有效时返回身份/context，不轮换 Cookie、不返回 access token。必须可信 Origin，执行现有认证限流/重放/权限撤销检查；“只读 bootstrap”不是承诺绝不写限流或安全撤销记录。
- `POST /auth/refresh` 在验证当前 refresh Cookie 和上下文后才轮换。发现历史令牌仍按原重放策略撤销，即使缺少或提交了错误 context，也不能让被盗历史令牌绕过安全检测。
- Cookie logout 对所识别的会话先检查 context，再撤销/清 Cookie；旧页面不能用新 Cookie 注销另一当前身份。logout-all 仍是当前用户的全会话操作。
- Bearer 调用继续使用明确 JWT 与数据库会话校验，不要求浏览器 context；context 不能替代现有工作区过滤或角色门禁。

context 不是每次 UI 导航的随机 nonce。A→B→A 回到同一登录会话/工作区时可能相同；正文版本、发布确认指纹和操作编号仍负责对象级并发/重复操作保护。

## 3. 客户端状态和刷新顺序

每个请求固定 `{API base, context, epoch}`，分页整批共用同一份。响应头后、JSON/Blob 完成后都检查页面代次，过期结果抛出 `StaleResponseError`，不进入新视图。全量加载/轮询另有批次代次，旧批次不能覆盖新批次。

不再消费的迟到响应、401 业务响应和恢复探测响应会尽力取消未读 body，释放资源；取消失败不能替代上下文错误或阻止页面锁定。这只清理本地响应流，不表示撤销服务器操作，也不触发重试。已经读取中的 JSON/Blob 仍在读取完成后拒绝过期结果。

`BroadcastChannel` 只通知旧页暂停，不根据广播直接切身份或采用 context；服务端前置条件才是防错投依据。广播不可用时，实际旧请求仍返回 409。

401 恢复流程在共享 Web Lock 内：

1. 查询当前 `/auth/session`，同时携带原页面 context；若其他页已经续期成功，不再旋转一次。
2. 新页面没有 context 且 access 已过期时，通过 bootstrap 取得前置条件。
3. 使用有效 context 刷新一次，读完回执，再查询身份，最后释放锁。
4. 原业务请求最多补发一次，仍用原上下文。不能把原写操作迁移到新工作区。

同页重复刷新共享一个 Promise。排队取锁最长 15 秒；单次认证 HTTP 最长 20 秒。刷新回执不确定时不盲目重试，不放宽令牌重放保护；页面锁住新请求、保留输入，让用户显式确认恢复。

## 4. 使用和兼容边界

- 正式部署只支持一个规范前端来源，并让所有页面使用同一规范 API base；不要同时开旧前端和新前端作为同一个 Cookie 会话的入口。
- [Web Locks](https://w3c.github.io/web-locks/) 协调共享 storage bucket 内的同源页面，不是跨域、跨浏览器 profile 或跨设备的分布式锁。多个不同前端来源共享 API Cookie 的并发续期不在本批支持范围；服务端上下文仍防错投，但不能承诺不出现会话失效。
- 无 Web Locks 时不自动刷新过期 Cookie；允许显式登录，提示重新认证/HTTPS。显式操作仍受服务器鉴权和上下文约束，但不声称不支持的浏览器获得跨页 Cookie 排序保证。
- localStorage 拒绝访问不阻止 Cookie 登录。API base 在页内固定，其他页修改本地配置不能重定向已开始的请求；正式构建继续由 CSP 和固定 API 配置限制目的地。
- 会话变化时保留当前页面与输入，停止轮询/新请求；“同步当前会话”必须确认后重载。用户仍可复制文字。不是持久草稿：关页/崩溃后的恢复属于尚未完成的 A17。
- 无法撤回已经发到服务器并在 A 身份下通过校验的请求。它可能合法完成 A 的操作；本批保证不把它解释为 B、旧响应不覆盖 B，不保证取消所有在途副作用。
- 业务数据的提交游标/项目筛选、内容编辑的版本冲突、生成幂等、供应商执行权不是会话锁的职责，继续按各自审计编号整改。

## 5. 升级与回退

本批没有新数据库迁移，既有数据库会话可通过 session/bootstrap 获得 context。但 API/Web 协议不向旧 Cookie 客户端透明兼容：旧客户端业务请求缺头会收到 428。

部署前备份并确认已验证候选；暂停测试写入，提醒用户保存/复制未提交内容；协调 API/Web 更新，检查同版标识，然后显式重载页面。不要静默刷新丢稿，也不要做“遇到 428 自动去掉头/忽略校验”的兼容后门。

仍使用项目既有 API/Web/Worker/schema 统一候选规则。回退 API/Web 必须协调且记录风险重新开放，不得只回滚一侧；不以降级删除数据库会话或更换签名密钥来绕过兼容问题。本轮不操作 Ubuntu、生产 Cookie 或真实账号。

## 6. 验收

- `tests/test_session_context.py`：缺失/过期上下文、换用户/工作区、上传/下载/发布、bootstrap Origin、历史令牌重放、无业务落库和无 Cookie 变更。
- `tests/test_postgres_sessions.py`：同旧 refresh 并发保留重放撤销、缓存身份等待行锁后复核、正常刷新后切区。只能用专用 PG 创建的临时数据库，skip 不算通过。
- `web/tests/browser/session.spec.ts`：真实页面/API 的双页切区、无广播拒绝、刷新竞争、刷新与切区交错、失回执、无锁与存储受限。
- `web/tests/browser-session.test.mjs`：执行实际 TS 模块，显式等待迟到响应/JSON/Blob/分页的 Promise 终结后断言拒绝；验证生产 API 配置不可覆盖、存储失败和未读响应释放/释放失败。该测试补足浏览器负向断言可能过早的问题；浏览器旅程另等待 held 请求成功结束或被明确取消，不用固定睡眠制造通过。

测试不访问真实平台、不触发收费调用，不把模块测试替代浏览器或真实 PostgreSQL。准确结果与剩余边界见实施进度。
