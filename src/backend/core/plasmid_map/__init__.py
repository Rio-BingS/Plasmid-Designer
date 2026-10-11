"""质粒图谱基础模块（契约 plasmid-map/1，见 docs/MAP.md）

纯函数包，不依赖 web 层：
- parsers   GenBank / FASTA / SnapGene .dna / 粘贴文本 → ParsedRecord
- types     FeatureType 归一、颜色
"""

from core.plasmid_map.parsers import MapParseError, parse_file, parse_text  # noqa: F401
from core.plasmid_map.types import SCHEMA  # noqa: F401
