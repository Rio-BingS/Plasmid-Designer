"""
导出格式模块
支持多种格式导出：GenBank、SnapGene .dna、Benchling JSON、FASTA
"""
import json
import zipfile
import io
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import struct
import logging

from core.genbank_format import (
    format_feature_line, format_locus_line, format_qualifier,
    render_origin_block,
)

logger = logging.getLogger(__name__)


@dataclass
class SequenceFeature:
    """序列特征"""
    name: str
    feature_type: str
    start: int
    end: int
    strand: str  # '+' or '-'
    color: str = "#4A90D9"
    description: str = ""


@dataclass
class ExportData:
    """导出数据结构"""
    name: str
    sequence: str
    features: List[SequenceFeature]
    description: str = ""
    is_circular: bool = True
    created_at: datetime = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


class GenBankExporter:
    """GenBank 格式导出器"""
    
    @staticmethod
    def export(data: ExportData) -> str:
        """
        导出为 GenBank 格式
        
        GenBank 格式规范：
        - LOCUS: 序列基本信息
        - FEATURES: 特征注释
        - ORIGIN: 序列数据
        """
        lines = []
        
        # LOCUS 行
        locus_name = data.name[:16].replace(' ', '_').replace('-', '_')
        seq_length = len(data.sequence)
        topology = "circular" if data.is_circular else "linear"

        lines.append(format_locus_line(locus_name, seq_length, topology=topology,
                                       date=data.created_at))
        
        # DEFINITION
        lines.append(f"DEFINITION  {data.description or data.name}")
        
        # ACCESSION
        lines.append(f"ACCESSION   {locus_name}")
        
        # VERSION
        lines.append(f"VERSION     {locus_name}.1")
        
        # SOURCE
        lines.append("SOURCE      synthetic DNA construct")
        lines.append("  ORGANISM  synthetic DNA construct")
        lines.append("            other sequences; artificial sequences.")
        
        # FEATURES
        lines.append("FEATURES             Location/Qualifiers")
        lines.append(f"     source          1..{seq_length}")
        lines.append(f'                     /organism="synthetic DNA construct"')
        lines.append('                     /mol_type="other DNA"')
        
        for feature in data.features:
            feature_type = feature.feature_type.upper()
            if feature.strand == '+':
                location = f"{feature.start}..{feature.end}"
            else:
                location = f"complement({feature.start}..{feature.end})"

            lines.append(format_feature_line(feature_type, location))
            lines.append(format_qualifier("label", feature.name))
            if feature.description:
                lines.append(format_qualifier("note", feature.description))
            lines.append(format_qualifier("color", feature.color))

        # ORIGIN
        lines.append("ORIGIN")
        lines.extend(render_origin_block(data.sequence))
        
        lines.append("//")
        
        return '\n'.join(lines)


class SnapGeneExporter:
    """SnapGene .dna 格式导出器"""
    
    @staticmethod
    def export(data: ExportData) -> bytes:
        """
        导出为 SnapGene .dna 格式
        
        SnapGene .dna 文件格式：
        - 包含序列数据和特征注释
        - 使用特定的二进制结构
        """
        # SnapGene .dna 文件结构
        output = io.BytesIO()
        
        # 文件头
        output.write(b'SnapGene\x00')
        output.write(struct.pack('<I', 1))  # 版本号
        
        # 序列数据
        seq_bytes = data.sequence.encode('utf-8')
        output.write(struct.pack('<I', len(seq_bytes)))
        output.write(seq_bytes)
        
        # 拓扑结构 (1 = circular, 0 = linear)
        output.write(struct.pack('B', 1 if data.is_circular else 0))
        
        # 特征数据
        output.write(struct.pack('<I', len(data.features)))
        for feature in data.features:
            name_bytes = feature.name.encode('utf-8')
            output.write(struct.pack('<I', len(name_bytes)))
            output.write(name_bytes)
            
            type_bytes = feature.feature_type.encode('utf-8')
            output.write(struct.pack('<I', len(type_bytes)))
            output.write(type_bytes)
            
            output.write(struct.pack('<I', feature.start - 1))  # 0-indexed
            output.write(struct.pack('<I', feature.end))
            output.write(struct.pack('B', 1 if feature.strand == '+' else 0))
            
            color_bytes = feature.color.encode('utf-8')
            output.write(struct.pack('<I', len(color_bytes)))
            output.write(color_bytes)
        
        return output.getvalue()


class BenchlingExporter:
    """Benchling JSON 格式导出器"""
    
    @staticmethod
    def export(data: ExportData) -> str:
        """
        导出为 Benchling JSON 格式
        
        Benchling 格式结构：
        - bases: 序列数据
        - annotations: 特征注释
        - metadata: 元数据
        """
        annotations = []
        for i, feature in enumerate(data.features):
            annotations.append({
                "name": feature.name,
                "type": feature.feature_type,
                "start": feature.start - 1,  # Benchling 使用 0-indexed
                "end": feature.end,
                "strand": 1 if feature.strand == '+' else -1,
                "color": feature.color,
                "notes": feature.description
            })
        
        benchling_data = {
            "name": data.name,
            "bases": data.sequence.upper(),
            "annotations": annotations,
            "isCircular": data.is_circular,
            "folderId": None,
            "schemaId": None,
            "customFields": {},
            "namingStrategy": {
                "strategy": "keep_existing_name"
            },
            "primers": [],
            "translations": []
        }
        
        return json.dumps(benchling_data, indent=2)


