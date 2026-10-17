# -*- coding: utf-8 -*-
"""CER单元测试：增/删/改/空串。python evaluation/test_cer.py 全PASS才算指标可信。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
from metrics import cer, normalize_text, corpus_cer, edit_ops
cases = [
    ("abc", "abc", 0.0, "相同"),
    ("ab", "abc", 1/3, "删除"),
    ("abcd", "abc", 1/3, "插入"),
    ("abd", "abc", 1/3, "替换"),
    ("", "", 0.0, "双空"),
    ("a", "", 1.0, "空GT非空pred"),
    ("", "abc", 1.0, "空pred"),
    ("发票金额12850元", "发票金额128.50元", 1/11, "中文删1字(.缺失)"),
]
ok = True
for pred, gt, exp, name in cases:
    got = cer(pred, gt)
    good = abs(got - exp) < 1e-9
    ok = ok and good
    print(("PASS" if good else "FAIL"), name, "got=%.6f exp=%.6f" % (got, exp))
# 归一化：空格换行不计
assert normalize_text("a b\nc") == "abc", "normalize fail"
assert abs(cer("a b\nc", "abc") - 0.0) < 1e-9, "normalize cer fail"
# corpus
cc = corpus_cer([("abd", "abc"), ("ab", "abc")])
assert cc["S"] == 1 and cc["D"] == 1 and cc["gt_chars"] == 6 and abs(cc["corpus_cer"] - 2/6) < 1e-9, str(cc)
print("PASS corpus", cc)
print("ALL_PASS" if ok else "SOME_FAIL")
raise SystemExit(0 if ok else 1)
