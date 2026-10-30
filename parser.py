"""Parser berbasis pattern matching (regex) untuk berbagai format dotfiles.

Format yang didukung:
  - Hyprland .conf  : source = ..., exec/exec-once = ..., bind = ..., exec, ...
                      termasuk ekspansi variabel ($terminal = kitty)
  - Hyprland .lua   : require("hypr.x"), dofile(...), exec_cmd("..."),
                      exec_once = { "..." }, o.launch("...")
  - Waybar .jsonc   : "include": [...], "exec": "...", "on-click": "..."
  - CSS             : @import "file.css";
  - Conf generik    : include = path / include path  (kitty, foot, mako)
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from pathlib import Path

from models import DepKind, Dependency

# ---------------------------------------------------------------------------
# Pola regex
# ---------------------------------------------------------------------------

# Hyprland .conf
RE_HYPR_VAR = re.compile(r"^\s*\$(?P<name>\w+)\s*=\s*(?P<value>.*?)\s*$")
RE_HYPR_SOURCE = re.compile(r"^\s*source\s*=\s*(?P<path>.+?)\s*$")
RE_HYPR_EXEC = re.compile(
    r"^\s*(?P<key>exec(?:-once|-shutdown|r|r-once)?)\s*=\s*(?P<cmd>.+?)\s*$"
)
# bind = MOD, KEY, exec, perintah   (juga binde, bindl, bindr, ...)
# bindd = MOD, KEY, deskripsi, exec, perintah   (varian dengan deskripsi)
RE_HYPR_BIND_EXEC = re.compile(
    r"^\s*(?P<key>bind[a-z]*)\s*=\s*(?:[^,]*,){2,3}\s*exec\s*,\s*(?P<cmd>.+?)\s*$"
)
RE_HYPR_VAR_REF = re.compile(r"\$(\w+)")

# include generik (kitty: "include file.conf", foot/mako: "include=file")
RE_GENERIC_INCLUDE = re.compile(r"^\s*(?:glob)?include\s*(?:=\s*|\s+)(?P<path>\S.*?)\s*$")

# Lua
_LUA_STR = r"""(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
RE_LUA_REQUIRE = re.compile(r"\brequire\s*\(?\s*" + _LUA_STR)
RE_LUA_DOFILE = re.compile(r"\bdofile\s*\((?P<expr>.*)\)\s*$", re.MULTILINE)
RE_LUA_EXEC_CALL = re.compile(
    r"\b(?P<fn>exec_cmd|exec_on_start|exec_once|launch_on_start|launch)\s*\("
)
# Omarchy: o.bind("SUPER + RETURN", "Terminal", "perintah")
RE_LUA_BIND = re.compile(r"\bbind\s*\(\s*" + _LUA_STR.replace("q>", "q1>").replace("s>", "s1>").replace("(?P=q)", "(?P=q1)")
                         + r"\s*,\s*" + _LUA_STR.replace("q>", "q2>").replace("s>", "s2>").replace("(?P=q)", "(?P=q2)")
                         + r"\s*,")
# local terminal = "kitty"   /   local bin = os.getenv("HOME") .. "/x"
RE_LUA_ASSIGN = re.compile(r"^[ \t]*(?:local[ \t]+)?(?P<name>[A-Za-z_]\w*)[ \t]*=[ \t]*(?P<expr>[^\n{]+?)[ \t]*$", re.MULTILINE)
RE_LUA_TOKEN = re.compile(_LUA_STR + r"|(?P<concat>\.\.)|(?P<ident>[A-Za-z_]\w*)|(?P<ws>\s+)")
RE_LUA_EXEC_TABLE = re.compile(r"\bexec_once\s*=\s*\{(?P<body>.*?)\}", re.DOTALL)
RE_LUA_STRING = re.compile(_LUA_STR)
RE_LUA_GETENV = re.compile(
    r"""\(?\s*os\.getenv\(\s*["'](?P<var>\w+)["']\s*\)(?:\s+or\s+["'](?P<default>[^"']*)["'])?\s*\)?"""
)

# Waybar (JSON/JSONC)
RE_WAYBAR_EXEC = re.compile(
    r'"(?P<key>exec(?:-if|-on-event)?|on-(?:click|scroll)[\w-]*)"\s*:\s*"(?P<cmd>(?:\\.|[^"\\])*)"'
)
RE_WAYBAR_INCLUDE = re.compile(r'"include"\s*:\s*(?P<value>\[[^\]]*\]|"[^"]*")', re.DOTALL)
RE_JSON_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')

# CSS
RE_CSS_IMPORT = re.compile(r"""@import\s+(?:url\()?\s*["'](?P<path>[^"']+)["']""")

# Program pembungkus yang dilewati untuk menemukan binary sebenarnya.
WRAPPERS = {
    "env", "exec", "nohup", "setsid", "sudo", "doas", "nice", "ionice",
    "uwsm", "uwsm-app", "app2unit", "systemd-run", "runapp", "flatpak-spawn",
}
WRAPPER_SUBCOMMANDS = {"app", "run"}
SHELLS = {"sh", "bash", "zsh", "fish", "dash"}
# Aksi bawaan modul Waybar (hyprland/workspaces, clock, ...), bukan program.
WAYBAR_ACTIONS = {
    "activate", "close", "mode", "tz_up", "tz_down", "shift_up", "shift_down",
    "shift_reset", "toggle", "next", "previous", "show", "hide",
}
SHELL_BUILTINS = {
    "echo", "printf", "cd", "test", "[", "true", "false", "export", "source",
    ".", "read", "kill", "wait", "if", "then", "fi", "for",
    "do", "done", "while", "case", "esac", "set", "unset", "eval", "trap",
}
RE_CMD_SUBST = re.compile(r"\$\(([^()]*)\)")
RE_OPERATOR = re.compile(r"[();<>|&]+")
RE_ENV_ASSIGN = re.compile(r"^\w+=")
RE_HYPR_RULE_PREFIX = re.compile(r"^\s*\[[^\]]*\]\s*")

SKIP_DIRS = {".git", "node_modules", "__pycache__", "lazy", "plugged", "cache", "Cache"}
RE_BACKUP = re.compile(r"(\.bak|\.orig|\.old|~$|\.swp$)", re.IGNORECASE)
MAX_FILE_SIZE = 1_000_000


# ---------------------------------------------------------------------------
# Utilitas
# ---------------------------------------------------------------------------

class ParseContext:
    """Konfigurasi bersama selama satu kali scan."""

    def __init__(self, home: Path, config_dir: Path):
        self.home = home
        self.config_dir = config_dir
        omarchy = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))
        # Sama dengan package.path di bootstrap Omarchy.
        self.lua_roots = [home / ".local/state", config_dir, omarchy]
        self.hypr_vars: dict[str, str] = {}

    def getenv(self, var: str) -> str | None:
        """Variabel environment, tapi HOME & XDG_CONFIG_HOME mengikuti home
        yang sedang discan (penting untuk home virtual repo GitHub)."""
        if var == "HOME":
            return str(self.home)
        if var == "XDG_CONFIG_HOME":
            return str(self.config_dir)
        return os.environ.get(var)

    def expand_path(self, raw: str, base_dir: Path) -> str:
        """Ekspansi ~, $HOME, $VAR, lalu jadikan absolut relatif ke base_dir."""
        text = raw.strip().strip("\"'")
        if text == "~" or text.startswith("~/"):
            text = str(self.home) + text[1:]
        for var in ("XDG_CONFIG_HOME", "HOME"):
            text = text.replace(f"${{{var}}}", self.getenv(var)).replace(f"${var}", self.getenv(var))
        text = os.path.expandvars(text)
        path = Path(text)
        if not path.is_absolute():
            path = base_dir / path
        return os.path.normpath(path)


