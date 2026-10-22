"""Offline checks for buzz-engine. No network, no tokens.

    python3 -m unittest discover -s tests -v
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")


def run(script, *args, cwd, env=None):
    e = dict(os.environ, **(env or {}))
    for k in ("BUZZ_PROJECT", "BUZZ_CONFIG", "SLACK_BOT_TOKEN", "PH_API_TOKEN"):
        if not env or k not in env:
            e.pop(k, None)
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                          cwd=cwd, env=e, capture_output=True, text=True, timeout=60)


class Project(unittest.TestCase):
    """A throwaway user repo with the template config installed."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        os.makedirs(os.path.join(self.dir, "buzz"))
        shutil.copy(os.path.join(ROOT, "templates", "buzz.config.yaml"),
                    os.path.join(self.dir, "buzz", "config.yaml"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestPaths(Project):
    def test_resolves_to_user_project_not_plugin(self):
        sub = os.path.join(self.dir, "nested", "deeper")
        os.makedirs(sub)
        code = "import buzzpaths as b; print(b.REPO); print(b.CONFIG_PATH); print(b.output_dir())"
        out = subprocess.run([sys.executable, "-c", code], cwd=sub, capture_output=True,
                             text=True, env=dict(os.environ, PYTHONPATH=SCRIPTS)).stdout.split()
        self.assertEqual(out[0], self.dir)
        self.assertEqual(out[1], os.path.join(self.dir, "buzz", "config.yaml"))
        self.assertEqual(out[2], os.path.join(self.dir, "buzz", "working"))

    def test_missing_config_points_at_init(self):
        os.remove(os.path.join(self.dir, "buzz", "config.yaml"))
        r = run("buzz-scan.py", "--platform", "hn", "--peek", cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("/buzz-init", r.stdout + r.stderr)


class TestTemplate(unittest.TestCase):
    def test_template_has_every_block_the_scripts_read(self):
        import yaml
        cfg = yaml.safe_load(Path(ROOT, "templates", "buzz.config.yaml").read_text())
        for key in ("product", "slack", "schedule", "state", "hn", "reddit", "ph",
                    "subreddits", "keywords", "warmup", "accounts", "brand_monitor",
                    "lookback_hours", "output_dir", "state_dir"):
            self.assertIn(key, cfg)
        self.assertIn(cfg["state"]["commit"], ("none", "pr", "push"))


class TestPh(Project):
    def test_no_token_fails_cleanly(self):
        r = run("buzz-ph-scan.py", "--peek", cwd=self.dir)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PH_API_TOKEN", r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestSlack(Project):
    def test_needs_exactly_one_of_file_or_text(self):
        r = run("buzz-slack-upload.py", cwd=self.dir)
        self.assertIn("exactly one of --file or --text", r.stderr)

    def test_no_token_fails_cleanly(self):
        r = run("buzz-slack-upload.py", "--text", "hi", "--channel", "C1", cwd=self.dir)
        self.assertIn("Missing SLACK_BOT_TOKEN", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestDigest(Project):
    def test_renders_html_from_candidates_and_drafts(self):
        work = os.path.join(self.dir, "buzz", "working")
        os.makedirs(work)
        cand = {"scanned_at": "2026-09-29T08:00:00Z", "candidates": [
            {"id": "hn:1", "platform": "hn", "type": "story", "title": "Thread one",
             "url": "https://news.ycombinator.com/item?id=1", "points": 40,
             "num_comments": 3, "author": "a", "age_hours": 2},
            {"id": "hn:2", "platform": "hn", "type": "story", "title": "Thread two",
             "url": "https://news.ycombinator.com/item?id=2", "points": 90,
             "num_comments": 80, "author": "b", "age_hours": 20}]}
        with open(os.path.join(work, "candidates-2026-09-29.json"), "w") as f:
            json.dump(cand, f)
        drafts = {"judged": [
            {"ph_link": cand["candidates"][0]["url"], "action": "card", "relevance": 8,
             "why_relevant": "x", "comment_draft": "Draft text here.",
             "comment_label": "ready to post", "grounded_in": "thread + top replies"},
            {"ph_link": cand["candidates"][1]["url"], "action": "upvote", "relevance": 7}]}
        dpath = os.path.join(work, "drafts.json")
        with open(dpath, "w") as f:
            json.dump(drafts, f)
        r = run("buzz-digest-pdf.py", "--drafts", dpath, "--channel", "hn",
                "--html-only", "--out", os.path.join(work, "d.pdf"), cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        html = Path(os.path.join(work, "d.html")).read_text()
        self.assertNotIn("Draft text here.", html)
        self.assertIn("HN research checklist", html)
        # A relative --out must work too: Chromium can't load "file://buzz/working/x.html".
        r = run("buzz-digest-pdf.py", "--drafts", dpath, "--channel", "hn",
                "--html-only", "--out", "buzz/working/rel.pdf", cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(work, "rel.html")))

    def test_ph_checklist_takes_time_from_config(self):
        cfgp = os.path.join(self.dir, "buzz", "config.yaml")
        s = Path(cfgp).read_text().replace('fire_time_label: "right after the PH board resets"',
                                      'fire_time_label: "9:05 AM CET"')
        Path(cfgp).write_text(s)
        code = "import importlib.util as u;s=u.spec_from_file_location('d','%s');m=u.module_from_spec(s);s.loader.exec_module(m);print(m.checklist_for('ph'))" % os.path.join(SCRIPTS, "buzz-digest-pdf.py")
        out = subprocess.run([sys.executable, "-c", code], cwd=self.dir, capture_output=True,
                             text=True, env=dict(os.environ, PYTHONPATH=SCRIPTS))
        self.assertIn("9:05 AM CET", out.stdout, out.stderr)
        self.assertNotIn("{ph_time}", out.stdout)


class TestVendor(unittest.TestCase):
    """vendor.py exports Buzz into an interactive host without plugin loading."""

    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def vendor(self, *extra):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "vendor.py"),
                               "--into", self.dir, *extra],
                              capture_output=True, text=True, timeout=60)

    def test_refuses_direct_and_aliased_source_before_planning(self):
        with tempfile.TemporaryDirectory() as parent:
            source = os.path.join(parent, "source")
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            alias = os.path.join(parent, "alias")
            os.symlink(parent, alias)
            for target in (source, os.path.join(alias, "source")):
                for flags in ((), ("--force",), ("--dry-run",), ("--force", "--dry-run")):
                    with self.subTest(target=target, flags=flags):
                        result = subprocess.run([sys.executable, os.path.join(source, "scripts/vendor.py"),
                                                 "--into", target, *flags], capture_output=True,
                                                text=True, timeout=60)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn("refusing to vendor buzz-engine into itself", result.stderr)
            self.assertFalse(os.path.exists(os.path.join(source, ".claude/commands")))
            self.assertFalse(os.path.exists(os.path.join(source, ".claude/agents")))
            self.assertFalse(os.path.exists(os.path.join(source, "buzz")))

    def test_allows_parent_alias_above_distinct_project_root(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, "project")
            os.mkdir(project)
            alias = os.path.join(parent, "alias")
            os.symlink(parent, alias)
            target = os.path.join(alias, "project")
            self.assertEqual(run("vendor.py", "--into", target, "--dry-run", cwd=parent).returncode, 0)
            self.assertEqual(os.listdir(project), [])
            self.assertEqual(run("vendor.py", "--into", target, cwd=parent).returncode, 0)
            self.assertTrue(os.path.isfile(os.path.join(project, "buzz/engine/NOTICE")))

    def test_blank_unowned_output_is_preserved_without_partial_writes(self):
        path = os.path.join(self.dir, "buzz/engine/VERSION")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb"):
            pass
        result = self.vendor()
        self.assertNotEqual(result.returncode, 0)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"")
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".claude")))

    def test_missing_owned_output_is_restored(self):
        self.assertEqual(self.vendor().returncode, 0)
        path = os.path.join(self.dir, "buzz/engine/NOTICE")
        os.remove(path)
        self.assertEqual(self.vendor().returncode, 0)
        with open(path, "rb") as actual, open(os.path.join(ROOT, "NOTICE"), "rb") as expected:
            self.assertEqual(actual.read(), expected.read())

    def test_invalid_manifest_schema_requires_force(self):
        path = os.path.join(self.dir, "buzz/engine/.vendor-manifest.json")
        os.makedirs(os.path.dirname(path))
        original = '{"format":1,"files":[]}'
        with open(path, "w") as handle:
            handle.write(original)
        result = self.vendor()
        self.assertNotEqual(result.returncode, 0)
        with open(path) as handle:
            self.assertEqual(handle.read(), original)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".claude")))
        self.assertEqual(self.vendor("--force").returncode, 0)

    def test_writes_commands_agent_and_engine_with_paths_rewritten(self):
        r = self.vendor()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".claude/commands/buzz.md", ".claude/commands/buzz-daily.md",
                    ".claude/commands/buzz-init.md", ".claude/agents/buzz-humanizer.md",
                    "buzz/engine/scripts/buzz-scan.py", "buzz/engine/rubrics/hn.md",
                    "buzz/engine/templates/buzz.config.yaml", "buzz/engine/VERSION"):
            self.assertTrue(os.path.exists(os.path.join(self.dir, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "buzz/engine/scripts/vendor.py")))
        for dp, _, fs in os.walk(self.dir):
            for fn in fs:
                if fn.endswith((".md", ".py", ".yaml", ".yml")):
                    text = Path(os.path.join(dp, fn)).read_text(encoding="utf-8")
                    self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text, fn)
                    self.assertNotIn("buzz-engine:", text, fn)
        agent = Path(os.path.join(self.dir, ".claude/agents/buzz-humanizer.md")).read_text()
        self.assertIn("\nname: buzz-humanizer\n", agent)
        buzz = Path(os.path.join(self.dir, ".claude/commands/buzz.md")).read_text()
        self.assertTrue(buzz.startswith("---\n"), "front matter must stay first")
        self.assertIn("buzz/engine/scripts/buzz-scan.py", buzz)

    def test_rerun_updates_but_never_clobbers_a_foreign_file(self):
        self.assertEqual(self.vendor().returncode, 0)
        self.assertEqual(self.vendor().returncode, 0)  # its own files: fine
        with open(os.path.join(self.dir, ".claude/commands/buzz.md"), "w") as f:
            f.write("somebody else's command")
        r = self.vendor()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".claude/commands/buzz.md", r.stdout + r.stderr)
        self.assertEqual(self.vendor("--force").returncode, 0)

    def test_rejects_symlink_ancestors_and_leaves_before_any_write(self):
        paths = (".claude", ".claude/commands", ".claude/commands/buzz.md",
                 "buzz", "buzz/engine", "buzz/engine/scripts",
                 "buzz/engine/scripts/buzz-scan.py", "buzz/engine/VERSION",
                 "buzz/engine/LICENSE", "buzz/engine/NOTICE",
                 "buzz/engine/.vendor-manifest.json")
        for rel in paths:
            for force in (False, True):
                with self.subTest(path=rel, force=force), tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as outside:
                    link = os.path.join(project, rel)
                    os.makedirs(os.path.dirname(link), exist_ok=True)
                    sentinel = os.path.join(outside, "sentinel")
                    with open(sentinel, "w") as handle:
                        handle.write("external data")
                    is_dir = rel in (".claude", ".claude/commands", "buzz", "buzz/engine", "buzz/engine/scripts")
                    os.symlink(outside if is_dir else sentinel, link)
                    result = run("vendor.py", "--into", project, *(["--force"] if force else []), cwd=project)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("symlink", result.stderr.lower())
                    self.assertEqual(os.listdir(outside), ["sentinel"])
                    with open(sentinel) as handle:
                        self.assertEqual(handle.read(), "external data")
                    # The first command in the plan must not be written before a later conflict.
                    first = os.path.join(project, ".claude/commands/buzz.md")
                    if not rel.startswith(".claude"):
                        self.assertFalse(os.path.exists(first))

    def test_rejects_dangling_symlink_and_linked_project_root(self):
        with tempfile.TemporaryDirectory() as outside:
            os.symlink(os.path.join(outside, "missing"), os.path.join(self.dir, "buzz"))
            result = self.vendor("--force")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(os.listdir(outside), [])
        with tempfile.TemporaryDirectory() as parent, tempfile.TemporaryDirectory() as outside:
            link = os.path.join(parent, "project")
            os.symlink(outside, link)
            result = run("vendor.py", "--into", link, cwd=parent)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(os.listdir(outside), [])

    def test_all_unowned_outputs_require_force_without_partial_writes(self):
        for rel in ("buzz/engine/scripts/buzz-scan.py", "buzz/engine/rubrics/hn.md",
                    "buzz/engine/VERSION", "buzz/engine/LICENSE", "buzz/engine/NOTICE",
                    "buzz/engine/.vendor-manifest.json"):
            with self.subTest(path=rel), tempfile.TemporaryDirectory() as project:
                path = os.path.join(project, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as handle:
                    handle.write("user-owned data")
                result = run("vendor.py", "--into", project, cwd=project)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                with open(path) as handle:
                    self.assertEqual(handle.read(), "user-owned data")
                self.assertFalse(os.path.exists(os.path.join(project, ".claude/commands/buzz.md")))
                self.assertEqual(run("vendor.py", "--into", project, "--force", cwd=project).returncode, 0)

    def test_edited_owned_outputs_require_force(self):
        self.assertEqual(self.vendor().returncode, 0)
        for rel in (".claude/commands/buzz.md", "buzz/engine/scripts/buzz-scan.py",
                    "buzz/engine/LICENSE", "buzz/engine/VERSION"):
            with self.subTest(path=rel):
                path = os.path.join(self.dir, rel)
                with open(path, "a") as handle:
                    handle.write("\nuser edit\n")
                with open(path, "rb") as handle:
                    edited = handle.read()
                self.assertNotEqual(self.vendor().returncode, 0)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), edited)
                self.assertEqual(self.vendor("--force").returncode, 0)

    def test_vendored_notices_are_exact_and_owned(self):
        self.assertEqual(self.vendor().returncode, 0)
        for name in ("LICENSE", "NOTICE"):
            with open(os.path.join(ROOT, name), "rb") as handle:
                expected = handle.read()
            with open(os.path.join(self.dir, "buzz/engine", name), "rb") as handle:
                self.assertEqual(handle.read(), expected)
        manifest_path = os.path.join(self.dir, "buzz/engine/.vendor-manifest.json")
        with open(manifest_path) as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["format"], 1)
        for rel, digest in manifest["files"].items():
            with open(os.path.join(self.dir, rel), "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), digest)
        self.assertEqual(self.vendor().returncode, 0)

    def test_force_replaces_hardlinked_output_without_changing_other_link(self):
        with tempfile.TemporaryDirectory() as outside:
            sentinel = os.path.join(outside, "sentinel")
            with open(sentinel, "w") as handle:
                handle.write("external data")
            os.chmod(sentinel, 0o640)
            target = os.path.join(self.dir, "buzz/engine/scripts/buzz-scan.py")
            os.makedirs(os.path.dirname(target))
            os.link(sentinel, target)
            self.assertNotEqual(self.vendor().returncode, 0)
            self.assertEqual(self.vendor("--force").returncode, 0)
            with open(sentinel) as handle:
                self.assertEqual(handle.read(), "external data")
            self.assertFalse(os.path.samefile(sentinel, target))
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o640)
            mask = os.umask(0)
            os.umask(mask)
            self.assertEqual(os.stat(os.path.join(self.dir, "buzz/engine/LICENSE")).st_mode & 0o777,
                             0o666 & ~mask)

    def test_failed_atomic_replace_preserves_original_and_removes_temporary_file(self):
        spec = importlib.util.spec_from_file_location("vendor", os.path.join(SCRIPTS, "vendor.py"))
        vendor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vendor)
        target = os.path.join(self.dir, "original")
        with open(target, "wb") as handle:
            handle.write(b"original data")
        with mock.patch.object(vendor.os, "replace", side_effect=PermissionError("synthetic failure")):
            with self.assertRaises(PermissionError):
                vendor.write_output(self.dir, target, b"replacement")
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"original data")
        self.assertEqual(os.listdir(self.dir), ["original"])

    def test_legacy_install_migrates_only_unchanged_files(self):
        self.assertEqual(self.vendor().returncode, 0)
        manifest_path = os.path.join(self.dir, "buzz/engine/.vendor-manifest.json")
        os.remove(manifest_path)
        self.assertEqual(self.vendor().returncode, 0)
        os.remove(manifest_path)
        path = os.path.join(self.dir, ".claude/commands/buzz.md")
        with open(path, "a") as handle:
            handle.write("\nlegacy user edit\n")
        self.assertNotEqual(self.vendor().returncode, 0)
        self.assertFalse(os.path.exists(manifest_path))
        self.assertEqual(self.vendor("--force").returncode, 0)

    def test_dry_run_has_no_writes_and_still_checks_conflicts(self):
        self.assertEqual(self.vendor("--dry-run").returncode, 0)
        self.assertEqual(os.listdir(self.dir), [])
        path = os.path.join(self.dir, "buzz/engine/LICENSE")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("unowned license")
        self.assertNotEqual(self.vendor("--dry-run").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.dir, ".claude")))

    def test_unchanged_owned_files_can_update(self):
        self.assertEqual(self.vendor().returncode, 0)
        rel = "buzz/engine/scripts/buzz-scan.py"
        path = os.path.join(self.dir, rel)
        old = b"previous version installed by vendor.py\n"
        with open(path, "wb") as handle:
            handle.write(old)
        manifest_path = os.path.join(self.dir, "buzz/engine/.vendor-manifest.json")
        with open(manifest_path) as handle:
            manifest = json.load(handle)
        manifest["files"][rel] = hashlib.sha256(old).hexdigest()
        with open(manifest_path, "w") as handle:
            json.dump(manifest, handle)
        self.assertEqual(self.vendor().returncode, 0)
        with open(path, "rb") as handle:
            self.assertNotEqual(handle.read(), old)

    def test_vendored_scripts_run_from_the_repo(self):
        self.assertEqual(self.vendor().returncode, 0)
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        r = subprocess.run([sys.executable, "buzz/engine/scripts/buzz-scan.py", "--peek"],
                           cwd=self.dir, capture_output=True, text=True, timeout=60)
        self.assertIn("/buzz-init", r.stdout + r.stderr)  # no config yet: points at init


