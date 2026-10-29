"""
第三方载体库集成模块

支持数据源:
1. NCBI E-utilities API - 免费，直接从 GenBank 获取序列
2. Addgene API - 开放载体库，有 API
3. SnapGene 格式导入 - .dna 文件
4. GenBank 格式导入 - .gb/.gbk 文件
5. Benchling 导出格式
"""

import logging
import os
import re
import json
import yaml
import urllib.request
import urllib.parse
import urllib.error
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path

from core import paths as core_paths

logger = logging.getLogger(__name__)


@dataclass
class ExternalVector:
    """外部载体数据结构"""
    id: str
    name: str
    source: str  # ncbi, addgene, snapgene, genbank
    sequence: str
    description: str
    vector_type: str
    host: List[str]
    antibiotic_resistance: List[str]
    features: List[Dict]
    url: Optional[str] = None
    original_format: Optional[str] = None
    # 真实拓扑（LOCUS 行）：此前 NCBI/批量导入不写 topology，线性记录被当环状画
    topology: str = "circular"


def locus_topology(content: str) -> str:
    """GenBank LOCUS 行里的拓扑：含 linear 记为线性；其余（含缺省）沿用载体库默认的环状"""
    m = re.search(r'^LOCUS[^\n]*', content or "", re.MULTILINE)
    if m and re.search(r'\blinear\b', m.group(0), re.IGNORECASE):
        return "linear"
    return "circular"


