import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from movbench.cli import main
from movbench.server import make_handler
from movbench.submission import load_submission


def test_bad_manifest_reports_an_input_error(tmp_path):
    (tmp_path / "submission.json").write_text('{"schema_version":1,"model":"broken"}')
    assert main(["validate", str(tmp_path)]) == 2


def test_import_rejects_incomplete_measurements(tmp_path):
    file = tmp_path / "partial.json"
    file.write_text('{"schema_version":1,"complete":false}')
    assert main(["import-run", str(file), "--db", str(tmp_path / "scores.sqlite3")]) == 2


def test_compiler_command_contract(tmp_path):
    import sys
    from pathlib import Path

    adapter = Path(__file__).resolve().parents[1] / "examples/compiler_adapter.py"
    result = main(
        [
            "generate",
            "--model",
            "adapter",
            "--out",
            str(tmp_path / "submission"),
            "--tasks",
            "route_64",
            "--tiers",
            "tiny",
            "--compiler-command",
            f"{sys.executable} {adapter} {{spec}} {{output}}",
        ]
    )
    assert result == 0
    manifest = load_submission(tmp_path / "submission")
    assert manifest["metadata"]["backend"] == "compiler-command"
    assert main(["validate", str(tmp_path / "submission")]) == 0


def test_empty_server_api_and_static_files(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path / "scores.sqlite3"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/leaderboard") as response:
            assert json.load(response)["rows"] == []
        with urlopen(base + "/api/meta") as response:
            assert len(json.load(response)["tasks"]) == 9
        with urlopen(base) as response:
            assert "Computation, by relocation." in response.read().decode()
            assert response.headers["Content-Security-Policy"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
