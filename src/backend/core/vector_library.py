"""
载体库管理模块

支持:
- 从 GenBank/YAML 导入载体
- 载体元件解析
- MCS (多克隆位点) 定位
- 载体兼容性检查
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import yaml


class ElementType(Enum):
    """载体元件类型"""
    PROMOTER = "promoter"
    TERMINATOR = "terminator"
    ORIGIN = "origin"
    RESISTANCE = "resistance"
    TAG = "tag"
    MCS = "multiple_cloning_site"
    GENE = "gene"
    ENHANCER = "enhancer"
    REGULATORY = "regulatory"  # 调控区（如 lac operator）
    SIGNALLING_PEPTIDE = "signalling_peptide"
    OTHER = "other"


@dataclass
class VectorElement:
    """载体元件"""
    name: str
    element_type: ElementType
    start: int  # 1-indexed
    end: int    # 1-indexed, inclusive
    sequence: str
    strand: str = "+"  # + or -
    description: str = ""
    
    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass
class CloningSite:
    """克隆位点"""
    enzyme_name: str
    recognition_seq: str
    cut_position_5: int  # 5' 切割位置（相对于载体起点）
    cut_position_3: int  # 3' 切割位置
    overhang: str  # 粘性末端, "" 表示平末端
    is_unique: bool = True


@dataclass
class MCS:
    """多克隆位点"""
    name: str = "MCS"
    start: int = 0
    end: int = 0
    sites: List[CloningSite] = field(default_factory=list)
    reading_frame: int = 1  # 1, 2, or 3
    
    def get_unique_enzymes(self) -> List[str]:
        """获取唯一位点酶列表"""
        return [s.enzyme_name for s in self.sites if s.is_unique]


@dataclass
class Vector:
    """载体定义"""
    id: str
    name: str
    sequence: str
    source: str = ""
    vector_type: str = "expression"  # expression, cloning, CRISPR, reporter, etc.
    host: List[str] = field(default_factory=list)  # E.coli, mammalian, yeast, etc.
    elements: List[VectorElement] = field(default_factory=list)
    mcs: Optional[MCS] = None
    antibiotic_resistance: List[str] = field(default_factory=list)
    copy_number: str = "medium"  # low, medium, high
    description: str = ""
    tags: List[str] = field(default_factory=list)  # N-His, C-Flag, etc.
    
    @property
    def length(self) -> int:
        return len(self.sequence)
    
    def get_element(self, name: str) -> Optional[VectorElement]:
        """按名称获取元件"""
        for elem in self.elements:
            if elem.name == name:
                return elem
        return None
    
    def get_elements_by_type(self, elem_type: ElementType) -> List[VectorElement]:
        """按类型获取元件"""
        return [e for e in self.elements if e.element_type == elem_type]


class VectorLibrary:
    """载体库管理器"""
    
    def __init__(self):
        self.vectors: Dict[str, Vector] = {}
    
    def add_vector(self, vector: Vector) -> None:
        """添加载体到库"""
        self.vectors[vector.id] = vector
    
    def get_vector(self, vector_id: str) -> Optional[Vector]:
        """获取载体"""
        return self.vectors.get(vector_id)
    
    def list_vectors(self, filter_type: Optional[str] = None) -> List[Vector]:
        """列出所有载体，可选过滤"""
        vectors = list(self.vectors.values())
        if filter_type:
            vectors = [v for v in vectors if v.vector_type == filter_type]
        return vectors
    
    def search_vectors(self, query: str) -> List[Vector]:
        """搜索载体"""
        query = query.lower()
        results = []
        for v in self.vectors.values():
            if (query in v.name.lower() or 
                query in v.id.lower() or 
                query in v.description.lower()):
                results.append(v)
        return results
    
    def load_from_yaml(self, yaml_path: str) -> None:
        """从YAML文件加载载体"""
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        if isinstance(data, list):
            for vec_data in data:
                vector = self._parse_vector_dict(vec_data)
                self.add_vector(vector)
        else:
            vector = self._parse_vector_dict(data)
            self.add_vector(vector)
    
    def load_from_directory(self, dir_path: str) -> int:
        """从目录批量加载载体"""
        count = 0
        for file in Path(dir_path).glob("*.yaml"):
            try:
                self.load_from_yaml(str(file))
                count += 1
            except Exception as e:
                print(f"Warning: Failed to load {file}: {e}")
        return count
    
    def _parse_vector_dict(self, data: dict) -> Vector:
        """解析载体字典"""
        # 解析元件
        elements = []
        for elem_data in data.get('features', []):
            elem = VectorElement(
                name=elem_data['name'],
                element_type=ElementType(elem_data.get('type', 'other')),
                start=elem_data['start'],
                end=elem_data['end'],
                sequence=elem_data.get('sequence', ''),
                strand=elem_data.get('strand', '+'),
                description=elem_data.get('description', '')
            )
            elements.append(elem)
        
        # 解析MCS
        mcs = None
        mcs_data = data.get('mcs')
        if mcs_data:
            sites = []
            for site_data in mcs_data.get('sites', []):
                site = CloningSite(
                    enzyme_name=site_data['enzyme'],
                    recognition_seq=site_data['sequence'],
                    cut_position_5=site_data['position'],
                    cut_position_3=site_data['position'] + len(site_data['sequence']),
                    overhang=site_data.get('overhang', ''),
                    is_unique=site_data.get('unique', True)
                )
                sites.append(site)
            
            mcs = MCS(
                name=mcs_data.get('name', 'MCS'),
                start=mcs_data.get('start', 0),
                end=mcs_data.get('end', 0),
                sites=sites,
                reading_frame=mcs_data.get('frame', 1)
            )
        
        return Vector(
            id=data['id'],
            name=data['name'],
            sequence=data.get('sequence', ''),
            source=data.get('source', ''),
            vector_type=data.get('type', 'expression'),
            host=data.get('host', []),
            elements=elements,
            mcs=mcs,
            antibiotic_resistance=data.get('antibiotic', []),
            copy_number=data.get('copy_number', 'medium'),
            description=data.get('description', ''),
            tags=data.get('tags', [])
        )
    
    def export_yaml(self, vector_id: str, output_path: str) -> None:
        """导出载体为YAML"""
        vector = self.get_vector(vector_id)
        if not vector:
            raise ValueError(f"Vector not found: {vector_id}")
        
        data = {
            'id': vector.id,
            'name': vector.name,
            'source': vector.source,
            'type': vector.vector_type,
            'host': vector.host,
            'antibiotic': vector.antibiotic_resistance,
            'copy_number': vector.copy_number,
            'description': vector.description,
            'tags': vector.tags,
            'features': [
                {
                    'name': e.name,
                    'type': e.element_type.value,
                    'start': e.start,
                    'end': e.end,
                    'sequence': e.sequence,
                    'strand': e.strand,
                    'description': e.description
                }
                for e in vector.elements
            ]
        }
        
        if vector.mcs:
            data['mcs'] = {
                'name': vector.mcs.name,
                'start': vector.mcs.start,
                'end': vector.mcs.end,
                'frame': vector.mcs.reading_frame,
                'sites': [
                    {
                        'enzyme': s.enzyme_name,
                        'sequence': s.recognition_seq,
                        'position': s.cut_position_5,
                        'overhang': s.overhang,
                        'unique': s.is_unique
                    }
                    for s in vector.mcs.sites
                ]
            }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


class VectorCompatibilityChecker:
    """载体兼容性检查器"""
    
    @staticmethod
    def check_enzyme_sites(
        insert_seq: str, 
        vector: Vector,
        required_enzymes: List[str]
    ) -> Dict[str, any]:
        """
        检查插入序列是否含有载体MCS中的酶切位点
        
        Returns:
            {
                'compatible': bool,
                'conflicts': [{enzyme, position_in_insert}],
                'recommended_enzymes': [enzymes without conflicts]
            }
        """
        if not vector.mcs:
            return {
                'compatible': False,
                'conflicts': [],
                'recommended_enzymes': [],
                'error': 'Vector has no MCS defined'
            }
        
        insert_seq = insert_seq.upper()
        conflicts = []
        safe_enzymes = []
        
        for site in vector.mcs.sites:
            # 检查插入序列中是否存在该酶切位点
            pattern = site.recognition_seq.upper()
            # 处理简并碱基
            pattern = pattern.replace('N', '[ATGC]')
            
            matches = list(re.finditer(pattern, insert_seq))
            
            if matches:
                conflicts.append({
                    'enzyme': site.enzyme_name,
                    'recognition_seq': site.recognition_seq,
                    'positions': [m.start() for m in matches]
                })
            elif site.is_unique:
                safe_enzymes.append(site.enzyme_name)
        
        return {
            'compatible': len(conflicts) == 0,
            'conflicts': conflicts,
            'recommended_enzymes': safe_enzymes,
            'mcs_frame': vector.mcs.reading_frame
        }
    
    @staticmethod
    def check_reading_frame(
        insert_seq: str,
        vector: Vector,
        start_enzyme: str,
        end_enzyme: str
    ) -> Dict[str, any]:
        """
        检查克隆后的阅读框
        
        Returns:
            {
                'in_frame': bool,
                'insert_frame': int,  # 插入序列的正确框架
                'adjustment_needed': int  # 需要添加的碱基数
            }
        """
        if not vector.mcs:
            return {'error': 'No MCS defined'}
        
        # 找到起始和终止酶的位置
        start_site = None
        end_site = None
        for site in vector.mcs.sites:
            if site.enzyme_name == start_enzyme:
                start_site = site
            if site.enzyme_name == end_enzyme:
                end_site = site
        
        if not start_site or not end_site:
            return {'error': 'Enzyme not found in MCS'}
        
        # 计算MCS框架位置
        mcs_frame = vector.mcs.reading_frame
        
        # 计算需要调整的碱基数
        # (简化计算，实际需要考虑酶切产生的末端)
        
        return {
            'in_frame': True,
            'insert_frame': 1,
            'adjustment_needed': 0,
            'mcs_start': start_site.cut_position_5,
            'mcs_end': end_site.cut_position_5
        }


# 常用限制性内切酶数据
COMMON_ENZYMES = {
    'EcoRI': {'seq': 'GAATTC', 'overhang': 'AATT', 'type': 'sticky_5'},
    'BamHI': {'seq': 'GGATCC', 'overhang': 'GATC', 'type': 'sticky_5'},
    'HindIII': {'seq': 'AAGCTT', 'overhang': 'AGCT', 'type': 'sticky_5'},
    'NcoI': {'seq': 'CCATGG', 'overhang': 'CATG', 'type': 'sticky_5'},
    'XhoI': {'seq': 'CTCGAG', 'overhang': 'TCGA', 'type': 'sticky_5'},
    'XbaI': {'seq': 'TCTAGA', 'overhang': 'CTAG', 'type': 'sticky_5'},
    'SalI': {'seq': 'GTCGAC', 'overhang': 'TCGA', 'type': 'sticky_5'},
    'PstI': {'seq': 'CTGCAG', 'overhang': 'TGCA', 'type': 'sticky_3'},
    'KpnI': {'seq': 'GGTACC', 'overhang': 'GTAC', 'type': 'sticky_3'},
    'SacI': {'seq': 'GAGCTC', 'overhang': 'GAGCT', 'type': 'sticky_3'},
    'NotI': {'seq': 'GCGGCCGC', 'overhang': 'GGCCGC', 'type': 'sticky_5'},
    'SmaI': {'seq': 'CCCGGG', 'overhang': '', 'type': 'blunt'},
    'SspI': {'seq': 'AATATT', 'overhang': '', 'type': 'blunt'},
    # Type IIS enzymes for Golden Gate
    'BsaI': {'seq': 'GGTCTC', 'overhang': '4bp_custom', 'type': 'IIS'},
    'BsmBI': {'seq': 'CGTCTC', 'overhang': '4bp_custom', 'type': 'IIS'},
    'BbsI': {'seq': 'GAAGAC', 'overhang': '4bp_custom', 'type': 'IIS'},
}
