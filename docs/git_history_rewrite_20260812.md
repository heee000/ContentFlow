# Git 历史身份重写记录（2026-08-12）

本记录用于解释一次经仓库所有者明确授权的作者身份迁移。重写前的提交使用无法关联 GitHub 账号的本地占位邮箱；重写后，属于项目维护者的提交使用 `John Wang <182348029+heee000@users.noreply.github.com>`。

## 范围与不变量

- 覆盖 9 个远程分支、30 个可达提交；其中 21 个提交的作者和提交者身份被迁移。
- 每个重写提交的 tree SHA 与重写前完全相同，文件内容、提交消息和时间保持不变。
- 7 个 Dependabot 提交保留 `dependabot[bot]` 作者，但父 SHA 改变使原 GPG 签名失效，因此新对象不携带旧签名。
- 既有 CI 链接仍证明重写前的旧 SHA；不得把它们表述为已验证新 SHA。重写后的 tip 由新的 GitHub Actions 运行重新签收。
- 重写前完整 bundle 的 SHA-256：`196FB9C6EF87E1B8A964E22E6A16CE7CCF5DAEB5ED9711A40A3AE873E36B476F`。恢复包仅保存在操作机的忽略目录中，不进入仓库。

## 提交映射

| 重写前 SHA | 重写后 SHA | 身份处理 | 提交说明 |
| --- | --- | --- | --- |
| `9411bed0138e6d357afdc44bb5882c2220c938b4` | `656c38305953b35600e19d39fc4b0f26d413d500` | 迁移到 John Wang (@heee000) | Initial public release of ContentFlow |
| `edc523a0b798deee5b4fdcf8db99b1b70276a326` | `48733707460632d5c0c5d15f4f48a932a386a74e` | 迁移到 John Wang (@heee000) | Harden local runtime and database upgrades |
| `b24e47f011e4b7ec1017b8f2cf50ba51bf1ce292` | `dc6ab043f22f4db7f531f088e8c8558998a4b3a6` | 迁移到 John Wang (@heee000) | Advance ContentFlow production readiness |
| `2b80fefebaf090492fe1643a376f206712618092` | `1b314d3def4e95ab8da42b3d229fe6e8639f3cb0` | 原作者保留 | Update setuptools requirement from >=68 to >=83.0.0 |
| `dca72dbacd13d9349b4316b94a3bafb1d1d51836` | `8d3c97fd71790bf4d13ddef81bca0f5900f63264` | 原作者保留 | Bump eslint from 9.39.4 to 10.8.0 in /web |
| `0122229f0c1d9ee360d2a2ebb3def4f3e6ae5b23` | `399e5f03a19201b13fc0a5e0b6337956a56fbed6` | 原作者保留 | Bump typescript from 5.9.3 to 7.0.2 in /web |
| `eff78d9686c240abc1dcd20f1365fac9ff965d5a` | `292c2a516725f78cf0b3a8a1df66ad128e6bba22` | 原作者保留 | Bump @types/node from 22.19.19 to 26.1.2 in /web |
| `29563c4afbd69c3e66425dda161bbfd4d518c7ad` | `9491596823fe367e9dbc4b83e31b14b97b4960ad` | 迁移到 John Wang (@heee000) | Fix tracked Sites build plugin |
| `beaeaf183a51a35484b25a2e5d90c870dafa7689` | `71b8b0fe10e6177afe9e4643a63d4e7a77feb33a` | 迁移到 John Wang (@heee000) | Add governed prompt releases |
| `1be5c4c57f7830c4c554ae41609a65de987dec00` | `6192c0930f2f2f957f517751241c5e15f44ea40c` | 迁移到 John Wang (@heee000) | Record prompt governance CI evidence |
| `4a9f8da4a56330c09e0b1c173f4480471ce29509` | `40c4b3aadadd13c37e0204599ecdb71b3d478793` | 迁移到 John Wang (@heee000) | Add versioned prompt evaluation gates |
| `0dfe8f98f065a1eac6bf8c4f38308726e16acb7f` | `f9f227b6e6a23022df6581c69e18b9873818fda1` | 迁移到 John Wang (@heee000) | Record prompt evaluation CI evidence |
| `47fe3444d9a4a2f7c2c8a284c4e6b0b95fcad4c2` | `18a844cc9fa31dc844860da6328e65f2d22dc01f` | 迁移到 John Wang (@heee000) | Enforce governed prompts in production |
| `27ee098946a2071486b593b719509a16c35b91ec` | `5ed1a2dd713258689627fd9e9bfba4c073cbbb78` | 迁移到 John Wang (@heee000) | Record governed prompt CI evidence |
| `fe3ee101799e36dc05e644f51efbca8204cc7b02` | `c368b8f51c4b94569f76fd6e97a11885b6dbe989` | 迁移到 John Wang (@heee000) | Add protected Prometheus metrics |
| `365b5048d01d20b2a5cffe0b1369fe2237c96c94` | `a17ab266bc0822ed928236f8b141675f5225ac1b` | 原作者保留 | Update psycopg[binary] requirement from <4.0,>=3.2 to >=3.3.4,<4.0 |
| `a4b7deea4b1e8575dd3ed4b0635fdaa8d7653ee8` | `d8cdeb34d40861a036540aa9740b4ad895e72f9e` | 迁移到 John Wang (@heee000) | Record metrics CI evidence |
| `a57b662128e8369ab2f5dd327453b5f9e1ecea98` | `eae4d46c17f5f9f8d76c9f0157b958bdb12e335e` | 迁移到 John Wang (@heee000) | Add versioned monitoring stack |
| `e2600b804a08c0a86b873230ab23e32c5a63a3a6` | `1a5a3c10a3b648b7d436ff8924070449093f7173` | 原作者保留 | Bump pytest-cov from 6.3.0 to 7.1.0 |
| `67e3206bf5bf24c609a79605e43eb2ea79ea7606` | `7242ff383ab648d6c9b1a59a117714f8c36316c2` | 迁移到 John Wang (@heee000) | Fix Prometheus alert timing test |
| `c9d73101e7318da5fed5e496ad9a78eb7fb09832` | `d65a67b0b2eaf23a3ba02f7d7933d9544986e5e0` | 迁移到 John Wang (@heee000) | Make provider integrations vendor neutral |
| `1fcc371c9b94117431760cc4d6b90b80cd653366` | `0d67d0091ca0471f40631e07d3830e2991effed8` | 迁移到 John Wang (@heee000) | Record vendor-neutral CI evidence |
| `58238f3fc694da4ab884ed3d0c158b9e49bc593e` | `d882187e95530cabb0cb0c1594b732504a075240` | 迁移到 John Wang (@heee000) | Formalize media provider contract v1 |
| `bceff28c84c723a176cf7d0f0860330fd3b3bc7f` | `fa3f627bb1e717bbb3984498c6b31c1f91fd27dc` | 迁移到 John Wang (@heee000) | Record media contract CI evidence |
| `0cf92c466409748de37ce47695cc0c7494a542f7` | `6e9e5965a6ca3ac8b3131165d8761c2c56273622` | 原作者保留 | Bump the frontend-minor-and-patch group across 1 directory with 10 updates |
| `8a79658952ebac63ed866c24b57940e3286c023b` | `c9da8df6c28312ccd3efb0364ce7e5d908ba428d` | 迁移到 John Wang (@heee000) | Harden enterprise media runtime and evidence |
| `285de6a32de15124d1f7a59b771b6972b086bce9` | `7751a010817897040893717427d36a3e9bd2232c` | 迁移到 John Wang (@heee000) | Record enterprise media phase evidence |
| `f23f17abf77d30ac88540f5dc4e33efa34e6dd36` | `8eaa22712e2f42ae895b1aa731dfb34d50ba791f` | 迁移到 John Wang (@heee000) | Record enterprise media CI signoff |
| `16b00a3de9f51e989b62dbeb0ae52b89f35af109` | `3637e441a7887b0c48b0dc207fd8fbef9f639b13` | 原作者保留 | Document repository maintainer and contributor |
| `79cd708e7bf9d6e24aad551aed4a73b78837c2d2` | `ad180bad4dff123b0ead6c3040c000f40d99bab1` | 原作者保留 | Normalize engineering change log newline |

## GitHub 缓存与恢复边界

GitHub Contributors/Insights 使用缓存，历史更新后可能约 24 小时才刷新。若需要恢复，不得无条件强推备份；必须先核对当前远端分支并对每个目标使用显式 lease。
