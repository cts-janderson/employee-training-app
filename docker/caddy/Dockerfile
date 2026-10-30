# Caddy with the Cloudflare DNS module, for ACME DNS-01 certificates on a host the internet may not reach.
FROM caddy:2.10-builder AS build
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2.10
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
