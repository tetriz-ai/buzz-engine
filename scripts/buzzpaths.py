"""Where buzz-engine finds the user's files.

The scripts ship inside the plugin; everything they read or write belongs to the
user's project. So paths resolve against the PROJECT, never against this file:

  project root   $BUZZ_PROJECT, else the git toplevel of the cwd, else the cwd
  config         $BUZZ_CONFIG,  else <root>/buzz/config.yaml
  secrets        <root>/buzz/.env   (gitignored; env vars win over nothing, see load_env)
  output_dir / state_dir from the config are relative to the project root.
"""
import os
import subprocess


def project_root():
    env = os.environ.get("BUZZ_PROJECT")
    if env:
        return os.path.abspath(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if top.returncode == 0 and top.stdout.strip():
            return top.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.getcwd()


REPO = project_root()
CONFIG_PATH = os.environ.get("BUZZ_CONFIG") or os.path.join(REPO, "buzz", "config.yaml")
ENV_PATH = os.path.join(REPO, "buzz", ".env")


def require_config(path=None):
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        raise SystemExit(
            "No buzz config at %s\n"
            "  → run /buzz-init in this project, or copy the plugin's\n"
            "    templates/buzz.config.yaml to buzz/config.yaml and fill it in." % path)
    return path


def output_dir():
    """output_dir from the config, without making PyYAML a hard dependency
    (the PDF renderer is deliberately dependency-free)."""
    rel = "buzz/working"
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            rel = (yaml.safe_load(f) or {}).get("output_dir") or rel
    except Exception:
        pass
    return os.path.join(REPO, rel)
