import json
from pathlib import Path
import tempfile
import unittest

try:
    from training import prepare_data as prep
except ImportError:
    prep = None


class AvailabilityTest(unittest.TestCase):
    def test_preparation_module_exists(self):
        self.assertIsNotNone(prep, 'Training preparation implementation is missing')


@unittest.skipIf(prep is None, 'Implementation not present yet')
class DataPreparationTest(unittest.TestCase):
    def test_manual_rejections_and_unreviewed_slide_substrings_are_excluded(self):
        self.assertTrue(hasattr(prep, 'candidate_exclusion_reason'))
        self.assertEqual(prep.candidate_exclusion_reason({'document_type': '书籍'},
                         {'alignment_method': 'exact_human_gt_line'}, 'bad', {'bad'}), 'manual_visual_rejection')
        self.assertEqual(prep.candidate_exclusion_reason({'document_type': '课件'},
                         {'alignment_method': 'exact_human_gt_substring'}, 'other', set()),
                         'slide_substring_requires_full_visual_review')
        self.assertIsNone(prep.candidate_exclusion_reason({'document_type': '书籍'},
                          {'alignment_method': 'exact_human_gt_line'}, 'good', {'bad'}))

    def test_only_requested_dev_prefix_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'metadata.jsonl'
            path.write_text(json.dumps({'image_id': 'a', 'split': 'dev'}) + '\n'
                            + 'DO NOT READ THE LOCKED TEST RECORD\n', encoding='utf-8')
            self.assertEqual(prep.load_dev_rows(path, limit=1)[0]['image_id'], 'a')

    def test_test_record_in_dev_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'metadata.jsonl'
            path.write_text(json.dumps({'image_id': 'x', 'split': 'test'}) + '\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                prep.load_dev_rows(path, limit=1)

    def test_hash_source_and_near_duplicate_edges_are_transitive(self):
        text = '甲乙丙丁戊己庚辛壬癸' * 12
        rows = [
            {'image_id': 'a', 'sha256': 'same', 'gt_text': text, 'document_type': 'book'},
            {'image_id': 'b', 'sha256': 'same', 'source_group': 'capture', 'gt_text': text, 'document_type': 'book'},
            {'image_id': 'c', 'source_group': 'capture', 'gt_text': 'other', 'document_type': 'book'},
            {'image_id': 'd', 'gt_text': text[:-4] + '改写结尾', 'document_type': 'book'},
            {'image_id': 'e', 'gt_text': '完全不同的独立文本' * 10, 'document_type': 'book'},
        ]
        groups, edges = prep.group_documents(rows)
        self.assertEqual(len({groups[k] for k in 'abcd'}), 1)
        self.assertNotEqual(groups['a'], groups['e'])
        self.assertTrue(any(e['reason'] == 'sha256' for e in edges))

    def test_group_split_is_deterministic_and_never_splits_a_group(self):
        rows = [{'image_id': str(i), 'document_type': 'book'} for i in range(10)]
        groups = {str(i): f'g{i // 2}' for i in range(10)}
        first = prep.assign_splits(rows, groups, seed=17)
        self.assertEqual(first, prep.assign_splits(rows, groups, seed=17))
        self.assertEqual(set(first), set(groups.values()))
        self.assertEqual(set(first.values()), {'train', 'val'})

    def test_exact_substring_label_is_sliced_from_human_gt(self):
        hit = prep.exact_alignment('人工智能技术', '前言\n现代人工智能技术正在发展。\n结语')
        self.assertEqual(hit['text'], '人工智能技术')
        self.assertIn('gt_substring', hit['alignment_method'])

    def test_ambiguous_unknown_and_unsupported_predictions_are_rejected(self):
        self.assertIsNone(prep.exact_alignment('重复的文字', '重复的文字\n重复的文字'))
        self.assertIsNone(prep.exact_alignment('未知的文字', '[UNK]未知的文字'))
        self.assertIsNone(prep.exact_alignment('错误预测文字', '人工正确标注'))

    def test_fuzzy_candidate_retains_human_label_and_requires_review(self):
        line = {'text': '今天我们开始学刁人工智能技术', 'confidence': 0.99, 'box': [[0, 0], [20, 0], [20, 5], [0, 5]]}
        candidates = prep.fuzzy_alignments([line], '今天我们开始学习人工智能技术')
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['text'], '今天我们开始学习人工智能技术')
        self.assertEqual(candidates[0]['annotation_status'], 'review_required')

    def test_fuzzy_matching_rejects_close_runners_up_and_reused_gt(self):
        gt = '今天我们开始学习人工智能技术\n今天我们开始学习人工智能应用'
        lines = [{'text': '今天我们开始学习人工智能方法', 'confidence': 0.99}]
        self.assertEqual(prep.fuzzy_alignments(lines, gt), [])
        lines = [{'text': '今天我们开始学刁人工智能技术', 'confidence': 0.99}] * 2
        candidates = prep.fuzzy_alignments(lines, gt.splitlines()[0])
        self.assertLessEqual(len(candidates), 1)

    def test_thumbnail_mapping_uses_actual_integer_dimensions(self):
        box = [[1, 2], [100, 2], [100, 20], [1, 20]]
        scaled = prep.scale_box(box, (2840, 3864), (1911, 2600))
        self.assertAlmostEqual(scaled[1][0], 100 * 2840 / 1911)
        self.assertAlmostEqual(scaled[0][1], 2 * 3864 / 2600)

    def test_fuzzy_matching_does_not_reuse_a_gt_line_already_matched_exactly(self):
        lines = [{'text': '今天我们开始学习人工智能技术'},
                 {'text': '今天我们开始学刁人工智能技术'}]
        self.assertEqual(prep.fuzzy_alignments(lines, '今天我们开始学习人工智能技术', {0}), [])

    def test_quad_crop_covers_the_correct_pixels_in_original_coordinates(self):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (100, 80), 'white')
        ImageDraw.Draw(image).rectangle((20, 10, 70, 30), fill='red')
        crop = prep.crop_quad(image, [[20, 10], [70, 10], [70, 30], [20, 30]])
        self.assertEqual(crop.size, (50, 20))
        self.assertEqual(crop.getpixel((25, 10)), (255, 0, 0))

    def test_seen_validation_labels_are_excluded_without_changing_train(self):
        train = [{'text': '中国大学MOOC'}, {'text': '保留训练文本'}]
        val = [{'text': '中国大学 MOOC'}, {'text': '独立验证文本'}]
        kept, excluded = prep.drop_seen_validation_labels(train, val)
        self.assertEqual([r['text'] for r in kept], ['独立验证文本'])
        self.assertEqual(len(excluded), 1)
        self.assertEqual(len(train), 2)

    def test_every_crop_provenance_explicitly_binds_it_to_a_dev_source(self):
        builder = getattr(prep, 'source_provenance', None)
        self.assertIsNotNone(builder, 'Explicit DEV provenance builder is missing')
        row = {'image_id': 'real-001.jpg', 'image_path': 'dev/dev/111/real-001.jpg',
               'sha256': 'known-image-hash', 'split': 'dev'}
        provenance = builder(row)
        self.assertEqual(provenance['source_split'], 'dev')
        self.assertEqual(provenance['source_image_id'], row['image_id'])
        self.assertEqual(provenance['source_sha256'], row['sha256'])
        self.assertEqual(provenance['source_image_path'], row['image_path'])
        with self.assertRaises(ValueError):
            builder(dict(row, split='test'))


if __name__ == '__main__':
    unittest.main()
