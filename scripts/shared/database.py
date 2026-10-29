import json
import secrets
import subprocess
import tarfile
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from datetime import UTC, datetime, timedelta
from pathlib import Path
from scripts.shared.configuration import ROOT, load_model_environment


def create_database_material() -> dict[str, str]:
    authority_key = ec.generate_private_key(ec.SECP384R1())
    server_key = ec.generate_private_key(ec.SECP384R1())
    authority_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Worldcup private Bolt CA")]
    )
    server_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "neo4j.worldcup.internal")]
    )
    beginning = datetime.now(UTC) - timedelta(minutes=5)
    expiry = beginning + timedelta(days=365)
    authority = (
        x509.CertificateBuilder()
        .subject_name(authority_name)
        .issuer_name(authority_name)
        .public_key(authority_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(beginning)
        .not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(authority_key, hashes.SHA384())
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(authority_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(beginning)
        .not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("neo4j.worldcup.internal")]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(authority_key, hashes.SHA384())
    )
    return {
        "bootstrap_password": secrets.token_urlsafe(32),
        "reader_password": secrets.token_urlsafe(32),
        "writer_password": secrets.token_urlsafe(32),
        "ca_certificate": authority.public_bytes(serialization.Encoding.PEM).decode(),
        "server_certificate": certificate.public_bytes(
            serialization.Encoding.PEM
        ).decode(),
        "ca_private_key": authority_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        "server_private_key": server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }


def build_bootstrap_bundle(folder: Path) -> Path:
    output = folder / "bootstrap.tar.gz"
    requirements = folder / "requirements.txt"
    # Bootstrap installs native wheels on the selected database host architecture.
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-default-groups",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        timeout=300,
    )
    with tarfile.open(output, "w:gz") as archive:
        archive.add(requirements, arcname="requirements.txt")
        for source, root in (
            (ROOT / "src", "src"),
            (ROOT / "assets/data/seed", "assets/data/seed"),
        ):
            for filename in sorted(source.rglob("*")):
                if filename.is_file() and "__pycache__" not in filename.parts:
                    archive.add(
                        filename, arcname=str(Path(root) / filename.relative_to(source))
                    )
        archive.add(ROOT / "scripts/shared/configure.py", arcname="configure.py")
        configuration = folder / "models.json"
        configuration.write_text(json.dumps(load_model_environment()))
        archive.add(configuration, arcname="models.json")
    return output
