import unittest
from unittest.mock import patch, MagicMock
from native_layers import remove_layer


class NativeLayersTests(unittest.TestCase):
    def test_remove_layer_does_not_crash_with_old_sections_lacking_layer_id(self):
        page = MagicMock()
        page.get_contents.return_value = [1, 2, 3]
        
        # Simulate a corrupted/old section without '_layer_id'
        corrupted = {'_stream': 1, 'text': 'old'}
        
        with patch('native_layers.layers', return_value=[corrupted]):
            section = {'_layer_id': 'new-id'}
            
            # After fix: should raise ValueError (no matches) rather than KeyError
            # This is safer than crashing with KeyError
            with self.assertRaises(ValueError) as context:
                remove_layer(page, section)
            self.assertIn('changed', str(context.exception))


if __name__ == '__main__':
    unittest.main()
