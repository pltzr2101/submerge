#!/usr/bin/env python3
"""Generate binary-encoded subtitle fixtures for encoding tests.

Run this script whenever fixture content changes. The resulting files are binary
(not UTF-8) by design — .gitattributes marks them as such to prevent Git from
mangling the bytes.
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent

SRT_CONTENT = (
    "1\n"
    "00:00:01,000 --> 00:00:04,000\n"
    "안녕하세요, 어떻게 지내세요?\n"
    "\n"
    "2\n"
    "00:00:05,000 --> 00:00:08,000\n"
    "잘 지내요, 감사합니다.\n"
    "\n"
    "3\n"
    "00:00:10,000 --> 00:00:14,000\n"
    "오늘 무엇을 할까요?\n"
)

# Write as actual EUC-KR bytes (NOT UTF-8)
(FIXTURES_DIR / "sample_euc_kr.srt").write_bytes(SRT_CONTENT.encode("euc-kr"))
print(f"Written sample_euc_kr.srt: {(FIXTURES_DIR / 'sample_euc_kr.srt').stat().st_size} bytes")

# Write as actual CP949 bytes (NOT UTF-8)
(FIXTURES_DIR / "sample_cp949.srt").write_bytes(SRT_CONTENT.encode("cp949"))
print(f"Written sample_cp949.srt: {(FIXTURES_DIR / 'sample_cp949.srt').stat().st_size} bytes")

# Write actual garbage bytes (not valid in any encoding as SRT)
garbage = bytes(range(0, 256)) * 4 + b"\x80\x81\x82\x83\xff\xfe\xfd"
(FIXTURES_DIR / "sample_garbage.bin").write_bytes(garbage)
print(f"Written sample_garbage.bin: {(FIXTURES_DIR / 'sample_garbage.bin').stat().st_size} bytes")

# Quick self-test: all generated fixtures must reject UTF-8 decoding
for name in ("sample_euc_kr.srt", "sample_cp949.srt", "sample_garbage.bin"):
    data = (FIXTURES_DIR / name).read_bytes()
    try:
        data.decode("utf-8")
        raise SystemExit(
            f"ERROR: {name} was written as valid UTF-8. "
            f"This is a bug — fixtures must be binary to test encoding fallbacks."
        )
    except UnicodeDecodeError:
        pass
print("Self-check passed: all fixtures are non-UTF-8 binary files.")
