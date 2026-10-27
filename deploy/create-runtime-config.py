"""Create private systemd environment files; preserve existing credentials."""
import argparse
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

SCOPES = ("ingest", "worker", "read", "manage")


def environment_value(value: str) -> str:
    """Quote a single systemd EnvironmentFile value, never a shell command."""
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("configuration values must be nonempty single lines")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Your HTTPS API base URL ending in /briefing/v1")
    parser.add_argument("--codex-bin", default="codex", help="Worker executable or name on its PATH")
    parser.add_argument("--data-dir", default="/var/lib/qwen-daily-briefing",
                        help="API host data directory; also update the API service ReadWritePaths when changed")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "runtime" / "credentials")
    args = parser.parse_args(argv)
    base_url = args.base_url.rstrip("/")
    try:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(char.isspace() for char in base_url)):
            raise ValueError("--base-url must be an HTTPS URL without credentials, query or fragment")
        parsed.port  # Reject malformed/out-of-range ports before writing anything.
        for value in (base_url, args.codex_bin, args.data_dir):
            environment_value(value)
        root = args.output_dir.expanduser()
        values = {}
        for scope in SCOPES:
            target = root / f"{scope}.token"
            token = target.read_text(encoding="utf-8").strip() if target.exists() else secrets.token_urlsafe(32)
            if len(token) < 24 or any(char.isspace() or char == "\0" for char in token):
                raise ValueError("existing credential is invalid; inspect its protected token file")
            values[scope] = token
        if len(set(values.values())) != len(SCOPES):
            raise ValueError("existing credentials must be distinct for all four roles")
    except ValueError as error:
        parser.error(str(error))

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for scope, token in values.items():
        target = root / f"{scope}.token"
        if not target.exists():
            target.write_text(token, encoding="utf-8", newline="\n")
        os.chmod(target, 0o600)
    api = {"BRIEFING_DATA_DIR": args.data_dir,
           **{f"BRIEFING_{scope.upper()}_TOKEN": token for scope, token in values.items()}}
    worker = {"BRIEFING_BASE_URL": base_url, "BRIEFING_WORKER_TOKEN": values["worker"], "CODEX_BIN": args.codex_bin}
    for name, settings in (("api.env", api), ("worker.env", worker)):
        target = root / name
        target.write_text("".join(f"{key}={environment_value(value)}\n" for key, value in settings.items()),
                          encoding="utf-8", newline="\n")
        os.chmod(target, 0o600)
    print("Runtime configuration created; existing credentials preserved; secret values withheld.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
