# BingOps 后端容器镜像
# 构建：docker build -t bingops-backend:latest .
# 运行：docker run --env-file .env -p 8000:8000 bingops-backend:latest

# ── 构建阶段：安装依赖 ────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 先拷依赖清单，利用层缓存（依赖不变则不重装）
COPY pyproject.toml ./
COPY bingops ./bingops

# 安装项目本体及全部运行时依赖到 /install 前缀
RUN pip install --prefix=/install .

# ── 运行阶段：最小镜像 ────────────────────────────────────────────────────────
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 非 root 运行
RUN useradd --create-home --uid 10001 bingops

# 只带已安装的依赖和源码，不带 venv/.env/开发文件
COPY --from=builder /install /usr/local
COPY --chown=bingops:bingops bingops /app/bingops

WORKDIR /app
USER bingops

EXPOSE 8000

# 健康检查：打 /api/health（镜像内无 curl，用标准库探测）
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "bingops.main:app", "--host", "0.0.0.0", "--port", "8000"]
