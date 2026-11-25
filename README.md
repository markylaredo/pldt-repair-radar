# PLDT Repair Radar

Check the status of your PLDT Home repair requests from the terminal, without
opening the website.

> **Unofficial.** This project is not affiliated with or endorsed by PLDT. It
> calls the same endpoints as `my.pldthome.com`. They are undocumented and can
> change without notice. Use it only with your own account.

## Requirements

- Python 3.9 or newer (standard library only, nothing to install)
- A My PLDT Home account (email and password)

## Setup

```bash
cp .env.example .env
chmod 600 .env
```

Then put your login details in `.env`:

```ini
PLDT_EMAIL=you@example.com
PLDT_PASSWORD='your-password'
```

- Environment variables with the same names override `.env`.
- If neither is set and you run the script in a terminal, it asks for them.
- Use one `KEY=value` per line. Quotes around a value are optional. Comments must
  be on their own line, starting with `#`.

## Usage

### Readable output

```bash
python3 pldt_status.py
```

Example (made-up values):

```text
PLDT repair status (checked 2026-01-15T14:30:00+08:00)

  Ticket   00000000000
  Title    Service Repair - Landline and Internet
  Status   In Progress
  Created  2026-01-15T09:00:00Z
  Message  Our technician is scheduled to visit and assess the issue on-site.
```

With no open tickets, it prints `No ongoing repair requests.`

### JSON output

```bash
python3 pldt_status.py --json
```

```json
{
  "checked_at": "2026-01-15T14:30:00+08:00",
  "requests": [
    {
      "ticket": "00000000000",
      "title": "Service Repair - Landline and Internet",
      "status": "In Progress",
      "created": "2026-01-15T09:00:00Z",
      "message": "Our technician is scheduled to visit and assess the issue on-site."
    }
  ]
}
```

`requests` is an empty list when there are no open tickets. The output does not
include your account number, name or address.

### Fields

| Field     | PLDT field           | Meaning                      |
|-----------|----------------------|------------------------------|
| `ticket`  | `prom_number`        | Ticket number                |
| `title`   | `service_title`      | Type of repair               |
| `status`  | `milestone`          | Current progress             |
| `created` | `creation_date`      | When the ticket was opened   |
| `message` | `messagebyworkgroup` | Latest note from PLDT's team |

`created` is shown exactly as PLDT sends it. Although it ends in `Z` (UTC), it
appears to be Philippine time. Confirm this before converting it or showing it to
others.

### Exit codes

| Code  | Meaning                                                   |
|-------|-----------------------------------------------------------|
| `0`   | Success, with or without open tickets                     |
| `1`   | Error. The message goes to stderr and stdout stays empty  |
| `130` | Cancelled with Ctrl+C                                     |

## How it works

Each run logs in from scratch and makes these requests to
`https://my.pldthome.com`:

| # | Request | Purpose |
|---|---------|---------|
| 1 | `GET /pldtssoweb/account/authorize` | Opens the login form and reads its anti-forgery token |
| 2 | `POST /pldtssoweb/Account/Login` | Signs in and gets an authorization code |
| 3 | `POST /revampExpLayer/token` | Exchanges the code for an access token |
| 4 | `GET /uhwrv/PLDTHANDLER_SSO/pldtssoapi/accounts` | Finds your default account |
| 5 | `GET /revampExpLayer/billing/planDetailsExp` | Finds the internet service number |
| 6 | `GET /revampExpLayer/help/getRepairRequestsExp` | Gets the ongoing repair requests |

Details that matter if you change the code:

- API calls send an `X-Trace-Id` header, as the web app does. Without it, the
  token endpoint fails with HTTP 500
  (`Cannot read properties of undefined (reading 'split')`).
- The accounts request (4) uses `Authorization: Bearer <token>`. The others (5
  and 6) send the token without `Bearer`.
- If the address returned by the login has no code, the script follows it, as a
  browser would.

### Limits

- Checks only your default account, or the first one if none is marked default.
- Checks only the first internet line on that account.
- Shows ongoing requests only, not past ones.

## Troubleshooting

| Error message | What to do |
|---------------|------------|
| `set PLDT_EMAIL and PLDT_PASSWORD` | Create `.env`, or set the environment variables |
| `login failed: ...` | Check your email and password by signing in on the website |
| `login accepted but no authorization code` | Sign in with a browser and complete any pending prompt |
| `login form token not found` | The login page changed, so the script needs an update |
| `HTTP <code> from <path>` | PLDT rejected the request or has a problem. Try again later |
| `cannot reach <path>` | Check your internet connection |
| `<path> did not return JSON` | The request was blocked, or the site changed |
| `no PLDT account is linked to this login` | Link your account on the website first |
| `no internet service found on this account` | The account has no line the script recognizes |
| `unexpected repair response` / `unexpected shape` | PLDT changed its data format |

An unrecognized response is reported as an error, never as "no repairs".

## Security

- `.env` holds your password in plain text. Keep it readable only by you
  (`chmod 600 .env`). Git ignores it.
- The script keeps session cookies and tokens in memory only.
- Never share browser network captures (`.har` files). They contain your password
  and session cookies. Git ignores `*.har`.
- Avoid running it very often. Every run is a full login, which PLDT might limit.