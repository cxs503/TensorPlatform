# Alertmanager notification bridge

This directory contains the persistent Alertmanager routing configuration for
the foundation platform. It sends firing and resolved events to the backend audit
receiver and never embeds an external notification credential in source code.

## Security and persistence

- The webhook bearer token is read from
  `secrets/alertmanager_webhook_token`, which is ignored by Git and must be mode
  `0600`.
- Alertmanager is bound to `127.0.0.1` and shares only the internal platform
  network with Prometheus and the backend.
- Runtime state is retained in the `alertmanager_data` volume.
- Duplicate firing deliveries update the active database event; a resolved
  delivery closes that same event.

## Enable without pulling an image

The service is behind the `observability-notifications` profile because the
host may not yet have the pinned image. Never omit `--pull never` in production.

```bash
docker image inspect prom/alertmanager:v0.27.0
docker compose --profile observability-notifications up -d --no-deps --pull never alertmanager
docker compose --profile observability-notifications ps alertmanager
docker kill --signal HUP kp-prometheus
```

If the first command reports that the image is absent, stop there and obtain an
approved, vulnerability-scanned image through the normal offline image process.

## Rollback

Stop only Alertmanager and keep its volume so notification state is recoverable:

```bash
docker compose --profile observability-notifications stop alertmanager
```

Before starting Prometheus without Alertmanager for an extended maintenance
window, remove the `alerting` block from `prometheus/prometheus.yml`, validate it
with `promtool`, then reload Prometheus. This does not affect rule evaluation or
stored metrics.
