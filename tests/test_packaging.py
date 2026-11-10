"""The Docker image and server requirements carry what the deployed system needs (fixes made on the server
are kept here, so the next update does not need them again)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_docker_image_has_libreoffice_writer_and_calc():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "libreoffice-writer-nogui" in text  # summons and envelopes: Word -> PDF
    assert "libreoffice-calc-nogui" in text  # status report: Excel -> PDF


def test_celery_broker_packages_are_pinned_together():
    reqs = (ROOT / "requirements-server.txt").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^([\w\[\].-]+)==([\d.]+)", reqs, flags=re.M))
    assert {"celery", "kombu[redis]", "redis"} <= set(pins)
    # kombu's redis extra accepts redis 8.x from kombu 5.7 on (5.6 allows only redis < 6.5)
    kombu = tuple(int(x) for x in pins["kombu[redis]"].split(".")[:2])
    redis_major = int(pins["redis"].split(".")[0])
    assert redis_major < 6 or kombu >= (5, 7)
