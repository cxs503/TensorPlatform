# 开放数字底座 - 后端Dockerfile (生产级)
# 多阶段构建优化镜像大小

# ==================== 构建阶段 ====================
FROM python:3.11-slim as builder

WORKDIR /build

# 安装构建依赖 (apt 源切换为清华镜像，国内构建加速)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY backend/requirements.txt .

# 创建虚拟环境并安装依赖 (CPU torch 节省 ~4GB)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip && \
    pip install --no-cache-dir torch==2.1.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple jupyterlab

# 卸载 GPU 相关大包(若未安装则跳过); 独立 RUN, 其失败不影响上面的安装结果。
# 注意: 之前这步与上面的 && 链合并在一个 RUN 里、末尾带 || true, 因 shell 优先级
# (A && B && C || true) 会掩盖整条链的失败 —— pip 安装失败时 build 仍 exit 0,
# 产出缺包的残缺镜像(曾因此缺 uvicorn 导致容器 crash-loop)。拆开后安装失败会正常报错。
RUN pip uninstall -y nvidia-cublas-cu12 nvidia-cuda-cupti-cu12 nvidia-cuda-nvrtc-cu12 \
        nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 \
        nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-nccl-cu12 nvidia-nvjitlink-cu12 \
        nvidia-nvtx-cu12 triton 2>/dev/null || true

# ==================== 运行阶段 ====================
FROM python:3.11-slim as runtime

# 标签信息
LABEL maintainer="foundation New Energy <maintainers@example.invalid>"
LABEL version="2.2.0"
LABEL description="foundation AI Platform Backend Service"
ARG foundation_GIT_REVISION=unknown
LABEL org.opencontainers.image.revision=${foundation_GIT_REVISION}

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# 创建非root用户（生产环境安全）
RUN groupadd -r foundation && useradd -r -g foundation foundation

WORKDIR /app

# 安装运行时依赖 (apt 源切换为清华镜像，国内构建加速)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libgomp1 \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/open-foundation \
    && chown -R foundation:foundation /var/log/open-foundation

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制应用代码
COPY --chown=foundation:foundation backend/ /app/

# 创建必要目录
RUN mkdir -p /app/uploads /app/.keys \
    && chown -R foundation:foundation /app

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8001/api/v1/health || exit 1

# 创建用户home目录（JupyterLab运行时需要）
RUN mkdir -p /home/foundation/.local/share/jupyter/runtime /tmp/jupyter_runtime     && chown -R foundation:foundation /home/foundation /tmp/jupyter_runtime

# 切换到非root用户
USER foundation

# 启动命令
# JupyterLab已在main.py启动事件中自动拉起
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1", "--timeout-keep-alive", "65", "--limit-concurrency", "50"]
