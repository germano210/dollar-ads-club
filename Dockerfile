FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DAC_DATA=/data U2NET_HOME=/data/modelos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
RUN useradd -u 1000 -m dac && mkdir -p /data && chown dac /data
USER dac
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/saude')"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
