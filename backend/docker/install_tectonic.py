"""Install the pinned Tectonic binary (LaTeX compiler for tailored CVs) into /usr/local/bin.

Used by backend/Dockerfile during the image build. The archive is accepted only if its SHA-256 equals the digest pinned
for its architecture. The digests are the ones GitHub publishes for each release asset. A different digest means the
download was altered or the pinned version changed, and the build stops.

Environment (set by the Dockerfile): TARGETARCH (amd64 or arm64), TECTONIC_VERSION, TECTONIC_SHA256_AMD64,
TECTONIC_SHA256_ARM64.
"""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

RELEASE_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    "tectonic%40{version}/tectonic-{version}-{triple}.tar.gz"
)
TRIPLES = {"amd64": "x86_64-unknown-linux-musl", "arm64": "aarch64-unknown-linux-musl"}
BINARY_NAME = "tectonic"
DESTINATION = Path("/usr/local/bin") / BINARY_NAME
DOWNLOAD_TIMEOUT_SECONDS = 120


def fail(message: str) -> None:
    sys.stderr.write(f"install_tectonic: {message}\n")
    sys.exit(1)


def main() -> None:
    architecture = os.environ.get("TARGETARCH", "")
    if architecture not in TRIPLES:
        fail(f"unsupported architecture {architecture!r}: Tectonic is pinned for amd64 and arm64 only")
    version = os.environ["TECTONIC_VERSION"]
    expected = os.environ[f"TECTONIC_SHA256_{architecture.upper()}"]
    url = RELEASE_URL.format(version=version, triple=TRIPLES[architecture])

    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # fixed https URL, pinned
        archive = response.read()
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        fail(f"checksum mismatch for Tectonic {version} ({architecture}): expected {expected}, got {actual}")

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember(BINARY_NAME)  # the release holds one binary at the root; nothing else is extracted
        content = tar.extractfile(member)
        if content is None or not member.isfile():
            fail("the archive does not contain a regular tectonic file")
        DESTINATION.write_bytes(content.read())
    DESTINATION.chmod(0o755)

    check = subprocess.run([str(DESTINATION), "--version"], capture_output=True, text=True, check=False)
    if check.returncode != 0:
        fail(f"installed binary does not run: {check.stderr.strip()}")
    print(f"installed {check.stdout.strip()} at {DESTINATION}")


if __name__ == "__main__":
    main()
