FROM debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

ARG LECTURESIFT_BUILD_REVISION=unknown
ARG LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256=unknown

LABEL org.opencontainers.image.revision="${LECTURESIFT_BUILD_REVISION}" \
    io.lecturesift.supply-chain-lock-sha256="${LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256}"

ENV LECTURESIFT_BUILD_REVISION="${LECTURESIFT_BUILD_REVISION}" \
    LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256="${LECTURESIFT_SUPPLY_CHAIN_LOCK_SHA256}"

COPY --from=python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

RUN set -eux; \
    test -s /etc/ssl/certs/ca-certificates.crt; \
    test ! -L /etc/ssl/certs/ca-certificates.crt; \
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
    apt-get \
      -o Acquire::https::CAInfo=/etc/ssl/certs/ca-certificates.crt \
      -o Acquire::https::Verify-Peer=true \
      -o Acquire::https::Verify-Host=true \
      update --error-on=any; \
    apt-get \
      -o Acquire::https::CAInfo=/etc/ssl/certs/ca-certificates.crt \
      -o Acquire::https::Verify-Peer=true \
      -o Acquire::https::Verify-Host=true \
      install -y --no-install-recommends ca-certificates squid squidclient; \
    rm -rf /var/lib/apt/lists/*

COPY squid.conf /etc/squid/squid.conf

USER proxy

EXPOSE 3128

CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
