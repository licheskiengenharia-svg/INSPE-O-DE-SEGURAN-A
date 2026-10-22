FROM python:3.12-slim
WORKDIR /app
COPY servidor/requirements.txt servidor/requirements.txt
RUN pip install --no-cache-dir -r servidor/requirements.txt
COPY index.html index.html
COPY servidor servidor
WORKDIR /app/servidor
ENV ISE_BANCO=/dados/ise.sqlite3
EXPOSE 8000
# um único processo (o agendador opcional do backup roda dentro dele); o Render informa a porta em PORT
CMD ["sh", "-c", "exec gunicorn -w 1 --threads 8 --timeout 900 -b 0.0.0.0:${PORT:-8000} app:app"]
