from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_copies_every_runtime_package_used_by_main():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for package in ("app", "analytics", "alerts", "collector", "config", "database", "signal_engine", "utils", "static"):
        assert f"COPY {package} ./{package}" in dockerfile


def test_render_uses_single_worker_and_health_check():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "--workers 1" in render
    assert "healthCheckPath: /api/health" in render
