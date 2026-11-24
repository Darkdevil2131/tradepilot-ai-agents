# Security Model

- The legacy API key is an administrator credential. Prefer separate viewer,
  analyst, operator, approver and administrator keys in production.
- Never put credentials in source, request bodies, logs or client-side code.
- Use TLS at the edge and rotate any credential exposed in chat or screenshots.
- Uploads are restricted by MIME type and 10 MiB size, parsed in memory, and
  never executed. Isolate document processing in a sandbox for hostile files.
- OCR, visible stamps and PDF signature fields do not prove authenticity.
- External email is opt-in. Booking and ERP writes fail closed because no
  authorized external adapter is included.
- Run dependency, container and secret scanning in CI. Before handling real
  trade data, commission an authorized penetration test covering authentication,
  broken object authorization, file-parser attacks, injection, SSRF, rate-limit
  bypass, workflow-state manipulation, audit integrity and backup exposure.

Report vulnerabilities privately to the repository owner. Do not include live
credentials, personal data or exploitation details in a public issue.
