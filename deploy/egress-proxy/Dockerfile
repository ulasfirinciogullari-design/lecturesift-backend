FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG LECTURESIFT_BUILD_REVISION=unknown
ARG LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=unknown

LABEL org.opencontainers.image.revision="${LECTURESIFT_BUILD_REVISION}" \
    io.lecturesift.supply-chain-lock-sha256="${LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256}"

ENV LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}" \
    LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256="${LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256}"

RUN set -eux; \
    rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \
    printf '%s\n' \
      'Types: deb' \
      'URIs: https://snapshot.debian.org/archive/debian/20260828T000000Z' \
      'Suites: bookworm bookworm-updates' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      'Check-Valid-Until: no' \
      '' \
      'Types: deb' \
      'URIs: https://snapshot.debian.org/archive/debian-security/20260828T000000Z' \
      'Suites: bookworm-security' \
      'Components: main' \
      'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
      'Check-Valid-Until: no' \
      > /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates squid squidclient; \
    rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf

USER proxy

EXPOSE 3128

CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
