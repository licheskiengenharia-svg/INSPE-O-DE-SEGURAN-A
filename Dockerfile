FROM python:3.12-slim
WORKDIR /app
COPY servidor/requirements.txt servidor/requirements.txt
RUN pip install --no-cache-dir -r servidor/requirements.txt
COPY index.html index.html
COPY servidor servidor
WORKDIR /app/servidor
ENV ISE_BANCO=/dados/ise.sqlite3
VOLUME /dados
EXPOSE 8000
# um único processo: o agendador do backup das 23h roda dentro dele
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8000", "app:app"]
