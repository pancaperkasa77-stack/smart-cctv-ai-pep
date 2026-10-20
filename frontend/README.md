# Smart CCTV AI — .NET 4.8 Mockup

Visual mockup of the Smart CCTV AI dashboard (Pertamina EP), reimplemented in
ASP.NET MVC5 (.NET Framework 4.5 target, runs on Mono + xsp4 on Linux) to
match the look of the original web frontend.

**This is a visual mockup only** — no real database, no real Keycloak/OIDC,
no real camera/AI inference. Login and admin user management are real
(session-based), but backed entirely by an in-memory dummy dataset that
resets on every restart.

## Demo credentials

| Username | Password | Role |
|---|---|---|
| `admin` | `busDev123!` | Admin |
| `supervisor1` | `busDev123!` | Supervisor |
| `operator1` | `busDev123!` | Operator |
| `operator2` | `busDev123!` | Operator (disabled) |

## Run locally

```bash
docker compose build
docker compose up -d
```

Serves on `http://127.0.0.1:8086`.

## Stack

- ASP.NET MVC5, Razor views, .NET Framework 4.5 target
- Mono 5.18 + xsp4 (Debian buster base image)
- Session-based dummy auth (no FormsAuthentication/OIDC)
- Docker multi-stage build (mono-devel build stage → mono-xsp4 runtime stage)
