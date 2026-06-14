FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined at BUILD time. Production passes NEXT_PUBLIC_API=1 so the
# app uses the real API by default (not the localStorage mock). API_BASE stays ""
# (same-origin: Caddy proxies /api and /auth to the FastAPI tier).
ARG NEXT_PUBLIC_API=0
ENV NEXT_PUBLIC_API=$NEXT_PUBLIC_API
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS run
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 3000
CMD ["npm", "start"]
