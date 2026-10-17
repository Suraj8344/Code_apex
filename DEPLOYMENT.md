# Deploy QArena on Render

QArena submits expeditions to a Redis-backed RQ queue. A separate persistent
worker runs Playwright and streams durable progress through Redis.

1. Create a Redis instance and an S3-compatible bucket (AWS S3 or another
   provider). Set `REDIS_URL`, `QA_S3_BUCKET`, and the S3 credentials on both
   the `qarena` web service and `qarena-browser-worker`.
2. Connect this repository to Render and create a Blueprint from `render.yaml`.
   The Blueprint starts the FastAPI web service and a separate RQ browser worker.
3. Set `GROQ_API_KEY` when prompted. The model defaults to
   `groq/openai/gpt-oss-120b`.
4. Deploy both services and open the web service's `onrender.com` URL.

The Docker image installs the Chromium browser required by Playwright. The web
service stores shared project data and run events in Redis; screenshots, traces,
reports, and exported tests are stored in the configured S3 bucket.
The queue mode requires Redis and S3-compatible storage. Redis stores run status
and resumable events; object storage keeps screenshots, traces, reports, and
the zipped evidence pack. Set `QUEUE_MODE=local` to use the in-process worker
for local development.

For AWS S3, provide `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and
`AWS_REGION`; for an S3-compatible provider, also set `QA_S3_ENDPOINT_URL` and
`QA_S3_REGION`. Use credentials restricted to this bucket and prefix.

## Vercel

The Vercel function handles short web requests while the separate persistent
worker runs the long-lived Playwright/Chromium job. Configure `QUEUE_MODE=redis`,
`REDIS_URL`, and
`QA_S3_BUCKET` on Vercel and deploy the separate browser worker with the same
settings. The web function only submits work and streams events from Redis; the
worker performs the Playwright run and uploads evidence to object storage.

## Runtime sizing and responsiveness

- Install Chromium in the deployment image with `playwright install --with-deps chromium`.
- Keep test-case, crawl, and replay limits explicit; shorten them for a live demo.
- QArena reuses one browser process during a run and records the total run budget.
- Persist run files in S3-compatible object storage outside the ephemeral
  application filesystem.
- Set a worker concurrency limit before running multiple browser jobs at once;
  each Chromium process consumes significant memory.
