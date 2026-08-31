FROM debian:bookworm-slim

ARG LECTURESIFT_BUILD_REVISION=unknown

LABEL org.opencontainers.image.revision="${LECTURESIFT_BUILD_REVISION}"

ENV LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates squid squidclient \
    && rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf

USER proxy

EXPOSE 3128

CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
