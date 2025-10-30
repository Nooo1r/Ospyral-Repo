FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ^
build-essential gcc libpq-dev ^
libjpeg62-turbo-dev zlib1g-dev libwebp-dev ^
netcat-openbsd ^
&& rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requiremnts.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
RUN chmod +x /app/entrypoint.sh
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["/app/entrypoint.sh","web"]
