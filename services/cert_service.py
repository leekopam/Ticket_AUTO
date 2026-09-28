"""LAN API 서버용 자체서명 인증서 관리 서비스.

인증서는 `.runtime/api_cert/`에 생성되며, 존재하면 재사용한다.
행사 중 재생성하면 지문이 바뀌어 모든 기기가 재페어링되므로 재사용이 원칙이다.
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from project_paths import resolve_project_path


CERT_DIR = ".runtime/api_cert"
_CERT_FILE = "server.crt"
_KEY_FILE = "server.key"
_CERT_VALID_DAYS = 370


@dataclass(frozen=True)
class ServerCertInfo:
    cert_path: str
    key_path: str
    sha256_fingerprint: str


def detect_lan_ips() -> list[str]:
    """로컬 LAN IPv4 후보를 수집한다 (실패 시 localhost만)."""
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def ensure_server_cert(
    cert_dir: str | None = None,
    extra_ips: list[str] | None = None,
) -> ServerCertInfo:
    """인증서가 있으면 재사용하고, 없으면 새로 생성한다."""
    directory = resolve_project_path(cert_dir or CERT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / _CERT_FILE
    key_path = directory / _KEY_FILE

    if not cert_path.exists() or not key_path.exists():
        _generate(cert_path, key_path, ips=detect_lan_ips() + (extra_ips or []))

    fingerprint = cert_sha256_fingerprint(cert_path)
    return ServerCertInfo(
        cert_path=str(cert_path),
        key_path=str(key_path),
        sha256_fingerprint=fingerprint,
    )


def remove_server_cert(cert_dir: str | None = None) -> None:
    """인증서·개인키를 삭제한다 — 다음 ensure_server_cert 호출 시 새로 생성된다."""
    directory = resolve_project_path(cert_dir or CERT_DIR)
    for name in (_CERT_FILE, _KEY_FILE):
        (directory / name).unlink(missing_ok=True)


def cert_sha256_fingerprint(cert_path: str | Path) -> str:
    """인증서 DER의 SHA-256 지문을 'AA:BB:...' 형식으로 반환한다."""
    data = Path(cert_path).read_bytes()
    cert = x509.load_pem_x509_certificate(data)
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)


def _generate(cert_path: Path, key_path: Path, ips: list[str]) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for ip in ips:
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue

    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "ticket-auto-pc"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ticket_AUTO"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_CERT_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
