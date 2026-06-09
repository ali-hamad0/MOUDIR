# Security Policy

## Scope

This policy covers the Modir codebase: the FastAPI backend, React frontend,
LangGraph agents, and the Docker Compose infrastructure configuration.

**In scope:**
- Authentication and authorisation flaws (JWT handling, tenant isolation bypass)
- Injection vulnerabilities (SQL, prompt, command)
- Secrets exposure (API keys, tokens in logs or responses)
- Cross-tenant data leakage (violation of The Wall)
- Insecure direct object references in API endpoints

**Out of scope:**
- Third-party services (Gemini, LangSmith, HashiCorp Vault, MinIO upstream)
- Vulnerabilities requiring physical access to the host machine
- Denial-of-service via resource exhaustion (rate limiting is already in place)
- Issues in dependencies that have an available upstream fix not yet applied here

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via one of these channels:

- **GitHub private advisory:** Repository → Security → Advisories → New draft advisory
- **Email:** ali.mufid.hamad2222@gmail.com
  Subject line: `[MODIR SECURITY] <brief description>`

Include:
- A description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept code or curl commands are helpful)
- The version / commit hash you tested against
- Any suggested remediation (optional but appreciated)

## Response timeline

| Event | Target |
|-------|--------|
| Acknowledgement | Within 72 hours of receipt |
| Initial triage | Within 7 days |
| Patch for Critical / High | Within 14 days of confirmation |
| Patch for Medium / Low | Within 30 days of confirmation |
| Public disclosure | Coordinated with reporter after patch is released |

## Notes

Modir is currently a development / portfolio project. There is no bug bounty
programme. Responsible disclosure is appreciated and reporters will be credited
in the release notes (with their permission).
