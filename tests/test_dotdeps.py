"""Unit test: python3 -m unittest discover -s tests -v"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph import DependencyGraph  # noqa: E402
from models import DepKind, Status  # noqa: E402
from parser import (ParseContext, eval_lua_expr, extract_binaries,  # noqa: E402
                    parse_hypr, parse_lua, parse_waybar, strip_lua_comments)
from validator import validate  # noqa: E402


class TestExtractBinaries(unittest.TestCase):
    def test_wrappers_and_operators(self):
        cases = {
            "uwsm app -- nm-applet --indicator": ["nm-applet"],
            "swww-daemon && swww img a.png": ["swww-daemon", "swww"],
            'sh -c "sleep 2 && dunst"': ["sh", "sleep", "dunst"],
            "[workspace 2 silent] firefox": ["firefox"],
            "env GDK_BACKEND=x11 steam": ["steam"],
            'grim -g "$(slurp)" - | wl-copy': ["grim", "wl-copy", "slurp"],
            "echo hai": [],
            "waybar > /dev/null 2>&1 &": ["waybar"],
            "cat < in.txt | sort": ["cat", "sort"],
        }
        for cmd, expected in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(extract_binaries(cmd), expected)


class TestParsers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.ctx = ParseContext(self.home, self.home / ".config")
        self.src = self.home / ".config/hypr/hyprland.conf"

    def tearDown(self):
        self.tmp.cleanup()

    def test_hypr_variables_and_comments(self):
        text = "$term = kitty\n# exec-once = komentar\nsource = ~/.config/hypr/a.conf # x\nbind = SUPER, Q, exec, $term\n"
        deps = parse_hypr(self.src, text, self.ctx)
        self.assertEqual([(d.kind, d.target) for d in deps], [
            (DepKind.FILE, str(self.home / ".config/hypr/a.conf")),
            (DepKind.BINARY, "kitty"),
        ])
        self.assertEqual(deps[1].line, 4)

    def test_lua_comments_keep_strings(self):
        code = 'exec_cmd("foo --bar") -- komentar\n--[[ blok\nexec_cmd("x") ]]\n'
        self.assertNotIn("komentar", strip_lua_comments(code))
        self.assertIn("--bar", strip_lua_comments(code))
        self.assertEqual(strip_lua_comments(code).count("\n"), 3)

    def test_lua_expr(self):
        self.assertEqual(eval_lua_expr('a .. "/x"', {"a": "/home"}), "/home/x")
        self.assertIsNone(eval_lua_expr("value.launch"))

    def test_lua_exec_and_bind(self):
        text = ('local bin = "/opt/tool"\n'
                'exec_once = {\n  "waybar",\n}\n'
                'o.bind("SUPER + K", "Keys", "omarchy-menu")\n'
                'o.bind("SUPER + W", "Close", hl.dsp.window.close())\n'
                'exec_cmd(bin .. " --run")\n')
        deps = parse_lua(self.home / "x.lua", text, self.ctx)
        self.assertEqual([d.target for d in deps if not d.unresolved],
                         ["/opt/tool", "omarchy-menu", "waybar"])

    def test_waybar(self):
        text = '{ // komentar\n "include": ["~/a.jsonc"],\n "x": {"on-click": "pavucontrol", "url": "http://a"}}'
        deps = parse_waybar(self.home / "config.jsonc", text, self.ctx)
        self.assertEqual([d.target for d in deps], [str(self.home / "a.jsonc"), "pavucontrol"])


class TestRegressions(unittest.TestCase):
    """Kasus yang ditemukan saat menguji repo dotfiles sungguhan."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.ctx = ParseContext(self.home, self.home / ".config")

    def tearDown(self):
        self.tmp.cleanup()

    def test_hypr_subfolder_and_bindd(self):
        from parser import detect_format
        path = self.home / ".config/hypr/configs/Keybinds.conf"
        self.assertEqual(detect_format(path), "hypr")
        deps = parse_hypr(path, "bindd = SUPER, D, app launcher, exec, rofi -show drun\n", self.ctx)
        self.assertEqual([d.target for d in deps], ["rofi"])

    def test_home_variable_in_binary(self):
        deps = parse_hypr(self.home / "h.conf", "exec-once = $HOME/.config/hypr/boot.sh\n", self.ctx)
        self.assertFalse(deps[0].unresolved)
        self.assertEqual(deps[0].target, str(self.home / ".config/hypr/boot.sh"))

    def test_lua_getenv_uses_scanned_home(self):
        self.assertEqual(eval_lua_expr('os.getenv("HOME") .. "/x"', None, self.ctx), f"{self.home}/x")

    def test_waybar_module_actions_are_not_programs(self):
        deps = parse_waybar(self.home / "config.jsonc", '{"on-click": "activate", "on-scroll-up": "shift_up"}', self.ctx)
        self.assertEqual(deps, [])

    def test_github_repo_spec(self):
        from github import parse_repo
        self.assertEqual(parse_repo("user/dots"), ("user", "dots", "HEAD"))
        self.assertEqual(parse_repo("https://github.com/user/dots.git"), ("user", "dots", "HEAD"))
        self.assertEqual(parse_repo("github.com/user/dots/tree/dev"), ("user", "dots", "dev"))


class TestGithubLayout(unittest.TestCase):
    """Deteksi folder ~/.config di berbagai struktur repo dotfiles."""

    def detect(self, dirs):
        from github import detect_layout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for d in dirs:
                (root / d).mkdir(parents=True)
            config_root, apps = detect_layout(root)
            self.last_paths = {k: str(v.relative_to(root)) for k, v in apps.items()}
            return str(config_root.relative_to(root)), sorted(apps)

    def test_layouts(self):
        self.assertEqual(self.detect(["config/hypr", "config/waybar", "assets"]),
                         ("config", ["hypr", "waybar"]))
        self.assertEqual(self.detect(["dotfiles/.config/hypr", "dotfiles/.config/kitty", "README"]),
                         ("dotfiles/.config", ["hypr", "kitty"]))
        self.assertEqual(self.detect(["hypr", "waybar", "wallpapers"]),
                         (".", ["hypr", "wallpapers", "waybar"]))

    def test_stow_layout_merged(self):
        self.detect(["hypr/.config/hypr", "waybar/.config/waybar"])
        self.assertEqual(self.last_paths, {"hypr": "hypr/.config/hypr", "waybar": "waybar/.config/waybar"})


class TestGraphAndValidator(unittest.TestCase):
    def test_broken_missing_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            hypr = home / ".config/hypr"
            hypr.mkdir(parents=True)
            (hypr / "hyprland.conf").write_text(
                "source = ~/.config/hypr/b.conf\nsource = ~/.config/hypr/hilang.conf\n")
            (hypr / "b.conf").write_text(
                "source = ~/.config/hypr/hyprland.conf\nexec-once = program-yang-tidak-ada\nexec-once = sh\n")

            ctx = ParseContext(home, home / ".config")
            graph = DependencyGraph.build([hypr / "hyprland.conf"], ctx)
            result = validate(graph)

            self.assertEqual(len(result.by_status(Status.BROKEN_LINK)), 1)
            self.assertEqual(result.by_status(Status.MISSING_PACKAGE)[0].dependency.target,
                             "program-yang-tidak-ada")
            self.assertEqual(len(result.by_status(Status.CYCLE)), 1)
            self.assertEqual(len(graph.parsed_files), 2)  # BFS mengikuti source

            chain = graph.paths_to("bin:sh")[0]
            self.assertEqual(chain[0], f"file:{hypr / 'hyprland.conf'}")


if __name__ == "__main__":
    unittest.main()
