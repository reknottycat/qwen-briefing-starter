"""Create private systemd environment files; preserve existing credentials."""
import argparse
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

from briefing.generators import AdapterFailure, https_base_url, secret_file
from briefing.preferences import load_initial_preferences
from briefing.source_policy import load_source_policy

SCOPES = ("ingest", "worker", "read", "manage")


class ConfigArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        if message.startswith("unrecognized arguments:") or "invalid choice:" in message:
            message = "Unsupported arguments; use --help and configure secret values in local files only"
        super().error(message)


def environment_value(value: str) -> str:
    """Quote a single systemd EnvironmentFile value, never a shell command."""
    if not value or any(char in value for char in "\r\n\0"):
        raise ValueError("configuration values must be nonempty single lines")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv=None) -> int:
    parser = ConfigArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Your HTTPS API base URL ending in /briefing/v1")
    parser.add_argument("--generator", choices=("codex", "pi"), default="codex")
    parser.add_argument("--codex-bin", default="codex", help="Worker executable or name on its PATH")
    parser.add_argument("--source-policy", type=Path, help="Confirmed source-policy.json, readable on API and worker hosts")
    parser.add_argument("--initial-preferences", type=Path, help="Confirmed initial-preferences.json; used only for a new state")
    parser.add_argument("--pi-bin", default="pi")
    parser.add_argument("--pi-provider")
    parser.add_argument("--pi-model")
    parser.add_argument("--pi-api-key-file", type=Path, help="Existing local secret file; never pass its value")
    parser.add_argument("--pi-base-url", help="Optional HTTPS provider API endpoint")
    parser.add_argument("--pi-ca-file", type=Path, help="Optional existing CA certificate file")
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
        policy_path = args.source_policy.expanduser().resolve() if args.source_policy else None
        preferences_path = args.initial_preferences.expanduser().resolve() if args.initial_preferences else None
        if preferences_path and not policy_path:
            raise ValueError("--initial-preferences requires the matching --source-policy")
        if policy_path:
            policy = load_source_policy(policy_path)
            environment_value(str(policy_path))
        if preferences_path:
            load_initial_preferences(preferences_path, source_names={source["name"] for source in policy["sources"]})
            environment_value(str(preferences_path))
        pi_settings = {}
        if args.generator == "pi":
            if (not args.pi_provider or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", args.pi_provider)
                    or not args.pi_model or len(args.pi_model) > 200
                    or any(char.isspace() or char == "\0" for char in args.pi_model)
                    or not args.pi_api_key_file):
                raise ValueError("Pi requires --pi-provider, --pi-model and --pi-api-key-file")
            key_path = args.pi_api_key_file.expanduser().resolve()
            secret_file(str(key_path))  # Validate locally; never include the key in generated env files.
            pi_settings = {"PI_BIN": args.pi_bin, "PI_PROVIDER": args.pi_provider,
                           "PI_MODEL": args.pi_model, "PI_API_KEY_FILE": str(key_path)}
            if args.pi_base_url:
                pi_settings["PI_BASE_URL"] = https_base_url(args.pi_base_url)
            if args.pi_ca_file:
                ca_path = args.pi_ca_file.expanduser().resolve()
                if not ca_path.is_file():
                    raise ValueError("--pi-ca-file must refer to an existing readable certificate file")
                with ca_path.open("rb") as ca:
                    ca.read(1)
                pi_settings["PI_CA_FILE"] = str(ca_path)
            for value in pi_settings.values():
                environment_value(value)
        elif any((args.pi_provider, args.pi_model, args.pi_api_key_file, args.pi_base_url, args.pi_ca_file)):
            raise ValueError("Pi-specific options require --generator pi")
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
    except AdapterFailure:
        parser.error("Invalid Pi endpoint or local secret file; inspect the file locally without printing its value")
    except (OSError, RuntimeError):
        parser.error("Cannot validate source policy, initial preferences or local configuration files")
    except ValueError as error:
        # Policy JSON parse errors can quote private source content. Only known
        # configuration guidance is echoed; other validation stays bounded.
        if str(error).startswith(("--", "Pi ", "Pi-specific", "configuration values", "existing credential")):
            parser.error(str(error))
        parser.error("Invalid source policy, initial preferences or runtime configuration")

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for scope, token in values.items():
        target = root / f"{scope}.token"
        if not target.exists():
            target.write_text(token, encoding="utf-8", newline="\n")
        os.chmod(target, 0o600)
    api = {"BRIEFING_DATA_DIR": args.data_dir,
           **{f"BRIEFING_{scope.upper()}_TOKEN": token for scope, token in values.items()}}
    worker = {"BRIEFING_BASE_URL": base_url, "BRIEFING_WORKER_TOKEN": values["worker"],
              "BRIEFING_GENERATOR": args.generator, "CODEX_BIN": args.codex_bin, **pi_settings}
    if policy_path:
        api["BRIEFING_SOURCE_POLICY_PATH"] = str(policy_path)
        worker["BRIEFING_SOURCE_POLICY_PATH"] = str(policy_path)
    if preferences_path:
        api["BRIEFING_INITIAL_PREFERENCES_PATH"] = str(preferences_path)
    for name, settings in (("api.env", api), ("worker.env", worker)):
        target = root / name
        target.write_text("".join(f"{key}={environment_value(value)}\n" for key, value in settings.items()),
                          encoding="utf-8", newline="\n")
        os.chmod(target, 0o600)
    print("Runtime configuration created; existing credentials preserved; secret values withheld.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
