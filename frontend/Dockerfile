# Multi-stage: build the static bundle with Node, serve it with nginx. The
# frontend is independently deployable — its own image, its own dependencies,
# no coupling to the backend container.

# ---- build ----
FROM node:20-alpine AS build
WORKDIR /app

# Install deps first (layer-cached unless package files change).
COPY package.json package-lock.json* ./
RUN npm ci

# Build. VITE_API_BASE_URL is baked at build time (Vite inlines env vars), so
# pass it as a build arg per environment.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
COPY . .
RUN npm run build

# ---- serve ----
FROM nginx:1.27-alpine AS serve
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
