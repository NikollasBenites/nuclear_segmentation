"""Dependency-light checks; run with python -m unittest discover -s tests -p test_cellpose_options.py."""
import ast
import json
from pathlib import Path
import unittest
from nuclear_segmentation.config import defaults, validate_config

STAGES = Path(__file__).resolve().parents[1] / 'src/nuclear_segmentation/stages'
KEYS = ('CELLPOSE_RESAMPLE', 'CELLPOSE_RESCALE', 'CELLPOSE_NORMALIZE')

def assignments(stage, names, namespace):
    tree = ast.parse((STAGES / (stage + '.py')).read_text())
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), stage, 'exec'), namespace)
    return namespace

class CellposeOptions(unittest.TestCase):
    def test_old_preset_defaults(self):
        c = defaults()
        for k in KEYS: c.pop(k)
        restored = validate_config(c)
        self.assertEqual([restored[k] for k in KEYS], [True, None, True])

    def test_invalid_values(self):
        for key, values in [('CELLPOSE_RESAMPLE', ['false', 0, None]),
                            ('CELLPOSE_RESCALE', [0, -1, True, '0.5', float('nan'), float('inf')]),
                            ('CELLPOSE_NORMALIZE', ['true', None, 1, []])]:
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_config({key: value})

    def test_json_and_eval_dispatch(self):
        for normalize in [True, False, {'normalize': True, 'percentile': [2, 98], 'norm3D': True}]:
            cfg = validate_config(dict(CELLPOSE_RESAMPLE=False, CELLPOSE_RESCALE=0.75, CELLPOSE_NORMALIZE=normalize))
            cfg = validate_config(json.loads(json.dumps(cfg)))
            ns = assignments('segment', {'eval_parameters'}, dict(cfg, ANISOTROPY=2))
            self.assertEqual({k: ns['eval_parameters'][k] for k in ('resample', 'rescale', 'normalize')},
                             {'resample': False, 'rescale': 0.75, 'normalize': normalize})

    def test_export_and_restore(self):
        tree = ast.parse((STAGES/'export.py').read_text())
        cellpose = next(value for node in ast.walk(tree) if isinstance(node, ast.Dict)
                        for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant) and key.value == 'cellpose')
        chosen = [(k,v) for k,v in zip(cellpose.keys,cellpose.values)
                  if isinstance(k,ast.Constant) and k.value in ('resample','rescale','normalize')]
        expr = ast.Expression(body=ast.Dict(keys=[k for k,v in chosen],values=[v for k,v in chosen]))
        cfg = validate_config(dict(CELLPOSE_RESAMPLE=False, CELLPOSE_RESCALE=0.5, CELLPOSE_NORMALIZE={'norm3D': True}))
        saved = eval(compile(ast.fix_missing_locations(expr), 'export', 'eval'), cfg)
        restored = assignments('restore', set(KEYS), {'previous_config': {'cellpose': json.loads(json.dumps(saved))}})
        self.assertEqual([restored[k] for k in KEYS], [cfg[k] for k in KEYS])
        legacy = assignments('restore', set(KEYS), {'previous_config': {'cellpose': {}}})
        self.assertEqual([legacy[k] for k in KEYS], [True, None, True])

if __name__ == '__main__': unittest.main()
