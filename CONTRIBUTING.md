# Contributing

## Documentation

`docs/guide.md` is the operator guide for this plugin. It is the single place
configuration is documented — the README deliberately stays short — and it is
aggregated into the central ADL documentation site.

**A pull request that touches a dispatch channel field (adding, removing or
renaming one, changing a default or a validation rule), the parameter-mapping
behaviour, the CSV layout or file naming, or any admin surface this plugin
adds must update `docs/guide.md` in the same PR.**

If the change is visible on screen, also update `docs/screenshots.yml` and
regenerate the images with the capture harness in the `adl` repo:

```bash
# from a checkout of wmo-raf/adl, with Docker running
scripts/capture-plugin-docs.sh ../adl-plugins/adl-s3-plugin
```

`--only <entry>` re-shoots a single entry, which is what to use for a crop fix:
a full run re-renders every image and the diagnostic shots carry live
timestamps, so fixing one crop otherwise lands as a diff in unrelated images.

The demo instance is seeded from `docs/screenshots/fixture.json`, which seeds
an FTP ingestion connection for data plus the two dispatch channels, and
`docs/screenshots/compose.mock.yml`, which runs a MinIO container as the
destination. A real dispatch cycle runs before capture, so the station-links
page shows last-sent times that were actually written.

Images are code: never hand-edit a PNG in `docs/images/`; change the manifest
entry and regenerate. Keep images free of text (only numbered badges), since
the docs are translated.

Messages the plugin shows to operators — *Test connection* results, dispatch
task-log lines — are listed verbatim-shaped in the guide's feedback catalogue;
add a row when you add or change one.

## Development

See the README for the dev stack. Run the tests with the Django runner, lint
with `make lint` and format with `make format` inside `plugins/adl_s3_plugin/`.

## Releases

Tag releases bare (`0.3.0`, never `v0.3.0`): `plugins.toml` entries pin the tag
verbatim. Use `gh release create 0.3.0`.
