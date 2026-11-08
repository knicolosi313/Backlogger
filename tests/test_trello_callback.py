"""Execute callback JavaScript with a fake browser and connection response."""

import json
import re
import shutil
import subprocess

import pytest

from services.api.connections import trello_callback


@pytest.mark.parametrize("status,expected", [
    (200, "Trello connected"),
    (400, "session is missing, expired"),
    (422, "invalid connection response"),
    (502, "could not validate"),
    (503, "configuration or database"),
    (504, "did not respond in time"),
    (500, "Check the backend logs"),
    (0, "Could not reach the backend"),
])
def test_callback_reports_failure_without_exposing_credentials(status, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to execute callback JavaScript")
    html = trello_callback().body.decode()
    script = re.search(r'<script nonce="[^"]+">(.*?)</script>', html, re.S).group(1)
    harness = """
const vm = require('node:vm');
const message = {textContent: ''};
let scrubbed = false;
let submitted = false;
const sandbox = {
    URLSearchParams,
    window: {
        location: {search: '?state=synthetic-state', hash: '#token=synthetic-secret', pathname: '/callback'},
        history: {replaceState() { scrubbed = true; }}
    },
    document: {getElementById() { return message; }},
    async fetch(url, options) {
        if (!scrubbed) throw new Error('Credentials were not scrubbed');
        const body = JSON.parse(options.body);
        submitted = body.state === 'synthetic-state' && body.token === 'synthetic-secret' && options.credentials === 'same-origin';
        if (STATUS === 0) throw new Error('Provider failure with synthetic-secret');
        return {ok: STATUS === 200, status: STATUS};
    }
};
vm.runInNewContext(SCRIPT, sandbox).then(() => {
    process.stdout.write(JSON.stringify({message: message.textContent, scrubbed, submitted}));
}).catch(error => { console.error(error); process.exitCode = 1; });
""".replace("STATUS", str(status)).replace("SCRIPT", json.dumps(script))
    result = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["scrubbed"] and outcome["submitted"]
    assert expected in outcome["message"]
    assert "synthetic-secret" not in outcome["message"]
    if status not in {0, 200}:
        assert f"HTTP {status}" in outcome["message"]
