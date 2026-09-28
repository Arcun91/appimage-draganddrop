ARG BUILD_FROM=alpine:3.20
FROM $BUILD_FROM

# Installazione pacchetti essenziali a bassissimo consumo
RUN apk add --no-cache \
    python3 \
    py3-pip \
    jq \
    curl \
    && pip install --no-cache-dir --break-system-packages qrcode \
    && rm -rf /root/.cache

# Creazione directory applicazione e archiviazione temporanea
WORKDIR /app
RUN mkdir -p /app /data /share/drop_share_temp

# Copia file applicazione
COPY server.py /app/server.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Porte: 8099 per Home Assistant Ingress, 5001 per LAN Web UI & Download
EXPOSE 8099 5001

CMD ["/run.sh"]
