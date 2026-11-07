# Flask 版后端。
# Alpine；pip 装依赖时如果缺 wheel（aarch64 上的 MarkupSafe 之类）会现场编译，
# 所以临时装上编译工具链，装完再删掉，保持镜像干净。
FROM python:3.12-alpine

COPY requirements.txt /tmp/requirements.txt

RUN apk add --no-cache --virtual .build-deps gcc musl-dev python3-dev libffi-dev \
 && (pip install --no-cache-dir -r /tmp/requirements.txt \
     || pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt) \
 && apk del .build-deps \
 && rm -f /tmp/requirements.txt

COPY app /app
COPY config.yaml /app/addon_config.yaml
WORKDIR /app

EXPOSE 8099
CMD ["python3", "/app/main.py"]