def detect_format(path: Path) -> str | None:
    suffix = path.suffix.lower()
    parent = path.parent.name.lower()
    if suffix == ".lua":
        return "lua"
    if suffix == ".css":
        return "css"
    if suffix == ".jsonc" or ("waybar" in path.parts and (suffix == ".json" or path.name == "config")):
        return "waybar"
    if suffix == ".conf" and ("hypr" in path.parts or path.name.startswith("hypr")):
        return "hypr"
    if suffix in {".conf", ".ini"} or path.name == "config":
        return "generic"
    return None


def discover_files(directory: Path) -> list[Path]:
    """Cari semua file config yang didukung di bawah `directory`."""
    found: list[Path] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(root) / name
            if RE_BACKUP.search(name) or detect_format(path) is None:
                continue
            try:
                if path.stat().st_size <= MAX_FILE_SIZE:
                    found.append(path)
            except OSError:
                continue
    return found


def strip_hash_comment(line: str) -> str:
    """Hapus komentar '#'. Hyprland memakai '##' sebagai escape karakter '#'."""
    if line.lstrip().startswith("#"):
        return ""
    return re.split(r"(?<!#)\s#(?!#)", line, maxsplit=1)[0].replace("##", "#")


def strip_lua_comments(text: str) -> str:
    """Hapus komentar Lua (-- dan --[[ ]]) tanpa merusak isi string.

    Newline dipertahankan agar nomor baris tetap akurat.
    """
    out: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
        elif ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
        elif text.startswith("--[[", i):
            end = text.find("]]", i + 4)
            end = n if end == -1 else end + 2
            out.append("\n" * text.count("\n", i, end))
            i = end
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def extract_binaries(command: str) -> list[str]:
    """Ambil nama binary dari sebuah perintah shell.

    Contoh:
      "uwsm app -- nm-applet --indicator"  -> ["nm-applet"]
      "swww-daemon && swww img a.png"      -> ["swww-daemon", "swww"]
      'sh -c "sleep 2 && dunst"'           -> ["sleep", "dunst"]
      "[workspace 2 silent] firefox"       -> ["firefox"]
    """
    command = RE_HYPR_RULE_PREFIX.sub("", command.strip())
    # Command substitution $(...) juga memanggil program; proses terpisah.
    inner = [b for sub in RE_CMD_SUBST.findall(command) for b in extract_binaries(sub)]
    command = RE_CMD_SUBST.sub("", command)
    # shlex dengan punctuation_chars memecah operator (&&, ;, |) sebagai token
    # tersendiri sambil tetap menghormati tanda kutip.
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:  # kutip tidak tertutup
        tokens = command.split()
    binaries: list[str] = []
    segment: list[str] = []
    skip_target = False
    for tok in tokens + [";"]:
        if skip_target:                       # tujuan redirect: > /dev/null
            skip_target = False
            if not RE_OPERATOR.fullmatch(tok):
                continue
        if RE_OPERATOR.fullmatch(tok) and ("<" in tok or ">" in tok):
            if segment and segment[-1].isdigit():  # file descriptor: 2>&1
                segment.pop()
            skip_target = True
        elif RE_OPERATOR.fullmatch(tok):
            binaries.extend(_binary_from_tokens(segment))
            segment = []
        else:
            segment.append(tok)
    return binaries + inner


