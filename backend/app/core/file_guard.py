from pathlib import Path

WHITELIST: list[Path] = []


def init(roots: list[str]):
    global WHITELIST
    WHITELIST = [Path(r).resolve() for r in roots]


def resolve(file_path: str) -> Path:
    path = Path(file_path).resolve()
    if not WHITELIST:
        return path
    for allowed in WHITELIST:
        try:
            path.relative_to(allowed)
            return path
        except ValueError:
            continue
    raise PermissionError(f"路径不在白名单内: {file_path}")
