FROM python:3.12-slim

# Unbuffered so the activation code and progress output show up immediately when
# running interactively; without it the first-run login prompt looks like a hang.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CONFIG_HOME=/config

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kobo-book-downloader/ ./kobo-book-downloader/

# The sources use flat imports (from Commands import Commands), so the package
# directory itself has to be on the path rather than the repository root.
ENV PYTHONPATH=/app/kobo-book-downloader

# Settings.py only honours XDG_CONFIG_HOME if the directory already exists,
# otherwise it silently falls back to ~/.config and the token is lost on exit.
RUN mkdir -p /config /books

VOLUME ["/config", "/books"]
WORKDIR /books

ENTRYPOINT ["python", "/app/kobo-book-downloader/__main__.py"]
CMD ["--help"]
