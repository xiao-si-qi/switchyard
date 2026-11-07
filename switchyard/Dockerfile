# Flask 版后端。Alpine + pip 都能用；国内网络慢时自动切清华镜像重试一次。
FROM python:3.12-alpine

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 || pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt

COPY app /app
COPY config.yaml /app/addon_config.yaml
WORKDIR /app

EXPOSE 8099
CMD ["python3", "/app/main.py"]
