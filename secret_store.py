import base64
import ctypes
import ctypes.wintypes as wt
from getpass import getpass
from pathlib import Path
import sys

DPAPI_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretStoreError(RuntimeError):
    """Şifreli anahtar okuma/yazma hatası."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wt.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _get_win32_crypt_funcs():
    if sys.platform != "win32":
        raise SecretStoreError("DPAPI şifreleme yalnızca Windows üzerinde desteklenir.")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wt.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wt.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wt.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wt.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    return crypt32, kernel32


def _make_blob(raw_bytes: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
    blob = _DataBlob(len(raw_bytes), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def encrypt_secret(plaintext: str) -> str:
    """Metni geçerli Windows kullanıcısına bağlı DPAPI ile şifreler."""
    cleaned = plaintext.strip()
    if not cleaned:
        raise SecretStoreError("Şifrelenecek anahtar boş olamaz.")

    crypt32, kernel32 = _get_win32_crypt_funcs()
    raw_bytes = cleaned.encode("utf-8")
    in_blob, _buf = _make_blob(raw_bytes)
    out_blob = _DataBlob()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "kusur-api-gemini-key",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStoreError(f"DPAPI şifreleme başarısız (kod={ctypes.GetLastError()}).")

    try:
        encrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

    encoded = base64.b64encode(encrypted_bytes).decode("ascii")
    return f"{DPAPI_PREFIX}{encoded}"


def decrypt_secret(token: str) -> str:
    """DPAPI ile şifrelenmiş anahtarı yalnızca geçerli Windows kullanıcısı için çözer."""
    value = token.strip()
    if not value.startswith(DPAPI_PREFIX):
        raise SecretStoreError("Geçersiz şifreli anahtar formatı ('dpapi:' öneki bekleniyor).")

    encoded = value[len(DPAPI_PREFIX):]
    try:
        encrypted_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise SecretStoreError("Şifreli anahtar base64 formatında değil.") from exc

    crypt32, kernel32 = _get_win32_crypt_funcs()
    in_blob, _buf = _make_blob(encrypted_bytes)
    out_blob = _DataBlob()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecretStoreError(f"DPAPI anahtar çözme başarısız (kod={ctypes.GetLastError()}).")

    try:
        decrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

    return decrypted_bytes.decode("utf-8")


def save_encrypted_key_to_env(plaintext: str, env_path: Path | None = None) -> Path:
    """Anahtarı DPAPI ile şifreleyip .env dosyasına yazar; düz metin anahtarı temizler."""
    target = env_path or (Path(__file__).resolve().parent / ".env")
    encrypted_value = encrypt_secret(plaintext)

    existing_lines: list[str] = []
    if target.exists():
        existing_lines = target.read_text(encoding="utf-8").splitlines()

    new_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("GEMINI_API_KEY="):
            continue
        if stripped.startswith("GEMINI_API_KEY_ENCRYPTED="):
            if not replaced:
                new_lines.append(f"GEMINI_API_KEY_ENCRYPTED={encrypted_value}")
                replaced = True
            continue
        new_lines.append(line)

    if not replaced:
        new_lines.append(f"GEMINI_API_KEY_ENCRYPTED={encrypted_value}")

    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    secret = getpass("Yeni Gemini API anahtarını girin (ekrana yazdırılmaz): ")
    saved_path = save_encrypted_key_to_env(secret)
    print(f"Şifreli anahtar güvenli şekilde kaydedildi: {saved_path}")
