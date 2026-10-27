FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml README.md LICENSE requirements.txt ./
COPY src ./src
COPY app ./app
RUN pip install --no-cache-dir . -r requirements.txt
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
