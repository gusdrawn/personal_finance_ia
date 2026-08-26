"""Deploy the Django application through the cPanel UAPI/API2 endpoints.

The hosting account does not expose SSH to GitHub Actions, so this script uses
Fileman to upload/extract the release and a short-lived cPanel cron entry to
run Django migrations inside the Python Selector virtual environment.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_TIMEOUT = 180
MIGRATION_TIMEOUT = 600
POLL_SECONDS = 15


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_host(value: str) -> str:
    host = value.strip().removeprefix("https://").removeprefix("http://")
    host = host.rstrip("/")
    if host.endswith(":2083"):
        host = host[:-5]
    if not host or "/" in host:
        raise RuntimeError("CPANEL_HOST must contain only a hostname")
    return host


class CPanelClient:
    def __init__(self, host: str, username: str, token: str) -> None:
        self.host = normalize_host(host)
        self.username = username
        self.base_url = f"https://{self.host}:2083"
        self.headers = {
            "Authorization": f"cpanel {username}:{token}",
            "Accept": "application/json",
            "User-Agent": "github-actions-cpanel-deploy/1.0",
        }

    def request_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = API_TIMEOUT,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        request_headers = dict(self.headers)
        if headers:
            request_headers.update(headers)
        method = "POST" if body is not None else "GET"
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"cPanel API HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach cPanel API: {exc.reason}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("cPanel API returned invalid JSON") from exc

    def uapi(self, module: str, function: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.request_json(
            f"/execute/{module}/{function}",
            params,
            timeout=API_TIMEOUT,
        )

    def api2(self, module: str, function: str, params: dict[str, Any]) -> dict[str, Any]:
        api2_params = {
            "cpanel_jsonapi_user": self.username,
            "cpanel_jsonapi_apiversion": "2",
            "cpanel_jsonapi_module": module,
            "cpanel_jsonapi_func": function,
            **params,
        }
        return self.request_json("/json-api/cpanel", api2_params, timeout=API_TIMEOUT)

    @staticmethod
    def status_is_ok(payload: dict[str, Any]) -> bool:
        return str(payload.get("status")) == "1"

    @staticmethod
    def api2_status_is_ok(payload: dict[str, Any]) -> bool:
        event = (payload.get("cpanelresult") or {}).get("event") or {}
        return str(event.get("result")) == "1"

    def upload_archive(self, archive: Path, app_root: str, remote_name: str) -> None:
        boundary = "----GitHubActionsCpanelDeployBoundary"
        archive_bytes = archive.read_bytes()
        chunks: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode(),
                    b"\r\n",
                ]
            )

        add_field("dir", app_root)
        add_field("overwrite", "1")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="file-0"; '
                    f'filename="{remote_name}"\r\n'
                ).encode(),
                b"Content-Type: application/gzip\r\n\r\n",
                archive_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        response = self.request_json(
            "/execute/Fileman/upload_files",
            body=b"".join(chunks),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=API_TIMEOUT,
        )
        data = response.get("data") or {}
        if not self.status_is_ok(response) or data.get("succeeded") != 1:
            raise RuntimeError(
                "cPanel upload failed: "
                f"{response.get('errors') or data.get('uploads') or response}"
            )
        print(f"Uploaded release archive ({archive.stat().st_size:,} bytes).")

    def trash(self, path: str) -> None:
        response = self.api2("Fileman", "fileop", {"op": "trash", "sourcefiles": path})
        if not self.api2_status_is_ok(response):
            raise RuntimeError(f"Could not move temporary file to cPanel trash: {path}")

    def extract_archive(self, archive_path: str, app_root: str) -> None:
        response = self.api2(
            "Fileman",
            "fileop",
            {
                "op": "extract",
                "sourcefiles": archive_path,
                "destfiles": app_root,
                "overwrite": "1",
            },
        )
        if not self.api2_status_is_ok(response):
            result = response.get("cpanelresult") or {}
            raise RuntimeError(f"cPanel extraction failed: {result.get('event') or result}")
        print(f"Extracted release into {app_root}.")

    def save_file(self, directory: str, filename: str, content: str) -> None:
        response = self.uapi(
            "Fileman",
            "save_file_content",
            {"dir": directory, "file": filename, "content": content},
        )
        if not self.status_is_ok(response):
            raise RuntimeError(f"Could not write {directory}/{filename}")

    def list_files(self, directory: str) -> set[str]:
        response = self.uapi(
            "Fileman",
            "list_files",
            {"dir": directory, "show_hidden": "1"},
        )
        if not self.status_is_ok(response):
            raise RuntimeError(f"Could not list remote directory: {directory}")
        data = response.get("data") or []
        if isinstance(data, dict):
            data = data.get("data") or data.get("files") or []
        return {
            item.get("file") or item.get("name")
            for item in data
            if isinstance(item, dict) and (item.get("file") or item.get("name"))
        }

    def read_file(self, directory: str, filename: str) -> str:
        response = self.uapi(
            "Fileman",
            "get_file_content",
            {"dir": directory, "file": filename},
        )
        if not self.status_is_ok(response):
            return ""
        data = response.get("data") or {}
        if isinstance(data, dict):
            return data.get("content") or ""
        return ""

    def add_migration_cron(self, command: str) -> None:
        response = self.api2(
            "Cron",
            "add_line",
            {
                "minute": "*",
                "hour": "*",
                "day": "*",
                "month": "*",
                "weekday": "*",
                "command": command,
            },
        )
        if not self.api2_status_is_ok(response):
            raise RuntimeError(f"Could not create temporary migration cron: {response}")
        print("Temporary migration cron created.")

    def find_cron_line(self, marker: str) -> int | None:
        response = self.api2("Cron", "fetchcron", {})
        data = (response.get("cpanelresult") or {}).get("data") or []
        for item in data:
            if isinstance(item, dict) and marker in item.get("command", ""):
                number = item.get("commandnumber")
                if number is not None:
                    return int(number)
        return None

    def remove_migration_cron(self, marker: str) -> bool:
        for _ in range(4):
            line = self.find_cron_line(marker)
            if line is not None:
                response = self.api2("Cron", "remove_line", {"line": str(line)})
                if not self.api2_status_is_ok(response):
                    raise RuntimeError("Could not remove temporary migration cron")
                print("Temporary migration cron removed.")
                return True
            time.sleep(2)
        print("Warning: temporary migration cron was not found for cleanup.")
        return False


def migration_command(app_root: str, python_bin: str, marker: str, lock: str, log: str) -> str:
    python_code = (
        "import finance_wsgi; from django.core.management import call_command; "
        "call_command('migrate', no_input=True)"
    )
    inner = (
        f"if [ ! -f {shlex.quote(marker)} ] && mkdir {shlex.quote(lock)} 2>/dev/null; "
        f"then trap 'rmdir {shlex.quote(lock)}' EXIT; "
        f"cd {shlex.quote(app_root)} && {shlex.quote(python_bin)} -c {shlex.quote(python_code)} "
        f"> {shlex.quote(log)} 2>&1 && touch {shlex.quote(marker)}; fi"
    )
    return f"/bin/sh -c {shlex.quote(inner)}"


def main() -> None:
    archive = Path(required_env("DEPLOY_ARCHIVE"))
    if not archive.is_file():
        raise RuntimeError(f"Deployment archive does not exist: {archive}")

    app_root = required_env("CPANEL_APP_ROOT")
    python_bin = required_env("CPANEL_PYTHON_BIN")
    if not app_root.startswith("/") or not python_bin.startswith("/"):
        raise RuntimeError("CPANEL_APP_ROOT and CPANEL_PYTHON_BIN must be absolute paths")

    deploy_id = re.sub(r"[^A-Za-z0-9_.-]", "-", required_env("DEPLOY_ID"))
    remote_archive = f"{app_root}/.github-deploy-{deploy_id}.tar.gz"
    marker = f"{app_root}/.github-migrate-{deploy_id}.done"
    lock = f"{app_root}/.github-migrate-{deploy_id}.lock"
    log = f"{app_root}/.github-migrate-{deploy_id}.log"
    marker_name = Path(marker).name

    client = CPanelClient(
        required_env("CPANEL_HOST"),
        required_env("CPANEL_USERNAME"),
        required_env("CPANEL_API_TOKEN"),
    )
    cron_marker = f".github-migrate-{deploy_id}"
    cron_created = False
    migration_succeeded = False

    try:
        client.upload_archive(archive, app_root, Path(remote_archive).name)
        client.extract_archive(remote_archive, app_root)

        command = migration_command(app_root, python_bin, marker, lock, log)
        client.add_migration_cron(command)
        cron_created = True

        print("Waiting for remote Django migrations...")
        deadline = time.monotonic() + MIGRATION_TIMEOUT
        while time.monotonic() < deadline:
            if marker_name in client.list_files(app_root):
                migration_succeeded = True
                print("Remote Django migrations completed.")
                break
            time.sleep(POLL_SECONDS)

        if not migration_succeeded:
            log_tail = client.read_file(app_root, Path(log).name).splitlines()[-30:]
            raise RuntimeError(
                "Remote migrations timed out. Last log lines:\n" + "\n".join(log_tail)
            )
    finally:
        if cron_created:
            client.remove_migration_cron(cron_marker)

        if migration_succeeded:
            client.save_file(
                f"{app_root}/tmp",
                "restart.txt",
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            print("Passenger restart requested.")

        try:
            client.trash(remote_archive)
            if migration_succeeded:
                client.trash(log)
                client.trash(marker)
        except RuntimeError as exc:
            print(f"Warning: temporary cleanup failed: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Deployment failed: {exc}")
        raise SystemExit(1) from exc
