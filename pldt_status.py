#!/usr/bin/env python3
"""Show the ongoing PLDT Home repair requests for your own account.

    python3 pldt_status.py          # readable text
    python3 pldt_status.py --json   # for other programs (no account details)

Credentials come from PLDT_EMAIL / PLDT_PASSWORD, from a .env file beside this
script, or from a prompt.
"""
import argparse
import getpass
import http.cookiejar
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE = "https://my.pldthome.com"
CHANNEL = "MyHomeRevamp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:160.0) "
    "Gecko/20100101 Firefox/160.0"
)
DATA_SERVICE_TYPE = 112  # "ServiceTypeCode" of the internet line (113 is voice)


class PLDTError(Exception):
    pass


def auth_code(url):
    return parse_qs(urlsplit(url).query).get("code", [None])[0]


def redact(text):
    """Shorten a server reply for an error message, hiding anything token-like."""
    return re.sub(r"[A-Za-z0-9_+/=%.-]{24,}", "<redacted>", text.strip())[:200]


class PLDT:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.jar))
        self.ssoid = self.token = self.last_url = None

    def request(self, path, params=None, data=None, headers=None):
        url = urljoin(BASE, path) + ("?" + urlencode(params) if params else "")
        # Messages name only the path, so codes in a query never reach the screen.
        name = urlsplit(urljoin(BASE, path)).path
        all_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if name.startswith(("/revampExpLayer/", "/uhwrv/")):
            # Without X-Trace-Id the API crashes with HTTP 500.
            origin, session = (
                ("#/my-home", self.token[-5:]) if self.token else ("", '"N/A"')
            )
            all_headers["Accept"] = "application/json"
            all_headers["X-Trace-Id"] = (
                f"{uuid.uuid4()} | Origin:{origin} | Session ID: {session}"
            )
        all_headers.update(headers or {})
        try:
            req = Request(url, data=data, headers=all_headers)
            with self.opener.open(req, timeout=30) as resp:
                self.last_url = resp.geturl()
                return resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            reply = redact(e.read().decode("utf-8", "replace"))
            raise PLDTError(f"HTTP {e.code} from {name}: {reply}") from None
        except (URLError, TimeoutError) as e:
            raise PLDTError(f"cannot reach {name}: {getattr(e, 'reason', e)}") from None

    def request_json(self, path, **kwargs):
        body = self.request(path, **kwargs)
        try:
            return json.loads(body)
        except ValueError:
            raise PLDTError(
                f"{path} did not return JSON (request blocked, or the site changed)"
            ) from None

    def authorize(self):
        return self.request(
            "/pldtssoweb/account/authorize",
            params={
                "client_id": "12059",
                "response_type": "code",
                "scope": "openid",
                "state": "randomid",
                "redirect_uri": BASE + "/",
            },
        )

    def code_after_login(self, return_url):
        """Like the browser, go to returnURL and look for ?code= where it ends up."""
        target = urljoin(BASE, return_url)
        if not auth_code(target) and urlsplit(target).netloc == urlsplit(BASE).netloc:
            self.request(target)
            target = self.last_url
            if not auth_code(target):
                # The web app asks authorize again when it has no code.
                self.authorize()
                target = self.last_url
        code = auth_code(target)
        if not code:
            where = urlsplit(target)
            raise PLDTError(
                "login accepted but no authorization code; PLDT sent us to "
                f"{where.netloc}{where.path} (log in with a browser and check "
                "for a pending prompt)"
            )
        return code

    def login(self, email, password):
        # The authorize URL sets a cookie and redirects to the login form.
        page = self.authorize()
        form_token = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page
        )
        if not form_token:
            raise PLDTError("login form token not found (the login page changed)")

        result = self.request_json(
            "/pldtssoweb/Account/Login",
            data=urlencode(
                {
                    "Email": email,
                    "Password": password,
                    "summary": "",
                    "RememberUser": "true",
                    "RedirectURL": BASE + "/",
                    "__RequestVerificationToken": form_token.group(1),
                }
            ).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE,
                "Referer": BASE + "/pldtssoweb/Account/Login",
            },
        )
        if not result.get("succeeded"):
            reason = result.get("resultSpiel") or f"code {result.get('code')}"
            raise PLDTError(f"login failed: {reason}")

        code = self.code_after_login(result.get("returnURL") or "/")
        session = self.request_json(
            "/revampExpLayer/token",
            data=b"",
            headers={
                "Content-Type": "application/json",
                "code": code,
                "redirect_uri": BASE + "/",
                "Origin": BASE,
            },
        )
        self.ssoid, self.token = session["ssoid"], session["accessToken"]
        # The web app stores these two as cookies after login.
        self.set_cookie("SSOID", self.ssoid)
        self.set_cookie("SSO_accesstoken", self.token)

    def set_cookie(self, name, value):
        host = urlsplit(BASE).hostname
        self.jar.set_cookie(
            http.cookiejar.Cookie(
                0, name, value, None, False, host, False, False, "/", True,
                False, None, True, None, None, {},
            )
        )

    def api(self, path, params, auth=None):
        return self.request_json(
            path,
            params=params,
            headers={
                "Authorization": auth or self.token,
                "Content-Type": "application/json",
                "Referer": BASE + "/",
            },
        )

    def default_account(self):
        data = self.api(
            "/uhwrv/PLDTHANDLER_SSO/pldtssoapi/accounts",
            {"SSOID": self.ssoid},
            auth=f"Bearer {self.token}",  # only the SSO API wants "Bearer"
        )
        accounts = data["result"]["accounts"]
        if not accounts:
            raise PLDTError("no PLDT account is linked to this login")
        chosen = next((a for a in accounts if a.get("isDefault")), accounts[0])
        return chosen["accountNumber"]

    def data_service_id(self, account):
        data = self.api(
            "/revampExpLayer/billing/planDetailsExp",
            {
                "accountNumber": account,
                "ssoid": self.ssoid,
                "source": CHANNEL,
                "xChannelName": CHANNEL,
            },
        )
        for service in data["AccountServiceInfo"]:
            if service.get("ServiceTypeCode") == DATA_SERVICE_TYPE:
                return service["ServiceNumber"]
        raise PLDTError("no internet service found on this account")

    def repair_requests(self, account, service_id):
        data = self.api(
            "/revampExpLayer/help/getRepairRequestsExp",
            {
                "ssoid": self.ssoid,
                "accountNo": account,
                "serviceid": service_id,
                "mode": "Ongoing",
            },
        )
        # Never report "no repairs" for a response we do not understand.
        if not isinstance(data, dict) or "ongoingRequest" not in data:
            raise PLDTError(f"unexpected repair response: {json.dumps(data)[:300]}")
        return data["ongoingRequest"] or []


