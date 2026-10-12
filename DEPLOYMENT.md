# Deploy QArena on Render

QArena runs expeditions in a background thread and streams progress from the
same Python process. Deploy the complete app as a persistent web service rather
than as separate serverless function invocations.

1. Connect this repository to Render and create a Blueprint from `render.yaml`.
2. Set `GROQ_API_KEY` when prompted. The model defaults to
   `groq/openai/gpt-oss-120b`.
3. Deploy the service and open its `onrender.com` URL.

The Docker image installs the Chromium browser required by Playwright. Render's
free service can spin down after inactivity; run data is temporary unless
persistent storage is configured.