def _binary_from_tokens(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if RE_ENV_ASSIGN.match(tok):          # VAR=nilai program
            i += 1
            continue
        name = os.path.basename(tok)
        if name in WRAPPERS:
            i += 1
            while i < len(tokens) and (
                tokens[i].startswith("-") or tokens[i] in WRAPPER_SUBCOMMANDS
                or RE_ENV_ASSIGN.match(tokens[i])
            ):
                i += 1
            continue
        if name in SHELLS and i + 2 < len(tokens) and tokens[i + 1] == "-c":
            return [name] + extract_binaries(tokens[i + 2])
        if name in SHELL_BUILTINS:
            return []
        return [tok]
    return []


# ---------------------------------------------------------------------------
# Parser per format
# ---------------------------------------------------------------------------

def _file_deps(raw: str, source: Path, line: int, directive: str, ctx: ParseContext) -> list[Dependency]:
    """Buat dependency FILE, termasuk ekspansi glob (source = conf.d/*.conf)."""
    target = ctx.expand_path(raw, source.parent)
    if "$" in target:  # variabel yang tidak dikenal
        return [Dependency(DepKind.FILE, raw, source, line, directive, raw, unresolved=True)]
    if glob.has_magic(target):
        matches = sorted(glob.glob(target))
        if matches:
            return [Dependency(DepKind.FILE, m, source, line, directive, raw) for m in matches]
    return [Dependency(DepKind.FILE, target, source, line, directive, raw)]


def _binary_deps(cmd: str, source: Path, line: int, directive: str, ctx: ParseContext,
                 raw: str | None = None) -> list[Dependency]:
    raw = cmd if raw is None else raw
    deps: list[Dependency] = []
    for binary in extract_binaries(cmd):
        if binary.startswith("~") or "/" in binary:
            binary = ctx.expand_path(binary, source.parent)  # juga ekspansi $HOME
        if "$" in binary or "{" in binary:
            deps.append(Dependency(DepKind.BINARY, binary, source, line, directive, raw, unresolved=True))
            continue
        deps.append(Dependency(DepKind.BINARY, binary, source, line, directive, raw))
    return deps


def expand_hypr_vars(value: str, ctx: ParseContext) -> str:
    for _ in range(5):  # izinkan variabel bersarang
        new = RE_HYPR_VAR_REF.sub(lambda m: ctx.hypr_vars.get(m[1], m[0]), value)
        if new == value:
            break
        value = new
    return value


def resolve_late(dep: Dependency, ctx: ParseContext) -> list[Dependency] | None:
    """Coba lagi dependency Hyprland yang memakai variabel yang baru
    didefinisikan di file yang di-source belakangan ($term di keybinds.conf,
    didefinisikan di defaults.conf). Return None jika tetap tidak bisa."""
    if not dep.unresolved or "$" not in dep.target or detect_format(dep.source) != "hypr":
        return None
    expanded = expand_hypr_vars(dep.target, ctx)
    if expanded == dep.target:
        return None
    if dep.kind is DepKind.FILE:
        return _file_deps(expanded, dep.source, dep.line, dep.directive, ctx)
    return _binary_deps(expanded, dep.source, dep.line, dep.directive, ctx, raw=dep.raw)


def parse_hypr(path: Path, text: str, ctx: ParseContext) -> list[Dependency]:
    lines = [strip_hash_comment(l) for l in text.splitlines()]

    # Pass 1: kumpulkan definisi variabel ($terminal = kitty).
    for line in lines:
        if m := RE_HYPR_VAR.match(line):
            ctx.hypr_vars[m["name"]] = m["value"]

    def expand(value: str) -> str:
        return expand_hypr_vars(value, ctx)

    # Pass 2: ekstrak dependensi.
    deps: list[Dependency] = []
    for lineno, line in enumerate(lines, start=1):
        if m := RE_HYPR_SOURCE.match(line):
            deps += _file_deps(expand(m["path"]), path, lineno, "source", ctx)
        elif m := RE_HYPR_EXEC.match(line):
            deps += _binary_deps(expand(m["cmd"]), path, lineno, m["key"], ctx)
        elif m := RE_HYPR_BIND_EXEC.match(line):
            deps += _binary_deps(expand(m["cmd"]), path, lineno, f"{m['key']} exec", ctx)
    return deps


def parse_generic(path: Path, text: str, ctx: ParseContext) -> list[Dependency]:
    deps: list[Dependency] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = strip_hash_comment(line)
        if m := RE_GENERIC_INCLUDE.match(line):
            deps += _file_deps(m["path"], path, lineno, "include", ctx)
    return deps


def eval_lua_expr(expr: str, variables: dict[str, str] | None = None,
                  ctx: ParseContext | None = None) -> str | None:
    """Evaluasi ekspresi string Lua sederhana secara statis.

    Mendukung literal, operator concat `..`, os.getenv("X") [or "default"],
    dan variabel lokal yang sudah diketahui. Return None jika terlalu dinamis.
    """
    variables = variables or {}

    def getenv(m: re.Match) -> str:
        value = ctx.getenv(m["var"]) if ctx else os.environ.get(m["var"])
        if value is None:
            value = m["default"] or ""
        return '"' + value.replace('"', '\\"') + '"'

    expr = RE_LUA_GETENV.sub(getenv, expr).strip()
    result: list[str] = []
    pos, expect_value = 0, True
    while pos < len(expr):
        m = RE_LUA_TOKEN.match(expr, pos)
        if not m:
            return None
        pos = m.end()
        if m["ws"]:
            continue
        if expect_value and m["s"] is not None:
            result.append(m["s"])
        elif expect_value and m["ident"] in variables:
            result.append(variables[m["ident"]])
        elif not expect_value and m["concat"]:
            pass
        else:
            return None
        expect_value = not expect_value
    return None if expect_value else "".join(result)


def lua_call_arg(code: str, start: int) -> str:
    """Ambil teks satu argumen fungsi mulai dari `start` sampai ',' atau ')'
    pada level kurung yang sama, dengan memperhatikan string."""
    depth, i, quote = 0, start, None
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "({[":
            depth += 1
        elif ch in ")}]":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        i += 1
    return code[start:i].strip()


def lua_roots_for(source: Path, ctx: ParseContext) -> list[Path]:
    """Search path Lua untuk sebuah file: folder `lua/` terdekat (gaya
    Neovim: ~/.config/nvim/lua) lalu root global (gaya Omarchy)."""
    local = [d / "lua" for d in source.parents if (d / "lua").is_dir() and d != ctx.home]
    return local[:1] + ctx.lua_roots


def resolve_lua_module(module: str, source: Path, ctx: ParseContext) -> tuple[str, bool]:
    """Cari file untuk require("a.b"). Return (path, ditemukan_di_root_lokal)."""
    rel = module.replace(".", "/")
    top = rel.split("/")[0]
    roots = lua_roots_for(source, ctx)
    for root in roots:
        for candidate in (root / f"{rel}.lua", root / rel / "init.lua"):
            if candidate.is_file():
                return str(candidate), True
    # Tidak ketemu. Jika folder induknya ada di salah satu root, berarti
    # modul lokal yang hilang (broken link). Jika tidak, kemungkinan library
    # Lua eksternal (misal plugin Neovim) -> tandai unresolved.
    for root in roots:
        if (root / top).is_dir():
            return str(root / f"{rel}.lua"), True
    return module, False


def parse_lua(path: Path, text: str, ctx: ParseContext) -> list[Dependency]:
    code = strip_lua_comments(text)
    deps: list[Dependency] = []

    for m in RE_LUA_REQUIRE.finditer(code):
        line = line_of(code, m.start())
        target, local = resolve_lua_module(m["s"], path, ctx)
        deps.append(Dependency(DepKind.FILE, target, path, line, "require", m["s"], unresolved=not local))

    variables: dict[str, str] = {}
    for m in RE_LUA_ASSIGN.finditer(code):
        value = eval_lua_expr(m["expr"], variables, ctx)
        if value is not None:
            variables[m["name"]] = value

    for m in RE_LUA_DOFILE.finditer(code):
        line = line_of(code, m.start())
        value = eval_lua_expr(m["expr"], variables, ctx)
        if value is None:
            deps.append(Dependency(DepKind.FILE, m["expr"], path, line, "dofile", m["expr"], unresolved=True))
        else:
            deps += _file_deps(value, path, line, "dofile", ctx)

    calls = [(m, m["fn"]) for m in RE_LUA_EXEC_CALL.finditer(code)]
    calls += [(m, "bind") for m in RE_LUA_BIND.finditer(code)]
    for m, fn in calls:
        line = line_of(code, m.start())
        arg = lua_call_arg(code, m.end())
        if not arg or RE_LUA_EXEC_CALL.search(arg) or (fn == "bind" and not arg[0] in "\"'" and arg not in variables):
            continue  # exec_cmd(o.launch("x")) sudah dihitung; bind ke dispatcher hl.dsp.*
        value = eval_lua_expr(arg, variables, ctx)
        if value is None:
            # "perintah --opsi " .. var  -> nama program tetap bisa diambil
            lit = RE_LUA_STRING.match(arg)
            if lit and " " in lit["s"].strip():
                value = lit["s"]
        if value is None:
            deps.append(Dependency(DepKind.BINARY, arg, path, line, fn, arg, unresolved=True))
        else:
            deps += _binary_deps(value, path, line, fn, ctx)

    for m in RE_LUA_EXEC_TABLE.finditer(code):
        for s in RE_LUA_STRING.finditer(m["body"]):
            line = line_of(code, m.start("body") + s.start())
            deps += _binary_deps(s["s"], path, line, "exec_once", ctx)
    return deps


def parse_waybar(path: Path, text: str, ctx: ParseContext) -> list[Dependency]:
    # Hapus komentar // tapi jangan sentuh "//" di dalam string (mis. URL).
    code = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m[1] or "", text)
    deps: list[Dependency] = []
    for m in RE_WAYBAR_INCLUDE.finditer(code):
        for s in RE_JSON_STRING.finditer(m["value"]):
            line = line_of(code, m.start("value") + s.start())
            deps += _file_deps(s[1], path, line, "include", ctx)
    for m in RE_WAYBAR_EXEC.finditer(code):
        cmd = m["cmd"].encode().decode("unicode_escape", errors="ignore")
        if cmd.strip() in WAYBAR_ACTIONS:
            continue  # "on-click": "activate" = aksi modul, bukan program
        deps += _binary_deps(cmd, path, line_of(code, m.start()), m["key"], ctx)
    return deps


