FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY corpus ./corpus
COPY db ./db
COPY evals ./evals
EXPOSE 8000
CMD ["uvicorn", "provenance.api:app", "--host", "0.0.0.0", "--port", "8000"]