class TestCodexInstall(unittest.TestCase):
    def test_links_the_original_skill_and_commands_without_copying(self):
        with tempfile.TemporaryDirectory() as project:
            r = run("install-codex.py", "--into", project, "--dry-run", cwd=project)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(project, ".agents")))
            r = run("install-codex.py", "--into", project, cwd=project)
            self.assertEqual(r.returncode, 0, r.stderr)
            link = os.path.join(project, ".agents", "skills", "buzz-engine")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.path.realpath(link), ROOT)
            for path in ("SKILL.md", "commands/buzz.md", "agents/humanizer.md", "rubrics/hn.md"):
                self.assertTrue(os.path.samefile(os.path.join(link, path), os.path.join(ROOT, path)))
            self.assertEqual(run("install-codex.py", "--into", project, cwd=project).returncode, 0)

    def test_preserves_existing_skill_and_rejects_symlink_parents(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as outside:
            parent = os.path.join(project, ".agents", "skills")
            os.makedirs(parent)
            skill = os.path.join(parent, "buzz-engine")
            os.makedirs(skill)
            r = run("install-codex.py", "--into", project, cwd=project)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(os.path.islink(skill))
            shutil.rmtree(os.path.join(project, ".agents"))
            os.symlink(outside, os.path.join(project, ".agents"))
            r = run("install-codex.py", "--into", project, cwd=project)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(os.listdir(outside), [])

class TestFrontmatter(unittest.TestCase):
    """Every command and agent must open with strict YAML frontmatter on line 1."""

    def test_commands_and_agents_parse(self):
        import yaml
        for d, required in (("commands", ("description",)), ("agents", ("name", "description"))):
            for fn in sorted(os.listdir(os.path.join(ROOT, d))):
                if not fn.endswith(".md"):
                    continue
                text = Path(os.path.join(ROOT, d, fn)).read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), "%s/%s: frontmatter not on line 1" % (d, fn))
                meta = yaml.safe_load(text.split("\n---\n", 1)[0][4:])
                for key in required:
                    self.assertTrue(meta.get(key), "%s/%s: missing %s" % (d, fn, key))

    def test_manifest_version_matches_marketplace(self):
        plugin = json.loads(Path(ROOT, ".claude-plugin", "plugin.json").read_text())
        market = json.loads(Path(ROOT, ".claude-plugin", "marketplace.json").read_text())
        self.assertEqual(plugin["version"], market["plugins"][0]["version"])

    def test_codex_skill_has_valid_metadata_and_resolvable_references(self):
        import yaml
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("---\n"))
        meta = yaml.safe_load(text.split("\n---\n", 1)[0][4:])
        self.assertEqual(meta["name"], "buzz-engine")
        self.assertTrue(meta["description"])
        for path in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), path)


if __name__ == "__main__":
    unittest.main()
