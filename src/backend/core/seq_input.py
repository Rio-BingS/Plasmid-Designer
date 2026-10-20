"""粘贴序列的字符归一 — 全角/异体符号 → 半角 ASCII，去全部空白

真实输入里最常见的两种「看不懂的字符」都不是用户的错：

* 中文输入法下按 Shift+8 得到的是全角＊（U+FF0A），还有﹡（U+FE61）、
  ∗（U+2217）、٭（U+066D）——后端按单字母代码校验时一律报
  「无效的氨基酸代码」，用户看到的就是「* 无法识别」；
* 从 Word / Excel / 知网复制过来的序列常带全角字母、全角空格（U+3000）、
  不换行空格（U+00A0）与制表符，只 replace(" ", "") 去不掉。

所以归一放在**最早**的一步（请求模型 / process_sequence），而不是在每个
校验点各写一遍。只做字符层面的等价替换，不改序列语义：
全角 ASCII（U+FF01..FF5E）→ 半角、各类空白 → 删除、异体星号 → "*"、小写 → 大写。
U→T 只对 DNA 输入做：蛋白里的 U 是硒代半胱氨酸，换成 T（苏氨酸）会静默改序列。
"""

from __future__ import annotations

import unicodedata

# 异体星号：全角、小型、数学星号、阿拉伯星号（中文/日文输入法与排版软件常见）
_STAR_VARIANTS = "\uff0a\ufe61\u2217\u066d\u204e\uff3f"
_STAR_TABLE = {ord(ch): "*" for ch in _STAR_VARIANTS}

# 需要整体删除的零宽字符（粘贴时的隐形污染，str.isspace() 认不出来）
_INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff"


def normalize_sequence_text(text: str) -> str:
    """序列文本归一：去空白/隐形字符、全角转半角、异体星号转 *、大写

    只处理字符等价与排版噪声，不校验字符合法性——非法字符要留给调用方
    报出「哪个字符、在第几位」，在这里悄悄丢弃会让用户以为序列被接受。
    """
    if not text:
        return ""
    # NFKC 把全角字母/数字/符号折成半角（＊→*、Ａ→A、１→1）
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_STAR_TABLE)
    kept = [ch for ch in out if ch not in _INVISIBLE and not ch.isspace()]
    return "".join(kept).upper()


def normalize_dna_text(text: str) -> str:
    """DNA 输入：在通用归一上再把 U（RNA）折成 T"""
    return normalize_sequence_text(text).replace("U", "T")


def find_invalid_chars(seq: str, allowed: str) -> list[tuple[str, int]]:
    """返回 [(字符, 1-based 位置)]，供错误提示精确到位置"""
    allowed_set = set(allowed)
    return [(ch, i + 1) for i, ch in enumerate(seq) if ch not in allowed_set]
