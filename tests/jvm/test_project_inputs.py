# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[2] / 'tools'))
import project_inputs


class ProjectInputsTest(unittest.TestCase):
    @patch('project_inputs.nix_json')
    def test_nix_identity_is_retained_and_reused_until_update(self, nix):
        nix.return_value = {'url': 'github:example/project/revision?narHash=sha256-hash',
                            'locked': {'narHash': 'sha256-hash'}}
        reference = 'github:example/project#library'
        inputs = project_inputs.pin([reference, reference])
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0]['attribute'], 'library')
        self.assertEqual(inputs[0]['narHash'], 'sha256-hash')
        nix.assert_called_once()
        nix.reset_mock()
        self.assertEqual(project_inputs.pin([reference], inputs), inputs)
        nix.assert_not_called()
        project_inputs.pin([reference], inputs, refresh=True)
        nix.assert_called_once()

    @patch('project_inputs.nix_json')
    def test_unhashed_input_is_rejected(self, nix):
        nix.return_value = {'url': 'indirect:unpinned', 'locked': {}}
        with self.assertRaisesRegex(ValueError, 'content hash'):
            project_inputs.pin(['unpinned'])

    @patch('project_inputs.nix_json')
    def test_repository_and_catalog_are_realized_together(self, nix):
        nix.return_value = [{'outputs': {'out': '/nix/store/repository',
                                        'metadata': '/nix/store/metadata'}}]
        entry = {'url': 'locked-source', 'attribute': 'library', 'narHash': 'sha256-hash'}
        self.assertEqual(project_inputs.maven_views([entry]), [{
            'repository': Path('/nix/store/repository'), 'catalog': Path('/nix/store/metadata/catalog.json')}])
        self.assertEqual(json.loads(nix.call_args.kwargs['env']['PURIFIER_PROJECT_INPUT']), entry)
        self.assertNotIn('locked-source', nix.call_args.args[-1])


if __name__ == '__main__':
    unittest.main()
