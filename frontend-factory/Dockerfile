# TensorPlatform 科研运行台前端：源码构建，绝不复制预生成 dist/。
FROM node:20-alpine AS build
WORKDIR /app

RUN sed -i 's|dl-cdn.alpinelinux.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apk/repositories
COPY frontend-console/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend-console/ ./
RUN npm run build

FROM nginx:alpine
ARG foundation_RELEASE_REVISION=unknown
LABEL maintainers="TensorPlatform" \
      version="1.0.0" \
      description="TensorPlatform research console frontend" \
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
