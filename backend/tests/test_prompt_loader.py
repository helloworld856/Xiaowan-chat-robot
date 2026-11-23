import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import _imports
from app import prompt_loader


class PromptLoaderTests(unittest.TestCase):
    def test_load_failure_preserves_original_error_and_does_not_log_success(self):
        cases = (
            ('missing_file', None, FileNotFoundError),
            ('invalid_encoding', b'\xff', UnicodeDecodeError),
            ('missing_variable', b'${missing}', KeyError),
            ('invalid_placeholder', b'${bad-name}', ValueError),
        )

        with TemporaryDirectory() as directory:
            prompts = Path(directory)
            for name, content, expected_error in cases:
                with self.subTest(name=name):
                    if content is not None:
                        (prompts / f'{name}.txt').write_bytes(content)

                    with patch.object(prompt_loader, 'PROMPTS_PATH', prompts):
                        with self.assertLogs(prompt_loader.logger, level='INFO') as logs:
                            with self.assertRaises(expected_error):
                                prompt_loader.load_prompt(name)

                    errors = [record.getMessage() for record in logs.records if record.levelname == 'ERROR']
                    self.assertEqual(len(errors), 1)
                    self.assertIn(name, errors[0])
                    self.assertIn(expected_error.__name__, errors[0])
                    self.assertNotIn('加载成功', '\n'.join(logs.output))
