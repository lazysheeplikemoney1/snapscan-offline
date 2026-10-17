# 真实数据规范（冻结版 v1）

## 规模：130 张（取代原来含糊的"100张"）
- 课件 30 / 书籍 30 / 票据 30 / 黑板白板 30 / 难例 10（反光/褶皱/模糊/低照度至少各2）
- 实际 30*4+10=130，不要再写100。

## 目录（物理隔离，合成不混入）
```
data/synthetic/            # 50张合成，仅回归/smoke，不进最终报告指标
data/real/dev/             # ~90张：调参、找失败案例，只能从这里调 CLAHE/阈值
data/real/test/            # ~40张：锁定后一次性测试，最终CER只从这里出
```
建议 dev:test = 90:40，每类按比例分层抽。

## 每张图 metadata（metadata.jsonl，一行一张）
| 字段 | 取值 |
|---|---|
| image_id | 如 real_001.jpg |
| document_type | 课件/书籍/票据/黑板白板 |
| difficulty | 正常/透视/阴影/反光/模糊/低照度/褶皱 |
| perspective | none/mild/strong |
| shadow/glare/blur | 0/1 |
| handwriting | 0/1 |
| split | dev/test |
| gt_text | 逐行文本（换行用\n） |

## 标注规则
1. 先拍 dev 90 张，标完再抽 test 40 张并上锁（上锁后不许调参）。
2. 票据脱敏：涂掉姓名电话身份证，只留金额日期。
3. gt逐字转录，不脑补；模糊到看不清的字标 [UNK]，评测时跳过该行。
4. 每个 difficulty 至少保证 test 里有2张，否则分层失效。

## 验收
- [ ] metadata.jsonl 130行可解析
- [ ] dev/test无泄漏（文件名不重叠）
- [ ] 脱敏检查通过
