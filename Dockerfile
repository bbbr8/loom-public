FROM python:3.13-slim
WORKDIR /app
RUN useradd --create-home --uid 10001 loom && mkdir /data && chown loom:loom /data
COPY --chown=loom:loom . /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SURD_USAGE=0 LOOM_HOST=0.0.0.0 PORT=10000 LOOM_DATA_DIR=/data
RUN pip install --no-cache-dir -r requirements-intake.txt
USER loom
EXPOSE 10000
CMD ["python", "-B", "server.py"]
