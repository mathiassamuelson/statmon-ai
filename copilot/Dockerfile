FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY copilot/ copilot/
COPY static/ static/

EXPOSE 8443

CMD ["uvicorn", "copilot.app:app", "--host", "0.0.0.0", "--port", "8443"]
