# ContentFlow Web

ContentFlow 的运营工作台，基于 Next.js API 与 vinext/Vite 构建。

## 本地开发

需要 Node.js 22.13 或更高版本。

```bash
npm ci
npm run dev:local
```

本地工作台固定使用 `http://localhost:3001`，默认连接
`http://localhost:8000/api/v1`。如需修改后端地址：

```dotenv
NEXT_PUBLIC_CONTENTFLOW_API_BASE=http://localhost:8000/api/v1
```

## 验证

```bash
npm run lint
npm run build
npm test
```

完整的系统说明、部署方式和平台能力边界见仓库根目录的 `README.md` 与 `docs/`。
