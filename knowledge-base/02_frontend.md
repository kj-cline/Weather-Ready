# 02 — Frontend

React 18 + TypeScript, bundled with Vite. Single-workspace SPA with
conditional panel rendering — no React Router.

## Key files

- `frontend/src/App.tsx` — root container, panel switching, workspace state
- `frontend/src/api.ts` — fetch client for every `/api/*` endpoint
- `frontend/src/types.ts` — TS contracts that mirror backend serializers
- `frontend/src/components/WeatherGlyph.tsx` — risk glyphs for the forecast strip
- `frontend/src/components/StatusPip.tsx`, `GlyphLegend.tsx` — visual primitives
- `frontend/src/styles.css` — all styling; no CSS library

## Surfaces

- **Chat** — operator conversation, suggested replies, capture-note prompts
- **Forecast strip** — 7-day outlook with risk glyphs and driver badges
- **Service plan & actuals** — planned vs realized covers, nightly entry
- **Learning panels** — learning-agenda card, fact-memory drawer, reminders
- **Setup wizard** — onboarding flow that posts to `/api/onboarding/complete`

## Backend contract

State comes from:

- `GET /api/bootstrap` — initial workspace payload on app load
- `GET /api/operators/{id}/workspace` — post-mutation refresh
- `GET /api/operators/{id}/chat-history` — paginated messages

Mutations go through typed POSTs in `api.ts`. Response shapes are defined in
`api/serializers.py`; keep `frontend/src/types.ts` aligned when they change.

## Tests

- `frontend/src/App.test.tsx` — Vitest unit test
- `frontend/tests/smoke.spec.ts`, `frontend/tests/inspect.spec.ts` — Playwright E2E

→ see: 03 (api layer — endpoint contracts), 05 (orchestration — what drives forecast-strip data)
