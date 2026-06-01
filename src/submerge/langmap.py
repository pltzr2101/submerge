"""ISO 639 language code alias mapping and normalization.

Provides robust language code matching across ISO 639-1 (2-letter),
ISO 639-2/T (3-letter), ISO 639-2/B (bibliographic), and common
locale-style codes that Bazarr/Lingarr may generate.
"""

from __future__ import annotations

# Maps any known alias -> ISO 639-1 (2-letter) code
# Built from common subtitle naming conventions in the ARR ecosystem
_ALIAS_TO_639_1: dict[str, str] = {}

# Primary mapping: ISO 639-1 -> list of known aliases
_KNOWN_ALIASES: dict[str, list[str]] = {
    "de": ["de", "deu", "ger", "de-DE", "de_DE", "de-at", "de-AT", "de-ch", "de-CH"],
    "en": ["en", "eng", "en-US", "en_US", "en-GB", "en_GB", "en-AU", "en_AU"],
    "ko": ["ko", "kor", "ko-KR", "ko_KR"],
    "fr": ["fr", "fra", "fre", "fr-FR", "fr_FR", "fr-CA", "fr_CA"],
    "pl": ["pl", "pol"],
    "es": ["es", "spa", "es-ES", "es_ES", "es-MX", "es_MX", "es-419"],
    "it": ["it", "ita", "it-IT", "it_IT"],
    "ja": ["ja", "jpn", "ja-JP", "ja_JP"],
    "zh": [
        "zh", "zho", "chi", "zh-CN", "zh_CN", "zh-TW", "zh_TW",
        "zh-HK", "zh_HK", "zh-Hans", "zh-Hant",
    ],
    "ru": ["ru", "rus", "ru-RU", "ru_RU"],
    "pt": ["pt", "por", "pt-BR", "pt_BR", "pt-PT", "pt_PT"],
    "ar": ["ar", "ara", "ar-SA", "ar_SA", "ar-EG", "ar_EG"],
    "nl": ["nl", "nld", "dut", "nl-NL", "nl_NL", "nl-BE", "nl_BE"],
    "sv": ["sv", "swe", "sv-SE", "sv_SE"],
    "no": ["no", "nor", "nb", "nob", "nn", "nno"],
    "da": ["da", "dan", "da-DK", "da_DK"],
    "fi": ["fi", "fin", "fi-FI", "fi_FI"],
    "tr": ["tr", "tur", "tr-TR", "tr_TR"],
    "el": ["el", "ell", "gre", "el-GR", "el_GR"],
    "cs": ["cs", "ces", "cze", "cs-CZ", "cs_CZ"],
    "hu": ["hu", "hun", "hu-HU", "hu_HU"],
    "ro": ["ro", "ron", "rum", "ro-RO", "ro_RO"],
    "uk": ["uk", "ukr", "uk-UA", "uk_UA"],
    "th": ["th", "tha", "th-TH", "th_TH"],
    "vi": ["vi", "vie", "vi-VN", "vi_VN"],
    "hi": ["hi", "hin", "hi-IN", "hi_IN"],
    "bn": ["bn", "ben", "bn-BD", "bn_BD", "bn-IN", "bn_IN"],
    "id": ["id", "ind", "id-ID", "id_ID"],
    "ms": ["ms", "msa", "may", "ms-MY", "ms_MY"],
    "tl": ["tl", "tgl", "fil", "tl-PH", "tl_PH"],
    "he": ["he", "heb", "he-IL", "he_IL"],
    "sk": ["sk", "slk", "slo", "sk-SK", "sk_SK"],
    "bg": ["bg", "bul", "bg-BG", "bg_BG"],
    "ca": ["ca", "cat", "ca-ES", "ca_ES"],
    "hr": ["hr", "hrv", "hr-HR", "hr_HR"],
    "et": ["et", "est", "et-EE", "et_EE"],
    "lt": ["lt", "lit", "lt-LT", "lt_LT"],
    "lv": ["lv", "lav", "lv-LV", "lv_LV"],
    "sl": ["sl", "slv", "sl-SI", "sl_SI"],
    "sr": ["sr", "srp", "sr-RS", "sr_RS"],
    "sq": ["sq", "sqi", "alb", "sq-AL", "sq_AL"],
    "mk": ["mk", "mkd", "mac", "mk-MK", "mk_MK"],
    "is": ["is", "isl", "ice", "is-IS", "is_IS"],
    "fa": ["fa", "fas", "per", "fa-IR", "fa_IR"],
    "ta": ["ta", "tam", "ta-IN", "ta_IN"],
    "te": ["te", "tel", "te-IN", "te_IN"],
    "ml": ["ml", "mal", "ml-IN", "ml_IN"],
    "kn": ["kn", "kan", "kn-IN", "kn_IN"],
    "mr": ["mr", "mar", "mr-IN", "mr_IN"],
    "gu": ["gu", "guj", "gu-IN", "gu_IN"],
    "pa": ["pa", "pan", "pa-IN", "pa_IN"],
    "ur": ["ur", "urd", "ur-PK", "ur_PK"],
}

# Build the reverse lookup table: every alias -> ISO 639-1
for _lang_code, _aliases in _KNOWN_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_639_1[_alias.lower()] = _lang_code


def normalize_lang(code: str) -> str | None:
    """Normalize any language code to ISO 639-1 (2-letter).

    Handles:
        - ISO 639-1: "de" -> "de"
        - ISO 639-2/T: "deu" -> "de"
        - ISO 639-2/B: "ger" -> "de"
        - Locale-style: "de-DE" -> "de", "ko_KR" -> "ko"
        - Case normalization: "DE" -> "de", "Deu" -> "de"

    Args:
        code: Any language code string

    Returns:
        ISO 639-1 code (lowercase) or None if not recognized
    """
    if not code:
        return None
    code = code.lower().strip()
    return _ALIAS_TO_639_1.get(code)


def get_all_aliases(lang: str) -> list[str]:
    """Get all known aliases for an ISO 639-1 code.

    Args:
        lang: ISO 639-1 language code (e.g., "de")

    Returns:
        List of all known aliases, including the code itself
    """
    lang = lang.lower()
    return _KNOWN_ALIASES.get(lang, [lang])
