import unittest

from app.ai.apd.model import remap_legacy_keys


class ModelKeyMappingTests(unittest.TestCase):
    def test_keep_original_transformers4_keys(self):
        key = 'model.encoder.encoder.0.layers.0.self_attn.out_proj.weight'
        self.assertEqual(remap_legacy_keys({key: 1}, {key}), {key: 1})

    def test_forward_transformers5_keys(self):
        old = 'model.encoder.encoder.0.layers.0.fc1.weight'
        new = 'model.encoder.aifi.0.layers.0.mlp.fc1.weight'
        self.assertEqual(remap_legacy_keys({old: 1}, {new}), {new: 1})

    def test_reverse_transformers5_keys(self):
        old = 'model.encoder.encoder.0.layers.0.self_attn.out_proj.weight'
        new = 'model.encoder.aifi.0.layers.0.self_attn.o_proj.weight'
        self.assertEqual(remap_legacy_keys({new: 1}, {old}), {old: 1})

    def test_collision_rejected(self):
        old = 'model.encoder.encoder.0.layers.0.fc1.weight'
        new = 'model.encoder.aifi.0.layers.0.mlp.fc1.weight'
        with self.assertRaises(ValueError):
            remap_legacy_keys({old: 1, new: 2}, {old})


if __name__ == '__main__':
    unittest.main()
