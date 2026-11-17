# NEWSWIRE web UI (Next.js standalone output).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Rewrites are resolved at build time; point them at the api service.
ARG NEWSWIRE_BACKEND_URL=http://api:8000
ENV NEWSWIRE_BACKEND_URL=$NEWSWIRE_BACKEND_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -S newswire && adduser -S newswire -G newswire
COPY --from=build --chown=newswire:newswire /app/.next/standalone ./
COPY --from=build --chown=newswire:newswire /app/.next/static ./.next/static
COPY --from=build --chown=newswire:newswire /app/public ./public
USER newswire
EXPOSE 3000
CMD ["node", "server.js"]
