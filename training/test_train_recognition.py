import importlib.util
import unittest


class TrainingContracts(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('training.train_recognition'),
                             'Training implementation must exist')
        from training import train_recognition
        return train_recognition

    def test_ctc_blank_and_repeats(self):
        m = self.api()
        self.assertEqual(m.ctc_decode([1, 1, 0, 1, 2, 2, 0], ['blank', '中', '文']), '中中文')
        self.assertEqual(m.min_ctc_steps([1, 1, 2, 2]), 6)

    def test_unknown_characters_rejected(self):
        m = self.api()
        with self.assertRaises(ValueError):
            m.encode_text('中?', ['blank', '中'])
        self.assertEqual(m.encode_text('中中', ['blank', '中']), [1, 1])

    def test_holdout_leakage_rejected(self):
        m = self.api()
        row = {'source_image_id': 'dev-a', 'source_split': 'dev', 'group_id': 'document-a', 'split': 'train', 'text': '中文'}
        other = dict(row, source_image_id='dev-b', split='val')
        with self.assertRaises(ValueError):
            m.validate_rows([row], [other])
        with self.assertRaises(ValueError):
            m.validate_rows([dict(row, split='test')], [dict(other, group_id='b')])
        with self.assertRaises(ValueError):
            m.validate_rows([], [other])

    def test_test_ids_cannot_masquerade_as_development(self):
        m = self.api()
        row = {'source_image_id': 'web_board_hf01.png', 'source_split': 'dev', 'group_id': 'a',
               'split': 'train', 'text': '中文'}
        val = dict(row, source_image_id='real-001.jpg', group_id='b', split='val')
        with self.assertRaises(ValueError):
            m.validate_rows([row], [val], dev_ids={'real-001.jpg'})
        row['source_image_id'] = 'real-002.jpg'
        del row['source_split']
        with self.assertRaises(ValueError):
            m.validate_rows([row], [val], dev_ids={'real-001.jpg', 'real-002.jpg'})

    def test_ctc_step_changes_parameters_and_learns(self):
        m = self.api()
        import torch
        torch.manual_seed(7)
        torch.set_num_threads(2)
        layer = torch.nn.Linear(2, 3)
        optimizer = torch.optim.Adam(layer.parameters(), lr=0.08)
        features = torch.tensor([[[1., 0.], [1., 0.], [0., 1.], [0., 1.]]])
        labels = [[1, 2]]
        before = layer.weight.detach().clone()
        losses = [m.train_one_batch(layer, optimizer, features, [4], labels) for _ in range(30)]
        self.assertLess(losses[-1], losses[0])
        self.assertFalse(torch.equal(before, layer.weight.detach()))


if __name__ == '__main__':
    unittest.main()