def read_env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def credentials():
    env = {**read_env_file(Path(__file__).with_name(".env")), **os.environ}
    email, password = env.get("PLDT_EMAIL"), env.get("PLDT_PASSWORD")
    if not (email and password) and not sys.stdin.isatty():
        raise PLDTError("set PLDT_EMAIL and PLDT_PASSWORD (see .env.example)")
    if not email:
        print("PLDT email: ", end="", file=sys.stderr, flush=True)
        email = input()
    return email, password or getpass.getpass("PLDT password: ")


def summarize(request):
    return {
        "ticket": request.get("prom_number"),
        "title": request.get("service_title"),
        "status": request.get("milestone"),
        "created": request.get("creation_date"),
        "message": request.get("messagebyworkgroup"),
    }


def main():
    parser = argparse.ArgumentParser(description="Show ongoing PLDT repair requests.")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    try:
        client = PLDT()
        client.login(*credentials())
        account = client.default_account()
        found = client.repair_requests(account, client.data_service_id(account))
    except PLDTError as e:
        sys.exit(f"error: {e}")
    except (KeyError, TypeError, AttributeError) as e:
        sys.exit(f"error: PLDT response had an unexpected shape ({e!r})")
    except KeyboardInterrupt:
        sys.exit(130)

    checked = datetime.now().astimezone().isoformat(timespec="seconds")
    requests = [summarize(r) for r in found]

    if args.json:
        print(json.dumps({"checked_at": checked, "requests": requests}, indent=2))
        return

    print(f"PLDT repair status (checked {checked})")
    if not requests:
        print("\nNo ongoing repair requests.")
    for r in requests:
        print()
        for key, value in r.items():
            print(f"  {key.capitalize():8} {value or '-'}")


if __name__ == "__main__":
    main()
