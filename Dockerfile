FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spam_filter.py .

EXPOSE 8484

CMD ["uvicorn", "spam_filter:app", "--host", "0.0.0.0", "--port", "8484"]
