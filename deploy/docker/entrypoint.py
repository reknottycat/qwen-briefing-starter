"""Read service-scoped Docker secret files, then replace this process."""
from __future__ import annotations

import os
import ipaddress
from pathlib import Path
import sys

ROLES = ("INGEST", "WORKER", "READ", "MANAGE")


def configure_trusted_proxies(environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    value = result.pop("BRIEFING_TRUSTED_PROXY_IPS", result.get("FORWARDED_ALLOW_IPS"))
    if value is None:
        return result
    try:
        entries = value.split(",")
        if len(value) > 512 or not 1 <= len(entries) <= 8:
            raise ValueError
        addresses = []
        for entry in entries:
            if "%" in entry:
                raise ValueError
            address = ipaddress.ip_address(entry.strip())
            if address.is_unspecified:
                raise ValueError
            normalized = str(address)
            if normalized not in addresses:
                addresses.append(normalized)
    except ValueError:
        raise ValueError("invalid_trusted_proxy_ips") from None
    result["FORWARDED_ALLOW_IPS"] = ",".join(addresses)
    return result


def load_role_secrets(environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    for role in ROLES:
        name = f"BRIEFING_{role}_TOKEN"
        pointer = name + "_FILE"
        if not result.get(pointer):
            continue
        if result.get(name):
            raise ValueError("ambiguous_role_secret")
        try:
            with Path(result[pointer]).open("rb") as handle:
                raw = handle.read(4097)
                value = raw.decode("utf-8").strip()
        except (OSError, UnicodeError):
            raise ValueError("unreadable_role_secret") from None
        if len(raw) > 4096 or not 24 <= len(value) <= 512 or any(char.isspace() or char == "\0" for char in value):
            raise ValueError("invalid_role_secret")
        result[name] = value
        del result[pointer]
    return result


def main() -> int:
    try:
        if len(sys.argv) < 2:
            raise ValueError("missing_command")
        environment = configure_trusted_proxies(load_role_secrets(dict(os.environ)))
        os.execvpe(sys.argv[1], sys.argv[1:], environment)
    except ValueError as error:
        print(f"Container configuration error: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("Container configuration error: command_unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
