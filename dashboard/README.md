# FreshGate dashboard

The dashboard is a React, TypeScript, Vite, and Tailwind CSS application. Its production build is served by FastAPI from `dashboard/dist` at `/`; bundled JS, CSS, and fonts are served from `/static/`.

For local UI development, start FreshGate at `http://127.0.0.1:8000`, then run:

```sh
cd dashboard
npm ci
npm run dev
```

The Vite server proxies API requests to the local FastAPI service. For a static production bundle, run `npm run build`. The Dockerfile builds this bundle in its Node stage and copies only the generated assets into the Python image.

Provider credentials entered on Verify and the admin key entered on Audit log are request-scoped, held in component memory, and cleared when the request finishes. The UI does not write them to browser storage.
