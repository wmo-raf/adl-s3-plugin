# ADL S3 Plugin

Pushes observations **out of** an [ADL](https://github.com/wmo-raf/adl)
instance as CSV objects into an **S3-compatible bucket** — Amazon S3, a
self-hosted MinIO, or any service speaking the S3 API (DigitalOcean Spaces,
Backblaze B2, Wasabi). It is a *dispatch* plugin: it collects nothing, and
adds two dispatch channel types, **S3 Upload** and **MinIO Upload**, to the
Dispatch Channels chooser. Each channel writes either one object per
observation or a daily object per station that later runs append to.

**Operator guide:** [docs/guide.md](docs/guide.md) — prerequisites, IAM
permissions, every channel field, the CSV layout, write modes, parameter
mappings, diagnostics and troubleshooting. The guide is also published on the
central ADL documentation site.

## Development setup

The plugin runs inside the ADL core image. Build the `adl:latest` image from
the [ADL core repository](https://github.com/wmo-raf/adl) first, then:

```bash
git clone https://github.com/wmo-raf/adl-s3-plugin.git
cd adl-s3-plugin
cp .env.sample .env        # set PLUGIN_BUILD_UID=$(id -u), PLUGIN_BUILD_GID=$(id -g), ADL_DB_PASSWORD
docker compose build
docker compose up
docker compose exec adl adl createsuperuser
```

The admin is served on `PORT` (default 8080). The plugin source is
bind-mounted, so code changes reload the dev server. If the build fails with
`pull access denied` for `adl:latest`, prefix the build with
`DOCKER_BUILDKIT=0`.

Tests are Django-runner tests under
`plugins/adl_s3_plugin/src/adl_s3_plugin/tests/`; the CSV and append-merge
tests are pure functions and need no database. Lint and format from
`plugins/adl_s3_plugin/` with `make lint` and `make format`. See
[CONTRIBUTING.md](CONTRIBUTING.md) — a change to any channel field must update
the guide in the same PR.
