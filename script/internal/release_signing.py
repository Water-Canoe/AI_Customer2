from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def _existing_parent(path: Path) -> Path:
    parent = path.expanduser().resolve().parent
    if not parent.is_dir():
        raise RuntimeError(f"父目录不存在: {parent}")
    return parent


def _public_fingerprint(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("发布私钥不是Ed25519私钥")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeError("发布公钥不是Ed25519公钥")
    return key


def generate(private_path: Path, public_path: Path) -> None:
    # Key generation is explicit and never overwrites an existing trust root.
    private_path = private_path.expanduser().resolve()
    public_path = public_path.expanduser().resolve()
    _existing_parent(private_path)
    _existing_parent(public_path)
    if private_path.exists() or public_path.exists():
        raise RuntimeError("密钥文件已存在，拒绝覆盖")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    with private_path.open("xb") as handle:
        handle.write(private_bytes)
    os.chmod(private_path, 0o600)
    try:
        with public_path.open("xb") as handle:
            handle.write(public_bytes)
    except Exception:
        private_path.unlink(missing_ok=True)
        raise

    print(f"private_key={private_path}")
    print(f"public_key={public_path}")
    print(f"public_fingerprint={_public_fingerprint(public_key)}")


def sign(private_path: Path, input_path: Path, output_path: Path) -> None:
    # Sign the exact bytes written by the publisher, without JSON reformatting.
    private_path = private_path.expanduser().resolve()
    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if not private_path.is_file():
        raise RuntimeError(f"发布私钥不存在: {private_path}")
    if not input_path.is_file():
        raise RuntimeError(f"待签名文件不存在: {input_path}")
    _existing_parent(output_path)
    if output_path.exists():
        raise RuntimeError("签名文件已存在，拒绝覆盖")

    private_key = _load_private_key(private_path)
    payload = input_path.read_bytes()
    signature = private_key.sign(payload)
    private_key.public_key().verify(signature, payload)
    output_path.write_text(base64.b64encode(signature).decode("ascii"), encoding="ascii")
    print(f"signature_file={output_path}")
    print(f"public_fingerprint={_public_fingerprint(private_key.public_key())}")


def verify(public_path: Path, input_path: Path, signature_path: Path) -> None:
    # Clients use the committed public key to reject altered manifests.
    public_path = public_path.expanduser().resolve()
    input_path = input_path.expanduser().resolve()
    signature_path = signature_path.expanduser().resolve()
    if not public_path.is_file() or not input_path.is_file() or not signature_path.is_file():
        raise RuntimeError("验签所需文件不完整")

    public_key = _load_public_key(public_path)
    try:
        signature = base64.b64decode(signature_path.read_text(encoding="ascii"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("签名文件不是有效Base64") from exc
    if len(signature) != 64:
        raise RuntimeError("Ed25519签名长度不正确")
    try:
        public_key.verify(signature, input_path.read_bytes())
    except InvalidSignature as exc:
        raise RuntimeError("发布签名验证失败") from exc
    print("signature_verified=true")
    print(f"public_fingerprint={_public_fingerprint(public_key)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Customer release signing helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="generate an Ed25519 key pair")
    generate_parser.add_argument("--private-key", type=Path, required=True)
    generate_parser.add_argument("--public-key", type=Path, required=True)

    sign_parser = subparsers.add_parser("sign", help="sign an exact manifest file")
    sign_parser.add_argument("--private-key", type=Path, required=True)
    sign_parser.add_argument("--input", type=Path, required=True)
    sign_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify", help="verify an exact manifest file")
    verify_parser.add_argument("--public-key", type=Path, required=True)
    verify_parser.add_argument("--input", type=Path, required=True)
    verify_parser.add_argument("--signature", type=Path, required=True)
    return parser


def main() -> None:
    try:
        args = build_parser().parse_args()
        if args.command == "generate":
            generate(args.private_key, args.public_key)
        elif args.command == "sign":
            sign(args.private_key, args.input, args.output)
        else:
            verify(args.public_key, args.input, args.signature)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
