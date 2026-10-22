#!/usr/bin/env python3
"""Deliver only validated digest PDFs on a fresh, non-LLM runner.

The checkout's trusted config supplies the channel. The artifact supplies PDF
bytes only, never commands, Python modules, destinations or uploader arguments.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

DIGEST_NAME = re.compile(r'(ph|hn|reddit|buzz)-digest-\d{2}-\d{2}-\d{4}\.pdf\Z')
MAX_BYTES = 50 * 1024 * 1024


def digest_files(root):
    """Fail closed before contacting Slack if any artifact entry is unexpected."""
    output = root / 'buzz/working'
    if output.is_symlink() or not output.is_dir():
        raise ValueError('Expected a regular buzz/working directory')
    paths = sorted(output.iterdir())
    if not 1 <= len(paths) <= 4:
        raise ValueError('Expected between one and four digest PDFs')
    for path in paths:
        if not DIGEST_NAME.fullmatch(path.name):
            raise ValueError('Unexpected digest artifact filename')
        if path.is_symlink() or not path.is_file():
            raise ValueError('Digest must be a regular file')
        if not 5 <= path.stat().st_size <= MAX_BYTES:
            raise ValueError('Digest PDF size outside allowed range')
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                raise ValueError('Digest does not have a PDF header')
    return paths


def main():
    root = Path.cwd().resolve()
    paths = digest_files(root)
    config = yaml.safe_load((root / 'buzz/config.yaml').read_text())
    channel = config.get('slack', {}).get('channel_id', '')
    if not isinstance(channel, str) or not re.fullmatch(r'[CG][A-Z0-9]{8,}', channel):
        raise ValueError('Trusted config must specify a Slack channel ID')
    uploader = Path(__file__).resolve().with_name('buzz-slack-upload.py')
    # Do not import any Python or inspect metadata supplied by the artifact.
    # The model's fixed output directory must not depend on a custom local config.
    with tempfile.TemporaryDirectory(prefix='buzz-delivery-') as directory:
        config_path = Path(directory) / 'config.json'
        config_path.write_text(json.dumps({'output_dir': 'buzz/working',
                                           'slack': {'channel_id': channel}}))
        env = dict(os.environ, BUZZ_PROJECT=str(root), BUZZ_CONFIG=str(config_path))
        for path in paths:
            subprocess.run([sys.executable, str(uploader), '--file', str(path),
                            '--channel', channel], check=True, env=env)



if __name__ == '__main__':
    main()
