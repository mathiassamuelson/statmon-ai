FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY statmon_chat/ statmon_chat/
COPY static/ static/

EXPOSE 8443

CMD ["uvicorn", "statmon_chat.app:app", "--host", "0.0.0.0", "--port", "8443"]
