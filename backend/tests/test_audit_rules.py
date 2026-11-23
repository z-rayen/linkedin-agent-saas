"""The secret and personal-data audit: each rule finds what it is for, ignores the placeholders the project uses, and the
tracked files are clean. The audit script lives in scripts/ and is loaded from there."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("audit_tracked", ROOT / "scripts" / "audit_tracked.py")
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit  # dataclasses look the module up by name while it is being defined
SPEC.loader.exec_module(audit)


def _rules(text: str) -> list[str]:
    return [finding.rule for finding in audit.scan_text("sample.txt", text)]


def test_real_looking_keys_are_found() -> None:
    assert "groq-key" in _rules("GROQ_API_KEY=gsk_" + "a" * 24)
    assert "github-token" in _rules("token ghp_" + "b" * 24)
    assert "private-key-block" in _rules("-----BEGIN RSA PRIVATE KEY-----")


def test_a_personal_address_is_found_but_documentation_domains_are_not() -> None:
    assert "personal-email" in _rules("write to someone.real@employer-corp.io please")
    assert _rules("write to jane.doe@example.com or ops@example.org") == []
    assert _rules("address jobs@acme.test is a reserved name") == []


def test_a_provider_address_on_a_placeholder_list_is_not_reported() -> None:
    assert _rules("support@sentry.io") == []
    assert "personal-email" in _rules("contact me at a.person@employer-corp.io")


def test_absolute_local_paths_are_found() -> None:
    assert "absolute-windows-path" in _rules(r"open C:\Users\someone\notes.txt")
    assert "absolute-unix-home" in _rules("cat /home/someone/.ssh/config")


def test_image_digests_and_published_checksums_are_not_secrets() -> None:
    digest = "9" * 10 + "a" * 30
    assert _rules(f"FROM python@sha256:{digest}") == []
    assert _rules(f"TECTONIC_SHA256_AMD64={digest}") == []


def test_a_model_name_with_hyphens_is_not_a_base64_secret() -> None:
    assert _rules("EMBEDDING: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2") == []


def test_the_tracked_files_of_the_repository_have_no_finding() -> None:
    findings = audit.scan_repository()
    assert [f"{f.path}:{f.line} {f.rule}" for f in findings] == []
