# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x     | Latest release only |

## Reporting a vulnerability

Open an issue at https://github.com/MarkBovee/ocpp-hub/issues. For anything that exposes credentials, contact the owner privately instead.

Do not include live credentials, IP addresses or charger serial numbers in reports.

## What to know

The OCPP server accepts connections on its configured port without authentication, like the OCPP servers of other Home Assistant integrations. Keep that port on a trusted network and do not forward it to the internet.
