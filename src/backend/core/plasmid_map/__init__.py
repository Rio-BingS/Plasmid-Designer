"""质粒图谱基础模块（契约 plasmid-map/1，见 docs/MAP.md）

纯函数包，不依赖 web 层：
- parsers   GenBank / FASTA / SnapGene .dna / 粘贴文本 → ParsedRecord
- types     FeatureType 归一、颜色
- enzymes   酶集合与契约 MapEnzyme（复用 core.enzyme_sites 的 REBASE 酶模型）
- orfs      双链、环状感知 ORF
- annotate  常见元件库自动注释（k-mer 种子 + 局部比对 + 肽段翻译检索）
- genbank   MapDocument → GenBank
- document  组装 MapDocument
"""

from core.plasmid_map.document import build_document, document_from_record  # noqa: F401
from core.plasmid_map.parsers import MapParseError, parse_file, parse_text  # noqa: F401
from core.plasmid_map.types import SCHEMA  # noqa: F401
