# -*- coding: utf-8 -*-
"""
AI-HUB 知识库检索封装：通过 ai-hub CLI 搜索所有有权限的知识库，
为 e签宝 · 功能价值教练 提供动态产品知识来源。
"""
import os
import json
import re
import subprocess
import hashlib


# 缓存目录（与 coach.db 同目录）
_CACHE_DIR = None


def set_cache_dir(path):
    """设置缓存根目录（由 app.py 传入 DATA_DIR）。"""
    global _CACHE_DIR
    _CACHE_DIR = path


def _ensure_cache_dir():
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(_CACHE_DIR, ".aihub_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


# ========== CLI 封装 ==========

def _run_aihub(args, timeout=30):
    """执行 ai-hub <args> 并返回 stdout；失败返回 (None, stderr)。"""
    cmd = ["ai-hub"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return None, result.stderr.strip()
        return result.stdout.strip(), None
    except FileNotFoundError:
        return None, "ai-hub CLI not found"
    except subprocess.TimeoutExpired:
        return None, "ai-hub CLI timeout"


def search_all_accessible(query, max_results=10):
    """在**所有有权限**的知识库里搜索指定 query，返回结构化结果列表。

    自动过滤无权限知识库，只搜索可访问的。
    """
    # 先列出所有有权限的知识库
    raw, err = _run_aihub(["knowledge", "list", "--all"])
    if not raw or err:
        return {"error": err or "无法获取知识库列表"}

    # 解析列表，提取有权限的知识库 ID
    kb_map = {}  # id -> name
    for line in raw.split("\n"):
        if "（有权限）" not in line:
            continue
        # 格式：名称：xxx（有权限） ID：xxxxxx 描述：...
        name_part = line.split("ID：")[0].replace("名称：", "").strip()
        if "（无权限）" in name_part:
            continue
        id_part = line.split("ID：")[1].split()[0]
        kb_map[id_part] = name_part

    if not kb_map:
        return {"error": "没有可访问的知识库"}

    # 搜索每个知识库（合并查询，取前 max_results 条）
    all_results = []
    batch_size = 5  # 每轮并行不超过5个，避免卡死
    ids_to_search = list(kb_map.keys())[:max_results]  # 控制数量

    for kb_id in ids_to_search:
        raw, err = _run_aihub([
            "knowledge", "search",
            "--query", query,
            "--id", kb_id
        ])
        if err or not raw:
            continue
        parsed = _parse_search_result(raw, kb_map.get(kb_id, ""))
        all_results.extend(parsed)

    # 按匹配度排序
    all_results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return {
        "results": all_results[:max_results],
        "total_available_kbs": len(kb_map),
    }


def _parse_search_result(raw_text, kb_name=""):
    """解析 ai-hub knowledge search 的输出文本为结构化数据。"""
    docs = []
    lines = raw_text.split("\n")
    current_doc = None

    i = 0
    while i < len(lines):
        line = lines[i]

        # 检测新文档块：行首"[数字]"开头（如 "[1]""[2]"）
        if re.match(r"^\[\d+\]\s*$", line):
            if current_doc:
                docs.append(current_doc)
            current_doc = {"kb_name": kb_name}

        # 提取 doc-id（文档ID: xxx 或 知识库ID: xxx）
        if current_doc is not None and "文档ID:" in line:
            current_doc["doc_id"] = line.split("文档ID:", 1)[1].strip()
        if current_doc is not None and "知识库ID:" in line:
            current_doc["kb_id"] = line.split("知识库ID:", 1)[1].strip()

        # 提取字段（标题、匹配度、内容等）
        for key in ["标题:", "文件名:", "匹配度:", "内容:"]:
            if key in line and current_doc is not None:
                val = line.split(key, 1)[1].strip()
                if "匹配度:" in key:
                    try:
                        current_doc["_score"] = float(val)
                    except ValueError:
                        pass
                else:
                    current_doc[key.replace(":", "")] = val

        i += 1

    if current_doc:
        docs.append(current_doc)

    return docs


# ========== 缓存层 ==========

def _cache_key(query, feature_name, category):
    """生成缓存 key。"""
    raw = f"{feature_name}|{category}|{query}"
    return hashlib.md5(raw.encode()).hexdigest()


def cache_get(key):
    """读取缓存，过期返回 None。"""
    d = _ensure_cache_dir()
    fp = os.path.join(d, f"{key}.json")
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        # TTL: 6 小时
        import time
        if time.time() - data.get("_ts", 0) > 6 * 3600:
            os.remove(fp)
            return None
        return data.get("results", [])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def cache_put(key, results):
    """写入缓存。"""
    d = _ensure_cache_dir()
    fp = os.path.join(d, f"{key}.json")
    try:
        import time
        data = {"_ts": time.time(), "results": results}
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ========== 对外统一接口 ==========

def fetch_product_context(feature_name, category, code=""):
    """根据功能信息，从 ai-hub 知识库检索相关产品上下文，用于注入 System Prompt。

    返回值：str — 格式化后的知识库内容字符串，可直接拼接到 System Prompt 里。
    检索不到时返回空字符串。
    """
    # 构建查询词：功能名 + 分类 + 通用产品关键词
    query_terms = [feature_name]
    if category:
        query_terms.append(category)
    query_terms.extend(["产品体系", "电子签名", "功能说明", "客户场景", "价值点"])
    query = " ".join(query_terms)

    # 检查缓存
    key = _cache_key(query, feature_name, category)
    cached = cache_get(key)
    if cached:
        return format_search_results(cached)

    # 搜索知识库
    result = search_all_accessible(query)
    if isinstance(result, dict) and "error" in result:
        return ""

    formatted = format_search_results(result.get("results", []))

    # 写入缓存
    if formatted:
        cache_put(key, result.get("results", []))

    return formatted


def format_search_results(results):
    """将搜索结果格式化为可读文本，供 AI 生成时参考。"""
    if not results:
        return ""

    sections = []
    for i, doc in enumerate(results, 1):
        title = doc.get("标题", "未知文档")
        content = doc.get("内容", "")
        kb_name = doc.get("kb_name", "")
        score = doc.get("_score", 0)

        section = f"[{i}] {title}\n来源：{kb_name}\n匹配度：{score:.2f}\n{content[:500]}"
        sections.append(section)

    return "\n\n---\n\n".join(sections)
