"""
SQL 血缘分析 — 基于 sqlglot 的 SQL 解析
"""
import logging
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


def extract_sql_lineage(sql: str) -> Dict:
    """从 SQL 语句提取表血缘关系

    Returns: {tables: [{name, alias, role: source|target}], columns: [{table, column, lineage}], graph: {nodes, edges}}
    """
    try:
        import sys
        sys.path.insert(0, "/app/.local")
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer import qualify

        parsed = sqlglot.parse_one(sql)
        if not parsed:
            return _simple_lineage(sql)

        # 提取表引用
        tables = []
        for table in parsed.find_all(exp.Table):
            alias = table.alias_or_name
            tables.append({
                "name": table.name,
                "alias": alias,
                "schema": table.db if hasattr(table, 'db') else None,
                "role": "source",
            })

        # CTE 检测
        ctes = [cte.alias for cte in parsed.find_all(exp.CTE)]

        # 区分 source / target
        if isinstance(parsed, exp.Insert):
            target_table = parsed.this
            tables.append({"name": target_table.name, "alias": None, "role": "target"})
        elif isinstance(parsed, exp.Create):
            target = parsed.find(exp.Table)
            if target:
                tables.append({"name": target.name, "alias": None, "role": "target"})

        # 提取列引用
        columns = []
        for col in parsed.find_all(exp.Column):
            columns.append({"column": col.name, "table": col.table if hasattr(col, 'table') else None})

        # 构建血缘图
        nodes = []
        edges = []
        seen = set()
        for t in tables:
            nid = t["name"]
            if nid not in seen:
                seen.add(nid)
                nodes.append({"id": nid, "label": nid, "type": "table", "role": t.get("role", "source")})

        sources = [t for t in tables if t.get("role") != "target"]
        targets = [t for t in tables if t.get("role") == "target"]
        for src in sources:
            for tgt in targets:
                edge_id = f"{src['name']}_to_{tgt['name']}"
                edges.append({
                    "id": edge_id, "source": src["name"], "target": tgt["name"],
                    "type": "etl", "sql": sql[:200],
                })

        if not targets and len(sources) > 1:
            for i in range(len(sources) - 1):
                for j in range(i + 1, len(sources)):
                    edges.append({
                        "id": f"{sources[i]['name']}_join_{sources[j]['name']}",
                        "source": sources[i]["name"], "target": sources[j]["name"],
                        "type": "join",
                    })

        return {
            "provider": "sqlglot 30.11",
            "tables": tables,
            "columns": columns,
            "ctes": ctes,
            "graph": {"nodes": nodes, "edges": edges},
            "raw_sql": sql[:500],
        }
    except ImportError:
        logger.warning("sqlglot not available, using simple parser")
        return _simple_lineage(sql)
    except Exception as e:
        logger.error(f"SQL lineage extraction failed: {e}")
        return _simple_lineage(sql)


def _simple_lineage(sql: str) -> Dict:
    """简单正则提取（sqlglot 不可用时的 fallback）"""
    import re
    upper = sql.upper()
    tables = []

    # 提取 FROM/JOIN 子句中的表名
    from_pattern = re.findall(r'(?:FROM|JOIN)\s+(\w+(?:\.\w+)?)', upper)
    for t in from_pattern:
        name = t.split('.')[-1].strip('`"').lower()
        tables.append({"name": name, "alias": None, "role": "source"})

    # 提取 INSERT INTO / CREATE TABLE
    target = re.search(r'(?:INSERT\s+INTO|CREATE\s+TABLE)\s+(\w+(?:\.\w+)?)', upper)
    if target:
        name = target.group(1).split('.')[-1].strip('`"').lower()
        tables.append({"name": name, "alias": None, "role": "target"})

    nodes = [{"id": t["name"], "label": t["name"], "type": "table", "role": t.get("role", "source")} for t in tables]
    edges = []
    sources = [t for t in tables if t.get("role") != "target"]
    targets = [t for t in tables if t.get("role") == "target"]
    for src in sources:
        for tgt in targets:
            edges.append({"id": f"{src['name']}_to_{tgt['name']}", "source": src["name"], "target": tgt["name"], "type": "etl"})

    return {
        "provider": "simple_regex",
        "tables": tables,
        "graph": {"nodes": nodes, "edges": edges},
        "raw_sql": sql[:500],
    }


def extract_column_lineage(sql: str) -> List[Dict]:
    """提取列级血缘"""
    import re

    # 简单正则提取 SELECT 中的列和表达式
    select_match = re.search(r'SELECT\s+(.+?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return []

    select_clause = select_match.group(1)
    columns = []
    for part in select_clause.split(','):
        part = part.strip()
        # 匹配 "column AS alias" 或 "expression AS alias"
        m = re.match(r'(.+?)\s+AS\s+(\w+)', part, re.IGNORECASE)
        if m:
            columns.append({"expression": m.group(1).strip(), "alias": m.group(2)})
        else:
            # 简单列名
            col_name = part.split('.')[-1].strip().strip('`"')
            columns.append({"column": col_name, "alias": col_name})

    return columns
