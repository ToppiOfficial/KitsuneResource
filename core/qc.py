from pathlib import Path

def qc_read_includes(qc_path: Path) -> list[Path]:
    if not qc_path.exists():
        return []

    visited = set()
    includes = []

    def scan(path: Path):
        if path in visited or not path.exists():
            return
        visited.add(path)

        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$include"):
                    continue

                raw = line.split(None, 1)[1].strip().strip('"')
                target = (path.parent / raw).resolve()

                if target.exists():
                    includes.append(target)
                    scan(target)

    scan(qc_path)
    return includes

def qc_read_materials(qc_path: Path, dumped_materials: list[str] | None = None) -> list[str]:
    if not qc_path.exists():
        return dumped_materials or []

    dumped_materials = dumped_materials or []

    def parse_renamematerial(path: Path) -> dict[str, str]:
        mapping = {}
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$renamematerial"):
                    continue
                raw = line[len("$renamematerial"):].strip()
                args, buf, in_quote = [], [], False
                for ch in raw:
                    if ch == '"':
                        if in_quote and buf:
                            args.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                        in_quote = not in_quote
                    elif ch.isspace() and not in_quote:
                        if buf:
                            args.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                    else:
                        buf.append(ch)
                if buf:
                    args.append("".join(buf).strip().replace("\\", "/"))
                if len(args) == 2:
                    mapping[args[0]] = args[1]
        return mapping

    def parse_cdmaterials(path: Path) -> list[str]:
        found = []
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("$cdmaterials"):
                    continue
                raw = line.split(None, 1)[1] if " " in line else ""
                tokens, buf, in_quote = [], [], False
                for ch in raw:
                    if ch == '"':
                        if in_quote and buf:
                            tokens.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                        in_quote = not in_quote
                    elif ch.isspace() and not in_quote:
                        if buf:
                            tokens.append("".join(buf).strip().replace("\\", "/"))
                            buf.clear()
                    else:
                        buf.append(ch)
                if buf:
                    tokens.append("".join(buf).strip().replace("\\", "/"))
                found.extend(tokens)
        return found

    def parse_texturegroup(path: Path) -> list[str]:
        mats = []
        content = path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip().lower()
            if line.startswith("$texturegroup") and "skinfamilies" in line:
                while i < len(lines) and "{" not in lines[i]:
                    i += 1
                i += 1
                while i < len(lines) and "}" not in lines[i]:
                    row = lines[i].strip()
                    tokens, buf, in_quote = [], [], False
                    for ch in row:
                        if ch == '"':
                            if in_quote and buf:
                                tokens.append("".join(buf).strip().replace("\\", "/"))
                                buf.clear()
                            in_quote = not in_quote
                        else:
                            if in_quote:
                                buf.append(ch)
                    mats.extend(tokens)
                    i += 1
            else:
                i += 1
        return mats

    rename_map = parse_renamematerial(qc_path)
    cdmats = parse_cdmaterials(qc_path)
    texmats = parse_texturegroup(qc_path)

    for inc in qc_read_includes(qc_path):
        rename_map.update(parse_renamematerial(inc))
        cdmats.extend(parse_cdmaterials(inc))
        texmats.extend(parse_texturegroup(inc))

    renamed_dumps = [rename_map.get(m, m) for m in dumped_materials]

    all_materials = renamed_dumps + [rename_map.get(m, m) for m in texmats]

    combined_mats = []

    if cdmats:
        # Combine each material with each cdmaterial base
        for base in cdmats:
            base = base.rstrip("/")
            for mat in all_materials:
                combined_mats.append(f"{base}/{mat}")
    else:
        # No cdmaterials, just keep materials as-is
        combined_mats = all_materials

    return renamed_dumps + combined_mats