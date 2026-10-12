FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends mkvtoolnix curl locales gosu \
 && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen \
 && rm -rf /var/lib/apt/lists/*
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 PYTHONUNBUFFERED=1 TZ=Europe/Stockholm

# The program. Everything this installation owns - settings, logs, backups -
# lives in the /config volume instead, and is created on first run.
COPY app/ /app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
WORKDIR /app
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "/app/webhook_listener.py"]

# Image metadata, last so that bumping the version does not invalidate the
# layers above it. The version lives in app/ui.py and nowhere else - this only
# stamps it onto the image, so docker inspect and the Container Manager UI
# report the same number as the page footer instead of whatever the image was
# tagged by hand. Builds that do not pass it say "dev" rather than claim a
# version they are not. To read it straight from the source:
#   docker build --build-arg VERSION="$(sed -n 's/^VERSION *= *"\([^"]*\)".*/\1/p' app/ui.py)" -t flaggarr:latest .
ARG VERSION=dev
LABEL org.opencontainers.image.title="flaggarr" \
      org.opencontainers.image.description="Sets the default audio and subtitle flags inside mkv files, and corrects the playback choice Plex saved for what the flag cannot reach." \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/ektoplasmaP/flaggarr"