class NCBIClient:
    """NCBI E-utilities API 客户端 - 免费获取 GenBank 序列"""
    
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    
    def __init__(self, email: str = "user@example.com", api_key: str = None):
        self.email = email
        self.api_key = api_key
        self.last_request_time = 0
        self.min_interval = 0.34 if api_key else 0.6  # NCBI 速率限制
    
    def _make_request(self, url: str) -> str:
        """发送 HTTP 请求，遵守速率限制"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                self.last_request_time = time.time()
                return response.read().decode('utf-8')
        except urllib.error.URLError as e:
            logger.warning("NCBI API 请求失败: %s", e)
            return None
    
    def search(self, query: str, database: str = "nucleotide", limit: int = 20) -> List[str]:
        """搜索 NCBI 数据库，返回序列 ID 列表"""
        params = {
            "db": database,
            "term": query,
            "retmax": limit,
            "retmode": "json",
            "email": self.email
        }
        if self.api_key:
            params["api_key"] = self.api_key
        
        url = f"{self.BASE_URL}/esearch.fcgi?{urllib.parse.urlencode(params)}"
        response = self._make_request(url)
        
        if response:
            data = json.loads(response)
            return data.get("esearchresult", {}).get("idlist", [])
        return []
    
    def fetch_genbank(self, seq_id: str) -> Optional[ExternalVector]:
        """通过序列 ID 获取 GenBank 格式数据"""
        params = {
            "db": "nucleotide",
            "id": seq_id,
            "rettype": "gb",
            "retmode": "text",
            "email": self.email
        }
        if self.api_key:
            params["api_key"] = self.api_key
        
        url = f"{self.BASE_URL}/efetch.fcgi?{urllib.parse.urlencode(params)}"
        response = self._make_request(url)
        
        if response:
            return self._parse_genbank_response(response, seq_id)
        return None
    
    def fetch_fasta(self, seq_id: str) -> Optional[Tuple[str, str]]:
        """通过序列 ID 获取 FASTA 格式序列"""
        params = {
            "db": "nucleotide",
            "id": seq_id,
            "rettype": "fasta",
            "retmode": "text",
            "email": self.email
        }
        if self.api_key:
            params["api_key"] = self.api_key
        
        url = f"{self.BASE_URL}/efetch.fcgi?{urllib.parse.urlencode(params)}"
        response = self._make_request(url)
        
        if response:
            lines = response.strip().split('\n')
            if lines:
                name = lines[0].replace('>', '').split()[0] if lines[0].startswith('>') else seq_id
                sequence = ''.join(lines[1:]).upper()
                return (name, sequence)
        return None
    
    def _parse_genbank_response(self, content: str, seq_id: str) -> ExternalVector:
        """解析 NCBI 返回的 GenBank 格式"""
        locus_match = re.search(r'LOCUS\s+(\S+)', content)
        name = locus_match.group(1) if locus_match else f"NCBI_{seq_id}"
        
        def_match = re.search(r'DEFINITION\s+(.+?)(?=\n[A-Z]+)', content, re.DOTALL)
        description = def_match.group(1).strip() if def_match else ""
        
        sequence = ""
        origin_match = re.search(r'ORIGIN\s*\n(.+?)(?=//)', content, re.DOTALL)
        if origin_match:
            seq_lines = origin_match.group(1)
            sequence = re.sub(r'[\s\d]+', '', seq_lines).upper()
        
        features = []
        features_match = re.search(r'FEATURES.+?ORIGIN', content, re.DOTALL)
        if features_match:
            features_text = features_match.group(0)
            feature_matches = re.finditer(r'^\s{5}(\w+)\s+(\d+)\.\.(\d+)', features_text, re.MULTILINE)
            for m in feature_matches:
                features.append({'type': m.group(1), 'start': int(m.group(2)), 'end': int(m.group(3))})
        
        return ExternalVector(
            id=f"ncbi_{seq_id}",
            name=name,
            source="ncbi",
            sequence=sequence,
            description=description,
            vector_type="imported",
            host=[],
            antibiotic_resistance=[],
            features=features,
            url=f"https://www.ncbi.nlm.nih.gov/nuccore/{seq_id}",
            original_format="genbank",
            topology=locus_topology(content),
        )
    
    def search_and_fetch(self, query: str, limit: int = 5) -> List[ExternalVector]:
        """搜索并获取载体数据（一键操作）"""
        vectors = []
        ids = self.search(query, limit=limit)
        for seq_id in ids:
            vector = self.fetch_genbank(seq_id)
            if vector:
                vectors.append(vector)
        return vectors


class AddgeneClient:
    """Addgene API 客户端"""
    
    BASE_URL = "https://api.addgene.org/v2"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
    
    def search_vectors(self, query: str, limit: int = 20) -> List[ExternalVector]:
        """搜索 Addgene 载体"""
        vectors = []
        popular_vectors = [
            {"id": "12259", "name": "pLenti-CMV-GFP-Puro", "type": "lentiviral"},
            {"id": "12252", "name": "pLenti-CMV-EGFP-FLAG-P2A-Puro", "type": "lentiviral"},
            {"id": "15860", "name": "pcDNA3.1(+)", "type": "expression"},
            {"id": "6318", "name": "pET-28a(+)", "type": "expression"},
            {"id": "39281", "name": "pGEX-6P-1", "type": "expression"},
        ]
        for v in popular_vectors:
            if query.lower() in v['name'].lower():
                vectors.append(ExternalVector(
                    id=f"addgene_{v['id']}",
                    name=v['name'],
                    source="addgene",
                    sequence="",
                    description=f"Addgene plasmid #{v['id']}",
                    vector_type=v['type'],
                    host=[],
                    antibiotic_resistance=[],
                    features=[],
                    url=f"https://www.addgene.org/{v['id']}/"
                ))
        return vectors[:limit]


class GenBankImporter:
    """GenBank 格式文件导入器"""
    
    def import_file(self, filepath: str) -> ExternalVector:
        """导入 GenBank 格式文件"""
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return self._parse_genbank(content)
    
    def _parse_genbank(self, content: str) -> ExternalVector:
        """解析 GenBank 格式内容"""
        locus_match = re.search(r'LOCUS\s+(\S+)', content)
        name = locus_match.group(1) if locus_match else "unknown"
        
        def_match = re.search(r'DEFINITION\s+(.+?)(?=\n[A-Z]+)', content, re.DOTALL)
        description = def_match.group(1).strip() if def_match else ""
        
        sequence = ""
        origin_match = re.search(r'ORIGIN\s*\n(.+?)(?=//)', content, re.DOTALL)
        if origin_match:
            sequence = re.sub(r'[\s\d]+', '', origin_match.group(1)).upper()
        
        return ExternalVector(
            id=f"genbank_{name}",
            name=name,
            source="genbank",
            sequence=sequence,
            description=description,
            vector_type="imported",
            host=[],
            antibiotic_resistance=[],
            features=[],
            original_format="genbank",
            topology=locus_topology(content),
        )
    
    def import_directory(self, dir_path: str, recursive: bool = True) -> List[ExternalVector]:
        """批量导入目录中的 GenBank 文件"""
        vectors = []
        path = Path(dir_path)
        patterns = ['**/*.gb', '**/*.gbk'] if recursive else ['*.gb', '*.gbk']
        for pattern in patterns:
            for file in path.glob(pattern):
                try:
                    vectors.append(self.import_file(str(file)))
                except Exception as e:
                    logger.warning("GenBank 导入失败 %s: %s", file, e)
        return vectors


class VectorLibraryManager:
    """载体库管理器 - 统一接口"""
    
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or str(core_paths.vectors_dir())
        self.ncbi = NCBIClient()
        self.addgene = AddgeneClient()
        self.genbank = GenBankImporter()
        self.vectors: Dict[str, ExternalVector] = {}
    
    def import_from_ncbi(self, query: str, limit: int = 5) -> int:
        """从 NCBI 搜索并导入载体"""
        vectors = self.ncbi.search_and_fetch(query, limit=limit)
        for v in vectors:
            self.vectors[v.id] = v
        return len(vectors)
    
    def import_from_ncbi_id(self, seq_id: str) -> bool:
        """通过 NCBI 序列 ID 直接导入"""
        vector = self.ncbi.fetch_genbank(seq_id)
        if vector:
            self.vectors[vector.id] = vector
            return True
        return False
    
    def import_from_genbank(self, path: str) -> int:
        """从 GenBank 文件导入"""
        path_obj = Path(path)
        if path_obj.is_file():
            try:
                vector = self.genbank.import_file(path)
                self.vectors[vector.id] = vector
                return 1
            except:
                return 0
        else:
            vectors = self.genbank.import_directory(path)
            for v in vectors:
                self.vectors[v.id] = v
            return len(vectors)
    
    def export_to_yaml(self, output_dir: str) -> int:
        """导出所有载体为 YAML 格式

        features 必须带 name/strand（VectorLibrary._parse_vector_dict 的契约），
        否则落盘的 YAML 加载时抛 KeyError、载体静默消失。
        """
        os.makedirs(output_dir, exist_ok=True)
        count = 0
        for vid, vector in self.vectors.items():
            # 文件名来自外部数据（GenBank LOCUS 行），只放行安全字符集——
            # 此前仅替换 `/`，Windows 上 `\` 仍是路径分隔符，`..\` 可越出目标目录
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", vector.name).strip("._") or "vector"
            filepath = os.path.join(output_dir, f"{safe_stem}.yaml")
            features = []
            for f in vector.features or []:
                item = dict(f)
                item['name'] = str(item.get('name') or item.get('type') or 'feature')[:80]
                item.setdefault('strand', '+')
                features.append(item)
            data = {
                'id': vector.id,
                'name': vector.name,
                'source': vector.source,
                'sequence': vector.sequence,
                'topology': 'linear' if vector.topology == 'linear' else 'circular',
                'description': vector.description,
                'features': features,
                'url': vector.url,
                # 结构化出处（与内置载体/在线导入同形状）：NCBI 来源可追溯到 accession
                'data_provenance': {
                    k: v for k, v in {
                        'source': vector.source or 'NCBI',
                        'url': vector.url or '',
                        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    }.items() if v
                },
            }
            with open(filepath, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            count += 1
        return count
    
    def search_local(self, query: str) -> List[ExternalVector]:
        """搜索本地载体库"""
        query = query.lower()
        return [v for v in self.vectors.values() if query in v.name.lower() or query in v.description.lower()]
    
    def search_online(self, query: str) -> List[ExternalVector]:
        """在线搜索"""
        return self.addgene.search_vectors(query)
