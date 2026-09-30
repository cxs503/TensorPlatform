# Build the active Vue application from source. Do not copy a pre-existing dist/
# directory: doing so can silently ship an older UI than the reviewed source.
FROM node:20-alpine AS build
WORKDIR /app

COPY frontend-vue/package.json frontend-vue/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend-vue/ ./
RUN npm run build

FROM nginx:alpine
ARG foundation_RELEASE_REVISION=unknown
LABEL maintainer="foundation New Energy <maintainers@example.invalid>" \
      version="2.2.0" \
      description="foundation AI Platform Vue Frontend Service" \
      org.opencontainers.image.revision="${foundation_RELEASE_REVISION}"

RUN sed -i 's|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apk/repositories \
    && apk add --no-cache curl tzdata
ENV TZ=Asia/Shanghai

COPY --from=build /app/dist/ /usr/share/nginx/html/
COPY frontend/nginx-default.conf /etc/nginx/conf.d/default.conf

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1
CMD ["nginx", "-g", "daemon off;"]