class FASTAExporter:
    """FASTA 格式导出器"""
    
    @staticmethod
    def export(data: ExportData) -> str:
        """
        导出为 FASTA 格式
        
        格式：>name description
              SEQUENCE
        """
        header = f">{data.name}"
        if data.description:
            header += f" {data.description}"
        
        # 序列每行 60 个字符
        seq = data.sequence.upper()
        seq_lines = [seq[i:i+60] for i in range(0, len(seq), 60)]
        
        return '\n'.join([header] + seq_lines)


class SBOLExporter:
    """SBOL (Synthetic Biology Open Language) 格式导出器"""
    
    @staticmethod
    def export(data: ExportData) -> str:
        """
        导出为 SBOL 格式（简化版）
        
        SBOL 是合成生物学的标准数据交换格式
        """
        sbol = {
            "@context": "https://sbolstandard.org/examples/sbol3",
            "@type": "Component",
            "identity": f"https://example.org/{data.name}",
            "namespace": "https://example.org",
            "type": "DnaRegion",
            "sequences": [{
                "@type": "Sequence",
                "identity": f"https://example.org/{data.name}/sequence",
                "encoding": "http://www.chem.qmul.ac.uk/iupac/",
                "elements": data.sequence.upper()
            }],
            "features": []
        }
        
        for i, feature in enumerate(data.features):
            sbol["features"].append({
                "@type": "SequenceFeature",
                "identity": f"https://example.org/{data.name}/feature/{i}",
                "location": {
                    "@type": "Range",
                    "start": feature.start,
                    "end": feature.end,
                    "orientation": "https://sbolstandard.org/examples/forward" if feature.strand == '+' else "https://sbolstandard.org/examples/reverse"
                }
            })
        
        return json.dumps(sbol, indent=2)


class ExportManager:
    """导出管理器"""
    
    exporters = {
        "genbank": GenBankExporter,
        "snapgene": SnapGeneExporter,
        "benchling": BenchlingExporter,
        "fasta": FASTAExporter,
        "sbol": SBOLExporter
    }
    
    @classmethod
    def export(cls, data: ExportData, format: str) -> tuple[str | bytes, str]:
        """
        导出数据到指定格式
        
        Args:
            data: 导出数据
            format: 格式名称 (genbank, snapgene, benchling, fasta, sbol)
        
        Returns:
            (导出内容, MIME类型)
        """
        format = format.lower()
        
        if format not in cls.exporters:
            raise ValueError(f"不支持的导出格式: {format}")
        
        exporter = cls.exporters[format]
        content = exporter.export(data)
        
        mime_types = {
            "genbank": "text/plain",
            "snapgene": "application/octet-stream",
            "benchling": "application/json",
            "fasta": "text/plain",
            "sbol": "application/json"
        }
        
        return content, mime_types[format]
    
    @classmethod
    def export_all(cls, data: ExportData) -> bytes:
        """
        导出所有格式为 ZIP 文件
        
        Returns:
            ZIP 文件字节
        """
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # GenBank
            gb_content, _ = cls.export(data, "genbank")
            zf.writestr(f"{data.name}.gb", gb_content)
            
            # FASTA
            fasta_content, _ = cls.export(data, "fasta")
            zf.writestr(f"{data.name}.fasta", fasta_content)
            
            # Benchling JSON
            benchling_content, _ = cls.export(data, "benchling")
            zf.writestr(f"{data.name}_benchling.json", benchling_content)
            
            # SBOL
            sbol_content, _ = cls.export(data, "sbol")
            zf.writestr(f"{data.name}_sbol.json", sbol_content)
            
            # SnapGene
            snapgene_content, _ = cls.export(data, "snapgene")
            zf.writestr(f"{data.name}.dna", snapgene_content)
        
        zip_buffer.seek(0)
        return zip_buffer.getvalue()
    
    @classmethod
    def get_supported_formats(cls) -> List[Dict[str, str]]:
        """获取支持的导出格式列表"""
        return [
            {"id": "genbank", "name": "GenBank", "extension": ".gb", "description": "标准生物序列格式"},
            {"id": "snapgene", "name": "SnapGene", "extension": ".dna", "description": "SnapGene 项目文件"},
            {"id": "benchling", "name": "Benchling", "extension": ".json", "description": "Benchling 导入格式"},
            {"id": "fasta", "name": "FASTA", "extension": ".fasta", "description": "简单序列格式"},
            {"id": "sbol", "name": "SBOL", "extension": ".json", "description": "合成生物学标准格式"}
        ]


# 导出辅助函数
def create_export_data_from_design(design_result: Dict) -> ExportData:
    """从设计结果创建导出数据"""
    features = []
    
    # 添加引物特征
    for primer in design_result.get("primers", []):
        features.append(SequenceFeature(
            name=primer.get("name", "primer"),
            feature_type="primer_bind",
            start=1,
            end=len(primer.get("sequence", "")),
            strand="+",
            color="#FF6B6B",
            description=f"Tm: {primer.get('tm', 0):.1f}°C"
        ))
    
    return ExportData(
        name=design_result.get("design_id", "design"),
        sequence=design_result.get("optimized_sequence", ""),
        features=features,
        description=f"Designed with Plasmid Designer - {design_result.get('vector_name', '')}",
        is_circular=True
    )


def create_export_data_from_vector(vector: Dict) -> ExportData:
    """从载体数据创建导出数据"""
    features = []
    
    for feat in vector.get("features", []):
        features.append(SequenceFeature(
            name=feat.get("name", "feature"),
            feature_type=feat.get("type", "misc_feature"),
            start=feat.get("start", 1),
            end=feat.get("end", 100),
            strand=feat.get("strand", "+"),
            color=feat.get("color", "#4A90D9"),
            description=feat.get("description", "")
        ))
    
    return ExportData(
        name=vector.get("name", "vector"),
        sequence=vector.get("sequence", ""),
        features=features,
        description=vector.get("description", ""),
        is_circular=True
    )
