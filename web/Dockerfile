ARG NODE_IMAGE=node:22-alpine
FROM ${NODE_IMAGE} AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_CONTENTFLOW_API_BASE=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_CONTENTFLOW_API_BASE=$NEXT_PUBLIC_CONTENTFLOW_API_BASE
RUN npx next build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup -S contentflow && adduser -S contentflow -G contentflow
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
USER contentflow
EXPOSE 3000
CMD ["node", "server.js"]
