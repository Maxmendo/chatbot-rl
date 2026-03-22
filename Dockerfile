FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
ARG CACHEBUST=1
CMD ["python", "bot.py"]
```

Luego en Railway → Variables → agregás una variable nueva:
```
CACHEBUST
