"""行级别文本差异对比工具（类 Git diff）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class DiffResult:
    v1: int = 0
    v2: int = 0
    additions: List[str] = None
    deletions: List[str] = None
    unchanged: List[str] = None

    def __post_init__(self):
        if self.additions is None:
            self.additions = []
        if self.deletions is None:
            self.deletions = []
        if self.unchanged is None:
            self.unchanged = []


def compute_diff(text_v1: str, text_v2: str) -> DiffResult:
    """逐行对比两个文本，返回差异结构。
    
    采用最长公共子序列（LCS）算法确定 unchanged 行，
    其余行标记为 additions 或 deletions。
    
    Returns:
        DiffResult:包含 additions（新增行）、deletions（删除行）、
                   unchanged（未变行）的结构。
    """
    lines1 = text_v1.splitlines(keepends=False)
    lines2 = text_v2.splitlines(keepends=False)
    
    # LCS DP 表
    m, n = len(lines1), len(lines2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if lines1[i - 1] == lines2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    
    # 回溯找出 LCS
    additions = []
    deletions = []
    unchanged = []
    
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and lines1[i - 1] == lines2[j - 1]:
            unchanged.insert(0, lines1[i - 1])
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            additions.insert(0, lines2[j - 1])
            j -= 1
        else:
            deletions.insert(0, lines1[i - 1])
            i -= 1
    
    return DiffResult(
        additions=additions,
        deletions=deletions,
        unchanged=unchanged,
    )


def format_unified_diff(text_v1: str, text_v2: str, v1_label: str = "v1", v2_label: str = "v2") -> str:
    """生成 unified diff 格式字符串（用于展示）。"""
    result = compute_diff(text_v1, text_v2)
    lines = []
    lines.append(f"--- {v1_label}")
    lines.append(f"+++ {v2_label}")
    
    #合并 unchanged 和变更行，生成统一格式
    all_lines = []
    for line in result.unchanged:
        all_lines.append((" ", line))
    for line in result.deletions:
        all_lines.append(("-", line))
    for line in result.additions:
        all_lines.append(("+", line))
    
    # 按原始顺序排列（通过位置标记模拟）
    #简化处理：直接分行输出
    for kind, line in [("--- deleted", l) for l in result.deletions] + \
                      [("+++ added", l) for l in result.additions] + \
                      [(" unchanged", l) for l in result.unchanged]:
        lines.append(f"{kind}: {line}")
    
    return "\n".join(lines)