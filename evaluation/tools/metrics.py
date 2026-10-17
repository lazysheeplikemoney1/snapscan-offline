# -*- coding: utf-8 -*-
"""CER指标规范（冻结版）.
文本归一化（所有实验统一，实验中途不许改）：
- 换行 \\n / \\r：删除（不计入编辑距离，避免版式换行污染指标）
- 空格/制表/全角空格：删除（中文OCR空格不稳定）
- 英文字母：不做大小写归一化（保持原样，报告注明）
- 标点：计入（中英文标点视为普通字符）
- 中文：按Unicode字符计
CER = (S+D+I)/N，N=len(GT归一化后)。corpus_cer = sum(S+D+I)/sum(N)。
[UNK] 行：整行含[UNK]则该行跳过（真实数据用，synthetic无此行）。
"""
import re

def normalize_text(s):
    if s is None:
        return ""
    s = str(s)
    # 含[UNK]的行跳过
    lines = []
    for ln in s.splitlines():
        if "[UNK]" in ln:
            continue
        lines.append(ln)
    s = "".join(lines)
    s = s.replace("\r", "").replace("\n", "")
    s = s.replace(" ", "").replace("\t", "").replace("　", "")
    return s

def edit_ops(pred, gt):
    """返回 (S,D,I)。标准Levenshtein DP + 回溯。"""
    n, m = len(gt), len(pred)
    if n == 0:
        return (0, 0, m)
    if m == 0:
        return (0, n, 0)
    dp = [[0]*(m+1) for _ in range(n+1)]
    for i in range(n+1): dp[i][0] = i
    for j in range(m+1): dp[0][j] = j
    for i in range(1, n+1):
        gi = gt[i-1]
        row, prow = dp[i], dp[i-1]
        for j in range(1, m+1):
            cost = 0 if gi == pred[j-1] else 1
            row[j] = min(prow[j]+1, row[j-1]+1, prow[j-1]+cost)
    # 回溯
    i, j = n, m
    S = D = I = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and gt[i-1] == pred[j-1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i-1][j-1] + 1:
            S += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i-1][j] + 1:
            D += 1; i -= 1
        elif j > 0 and dp[i][j] == dp[i][j-1] + 1:
            I += 1; j -= 1
        else:
            # 兜底（理论上不应到）
            if i > 0 and j > 0:
                S += 1; i -= 1; j -= 1
            elif i > 0:
                D += 1; i -= 1
            else:
                I += 1; j -= 1
    return (S, D, I)

def cer(pred, gt, normalize=True):
    """单图CER（兼容老接口）。normalize=True时先归一化。"""
    if normalize:
        pred = normalize_text(pred)
        gt = normalize_text(gt)
    n = len(gt)
    if n == 0:
        return 0.0 if len(pred) == 0 else 1.0
    S, D, I = edit_ops(pred, gt)
    return (S + D + I) / n

def corpus_cer(pairs, normalize=True):
    """pairs: [(pred, gt), ...]。返回 dict(S,D,I,gt_chars,corpus_cer)。"""
    S = D = I = N = 0
    for pred, gt in pairs:
        if normalize:
            pred = normalize_text(pred)
            gt = normalize_text(gt)
        if len(gt) == 0:
            continue
        s, d, i = edit_ops(pred, gt)
        S += s; D += d; I += i; N += len(gt)
    return {"S": S, "D": D, "I": I, "gt_chars": N,
            "corpus_cer": ((S + D + I) / N) if N else 0.0}

def field_f1(pred_fields, gt_fields):
    keys = set(pred_fields) | set(gt_fields)
    tp = sum(1 for k in keys if k in pred_fields and k in gt_fields and str(pred_fields[k]).strip() == str(gt_fields[k]).strip())
    prec = tp / max(1, len(pred_fields))
    rec = tp / max(1, len(gt_fields))
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    return {"precision": prec, "recall": rec, "f1": f1}
