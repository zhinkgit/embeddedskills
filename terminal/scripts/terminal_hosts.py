#!/usr/bin/env python3
import argparse
import datetime as _dt
import shutil
from pathlib import Path

from terminal_runtime import TerminalError


LIST_METADATA_KEYS = {"aliases", "groups", "tags"}
TEXT_METADATA_KEYS = {"description", "location"}
SUPPORTED_METADATA_KEYS = LIST_METADATA_KEYS | TEXT_METADATA_KEYS
WILDCARD_CHARACTERS = "*?"


def ssh_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def parse_hosts(lines: list[str]) -> list[dict]:
    hosts: list[dict] = []
    comments: list[str] = []
    current: dict | None = None

    def finish() -> None:
        nonlocal current
        if current:
            hosts.append(current)
            current = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            comments.append(line)
            continue

        keyword = stripped.split(None, 1)[0].casefold()
        if keyword == "host":
            finish()
            parts = stripped.split(None, 1)
            host_patterns = parts[1].split() if len(parts) == 2 else []
            concrete_patterns = [
                pattern
                for pattern in host_patterns
                if not pattern.startswith("!")
                and not any(character in pattern for character in WILDCARD_CHARACTERS)
            ]
            if not concrete_patterns:
                comments = []
                continue
            current = {
                "alias": concrete_patterns[0],
                "host_patterns": host_patterns,
                "options": {},
                "metadata": parse_metadata(comments),
                "raw_comments": comments,
            }
            comments = []
            continue

        if keyword == "match":
            finish()
            comments = []
            continue

        if current:
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                current["options"][parts[0].lower()] = parts[1]
            comments = []
            continue

        comments = []

    finish()
    return hosts


def parse_metadata(comments: list[str]) -> dict:
    metadata: dict = {}
    for line in comments:
        text = line.strip()
        if not text.startswith("#"):
            continue
        text = text[1:].strip()
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in SUPPORTED_METADATA_KEYS:
            metadata[key] = value
    return metadata


def metadata_values(metadata: dict, key: str) -> list[str]:
    value = metadata.get(key, "")
    if key not in LIST_METADATA_KEYS:
        return [value] if value else []
    return [item.strip() for item in value.split(",") if item.strip()]


def searchable_values(host: dict) -> list[str]:
    metadata = host.get("metadata", {})
    values = concrete_host_patterns(host)
    values.extend(str(value) for value in host.get("options", {}).values())
    for key in SUPPORTED_METADATA_KEYS:
        values.extend(metadata_values(metadata, key))
    return values


def concrete_host_patterns(host: dict) -> list[str]:
    return [
        pattern
        for pattern in host.get("host_patterns", [])
        if not pattern.startswith("!")
        and not any(character in pattern for character in WILDCARD_CHARACTERS)
    ]


def search_hosts(hosts: list[dict], terms: list[str]) -> list[dict]:
    normalized_terms = [term.strip().casefold() for term in terms if term.strip()]
    if not normalized_terms:
        return []

    matches: list[tuple[int, int, str, dict]] = []
    for host in hosts:
        values = searchable_values(host)
        folded_values = [value.casefold() for value in values]
        haystack = "\n".join(folded_values)
        if not all(term in haystack for term in normalized_terms):
            continue

        host_patterns = {item.casefold() for item in concrete_host_patterns(host)}
        aliases = {
            item.casefold()
            for item in metadata_values(host.get("metadata", {}), "aliases")
        }
        exact_pattern_matches = sum(
            term in host_patterns for term in normalized_terms
        )
        exact_alias_matches = sum(term in aliases for term in normalized_terms)
        matches.append((
            -exact_pattern_matches,
            -exact_alias_matches,
            host["alias"].casefold(),
            host,
        ))

    return [item[3] for item in sorted(matches, key=lambda item: item[:3])]


def resolve_hosts(hosts: list[dict], terms: list[str]) -> list[dict]:
    normalized_terms = [term.strip().casefold() for term in terms if term.strip()]
    if len(normalized_terms) == 1:
        exact_pattern_matches = [
            host
            for host in hosts
            if normalized_terms[0]
            in {item.casefold() for item in concrete_host_patterns(host)}
        ]
        if exact_pattern_matches:
            return exact_pattern_matches
        exact_metadata_aliases = [
            host
            for host in hosts
            if normalized_terms[0]
            in {
                item.casefold()
                for item in metadata_values(host.get("metadata", {}), "aliases")
            }
        ]
        if exact_metadata_aliases:
            return exact_metadata_aliases
    return search_hosts(hosts, terms)


def validate_single_line_field(name: str, value: object) -> None:
    if isinstance(value, str) and has_control_characters(value):
        raise ValueError(f"{name} must not contain control characters")


def has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def validate_host_alias(alias: str) -> None:
    validate_single_line_field("alias", alias)
    if not alias or alias.startswith(("-", "!")):
        raise ValueError("alias must be a non-option OpenSSH Host name")
    if any(character.isspace() for character in alias):
        raise ValueError("alias must not contain whitespace")
    if "#" in alias or any(
        character in alias for character in WILDCARD_CHARACTERS
    ):
        raise ValueError("alias must be a literal OpenSSH Host name")


def validate_port(port: object) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("port must be an integer between 1 and 65535") from exc
    if not 1 <= value <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return value


def format_config_value(value: str) -> str:
    if (
        not any(character.isspace() for character in value)
        and '"' not in value
        and "#" not in value
    ):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_add_arguments(args: argparse.Namespace) -> None:
    for name in (
        "alias",
        "host",
        "user",
        "port",
        "key",
        "proxy_jump",
        "description",
        "aliases",
        "groups",
        "tags",
        "location",
    ):
        validate_single_line_field(name, getattr(args, name, None))
    validate_host_alias(args.alias)
    validate_port(args.port)
    for name in ("host", "user", "proxy_jump"):
        value = getattr(args, name, None)
        if value and any(character.isspace() for character in value):
            raise ValueError(f"{name} must not contain whitespace")
        if value and "#" in value:
            raise ValueError(f"{name} must not contain '#'")


def backup_config(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def add_host(args: argparse.Namespace, path: Path) -> dict:
    validate_add_arguments(args)
    folded_alias = args.alias.casefold()
    if any(folded_alias == item.casefold() for host in parse_hosts(read_lines(path)) for item in concrete_host_patterns(host)):
        raise TerminalError("duplicate_alias", f"Host 别名已存在：{args.alias}")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = backup_config(path)
    tags = args.tags or ""
    block: list[str] = []
    if path.exists() and path.read_text(encoding="utf-8").strip():
        block.append("")
    if args.description:
        block.append(f"# description: {args.description}")
    if args.aliases:
        block.append(f"# aliases: {args.aliases}")
    if args.groups:
        block.append(f"# groups: {args.groups}")
    if tags:
        block.append(f"# tags: {tags}")
    if args.location:
        block.append(f"# location: {args.location}")
    block.extend([
        f"Host {args.alias}",
        f"    HostName {args.host}",
        f"    User {args.user}",
        f"    Port {validate_port(args.port)}",
    ])
    if args.key:
        block.append(f"    IdentityFile {format_config_value(args.key)}")
    if args.proxy_jump:
        block.append(f"    ProxyJump {args.proxy_jump}")

    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(block))
        f.write("\n")

    return {"alias": args.alias, "config_path": str(path), "backup_path": str(backup) if backup else None}
