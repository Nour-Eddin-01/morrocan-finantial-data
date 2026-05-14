import hashlib


def normalize_text_for_hash(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_source_payload(*, source_url: str, body_text: str) -> str:
    normalized_body = normalize_text_for_hash(body_text)
    return sha256_text(f"{source_url}\n{normalized_body}")

