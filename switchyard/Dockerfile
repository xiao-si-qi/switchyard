# Flask 版后端。
# 国内网络：pip / apk 都按「阿里云 → 中科大 → 清华」的顺序逐个尝试，
# 任一源可用就继续；全部失败才装编译工具链走官方源（几乎不会走到）。
FROM python:3.12-alpine

ARG PYPI_INDEXES="https://mirrors.aliyun.com/pypi/simple https://pypi.mirrors.ustc.edu.cn/simple https://pypi.tuna.tsinghua.edu.cn/simple"
ARG APK_MIRRORS="https://mirrors.aliyun.com/alpine https://mirrors.ustc.edu.cn/alpine https://mirrors.tuna.tsinghua.edu.cn/alpine"

COPY requirements.txt /tmp/requirements.txt

RUN set -eux; \
    if grep -q 'dl-cdn.alpinelinux.org' /etc/apk/repositories; then \
        alpine_ver="v$(cut -d. -f1,2 /etc/alpine-release)"; \
        : > /etc/apk/repositories; \
        for m in ${APK_MIRRORS}; do \
            printf '%s/%s/main\n%s/%s/community\n' "$m" "$alpine_ver" "$m" "$alpine_ver" >> /etc/apk/repositories; \
        done; \
        cat /etc/apk/repositories; \
    fi; \
    installed=0; \
    for idx in ${PYPI_INDEXES}; do \
        echo "==> pip 尝试源: $idx"; \
        if pip install --no-cache-dir --timeout 60 --retries 2 -i "$idx" -r /tmp/requirements.txt; then \
            installed=1; break; \
        fi; \
        echo "==> 该源失败，换下一个"; \
    done; \
    if [ "$installed" = "0" ]; then \
        echo "==> 所有镜像源失败，改用官方源 + 本地编译"; \
        apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev libffi-dev; \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
        apk del .build-deps; \
    fi; \
    rm -f /tmp/requirements.txt

COPY app /app
COPY config.yaml /app/addon_config.yaml
WORKDIR /app

EXPOSE 8099
CMD ["python3", "/app/main.py"]
