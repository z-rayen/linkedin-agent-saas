# syntax=docker/dockerfile:1
# Multi-stage build: the Vite build runs in Node, and the runtime image only holds nginx and the static files.
# nginx serves the single-page app and proxies /api to the API, so the browser sees one origin and needs no CORS.

ARG NODE_VERSION=26
ARG NGINX_VERSION=1.27-alpine

FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Build-time configuration. "/api" is the same-origin path that nginx proxies (see nginx.conf).
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:${NGINX_VERSION} AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