def parse_css(path: Path, text: str, ctx: ParseContext) -> list[Dependency]:
    code = re.sub(r"/\*.*?\*/", lambda m: "\n" * m[0].count("\n"), text, flags=re.DOTALL)
    deps: list[Dependency] = []
    for m in RE_CSS_IMPORT.finditer(code):
        line = line_of(code, m.start())
        found = _file_deps(m["path"], path, line, "@import", ctx)
        alt_base = _app_root(path, ctx)
        if (alt_base and not m["path"].startswith(("/", "~")) and len(found) == 1
                and not found[0].unresolved and not Path(found[0].target).exists()):
            # Tema sering disimpan di subfolder (waybar/style/x.css) tapi
            # dipakai lewat symlink ke waybar/style.css, sehingga path relatif
            # dihitung dari folder app. Pakai itu jika filenya ada di sana.
            alt = _file_deps(m["path"], alt_base / path.name, line, "@import", ctx)
            if len(alt) == 1 and Path(alt[0].target).exists():
                found = alt
        deps += found
    return deps


def _app_root(path: Path, ctx: ParseContext) -> Path | None:
    """Folder app (~/.config/<app>) tempat sebuah file berada."""
    try:
        rel = path.relative_to(ctx.config_dir)
    except ValueError:
        return None
    return ctx.config_dir / rel.parts[0] if len(rel.parts) > 2 else None


PARSERS = {
    "hypr": parse_hypr,
    "lua": parse_lua,
    "waybar": parse_waybar,
    "css": parse_css,
    "generic": parse_generic,
}


def parse_file(path: Path, ctx: ParseContext) -> list[Dependency]:
    fmt = detect_format(path)
    if fmt is None:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return PARSERS[fmt](path, text, ctx)
