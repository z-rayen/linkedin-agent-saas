# syntax=docker/dockerfile:1
# Multi-stage build: the Vite build runs in Node, and the runtime image only holds nginx and the static files.
# nginx serves the single-page app and proxies /api to the API, so the browser sees one origin and needs no CORS.


# Base images are pinned by digest. nginx runs unprivileged: it listens on 8080 and never as root.
FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Build-time configuration. "/api" is the same-origin path that nginx proxies (see nginx.conf).
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0 AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
