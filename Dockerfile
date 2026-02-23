FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir -e .

RUN useradd --create-home botuser
USER botuser

CMD ["futuresbot", "run", "--loop"]
