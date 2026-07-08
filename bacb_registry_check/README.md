# BACB Registry Check

Standalone BACB Certificant Registry checker for local Skyvern experiments. It
uses a persistent Chrome CDP session so a human can complete BACB/Cloudflare
verification once, then the service fills the registry form deterministically,
expands the matching row, parses the fields, and writes a screenshot.

## Start Chrome for Manual Verification

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/skyvern-bacb-chrome \
  --no-first-run \
  --no-default-browser-check
```

In that Chrome window, open `https://services.bacb.com/o.php?page=101135` and
complete the human verification. Leave the tab open.

## Start the Docker Service

```bash
docker compose up -d bacb-registry-check
```

Check health:

```bash
curl http://127.0.0.1:8765/health
```

Run through HTTP:

```bash
curl -X POST http://127.0.0.1:8765/check \
  -H "content-type: application/json" \
  -d '{"state":"NM","name":"Jennelle Otero","credential":"RBT"}'
```

Run a one-shot command in Docker:

```bash
docker compose run --rm bacb-registry-check \
  check --state NM --name "Jennelle Otero" --credential RBT
```

The JSON response includes parsed registry fields plus `screenshot_url`. The
image is stored under `artifacts/bacb-registry/` and can be downloaded from the
returned URL while the Docker service is running.

If the response status is `needs_human_verification`, go back to the Chrome tab,
complete the Cloudflare check manually, and rerun the command.

