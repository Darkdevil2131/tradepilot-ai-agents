FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt constraints-docker.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints-docker.txt
COPY tradepilot ./tradepilot
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown appuser:appuser /app/data
ENTRYPOINT ["python", "-m", "tradepilot.container_entrypoint"]
EXPOSE 8000
CMD ["sh","-c","exec uvicorn tradepilot.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
