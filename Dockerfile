FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .
RUN python -m storage.database --migrate

EXPOSE 8787
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8787"]
