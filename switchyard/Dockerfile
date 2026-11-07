# Flask 版后端。
# 国内网络优化：
#   1. apk / pip 都优先走清华镜像（官方源作为兜底）；
#   2. 只有在镜像上没有 wheel、确实需要编译时，才临时装 gcc/musl-dev。
FROM python:3.12-alpine

ARG APK_MIRROR=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt /tmp/requirements.txt

RUN set -eux; \
    if grep -q 'dl-cdn.alpinelinux.org' /etc/apk/repositories; then \
        sed -i "s#dl-cdn.alpinelinux.org#${APK_MIRROR}#g" /etc/apk/repositories; \
    fi; \
    pip install --no-cache-dir --timeout 60 -i "${PIP_INDEX}" -r /tmp/requirements.txt \
    || ( \
        echo "镜像源失败，改用官方源并要求本地编译"; \
        apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev libffi-dev; \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
        apk del .build-deps; \
    ); \
    rm -f /tmp/requirements.txt

COPY app /app
COPY config.yaml /app/addon_config.yaml
WORKDIR /app

EXPOSE 8099
CMD ["python3", "/app/main.py"]
