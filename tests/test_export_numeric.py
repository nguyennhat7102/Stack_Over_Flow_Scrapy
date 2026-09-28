"""Behavioral tests for numeric itemset export, independent of crawl/network."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.export_numeric import export_numeric


def record(qid, tags):
    return dict(question_id=qid, tags=tags, title='Example',
                creation_date='2023-01-01T00:00:00Z', score=0, answer_count=0)


class NumericExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.jsonl'
        self.output = self.root / 'numeric.jsonl'
        self.mapping = self.root / 'mapping.json'

    def write_source(self, rows):
        self.source.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def export(self, format='jsonl'):
        return export_numeric(self.source, self.output, self.mapping, format=format)

    def test_roundtrip_preserves_repeated_baskets_without_metadata(self):
        rows = [record(1, ['python', 'java']), record(2, ['java', 'python']),
                record(3, ['scrapy', 'python'])]
        self.write_source(rows)
        original = self.source.read_bytes()
        result = self.export()
        mapping = json.loads(self.mapping.read_text())
        output = [json.loads(line) for line in self.output.read_text().splitlines()]
        self.assertEqual(mapping, {'python': 1, 'java': 2, 'scrapy': 3})
        self.assertEqual(output, [[1, 2], [1, 2], [1, 3]])
        self.assertEqual(result['transactions'], 3)
        self.assertEqual(self.source.read_bytes(), original)

    def test_mapping_stable_when_source_order_changes_or_tags_added(self):
        self.write_source([record(1, ['python', 'java'])])
        self.export()
        old = json.loads(self.mapping.read_text())
        self.write_source([record(2, ['c++', 'java']), record(1, ['java', 'python'])])
        self.export()
        new = json.loads(self.mapping.read_text())
        self.assertEqual({tag: new[tag] for tag in old}, old)
        self.assertEqual(new['c++'], 3)
        first = self.output.read_bytes()
        self.export()
        self.assertEqual(self.output.read_bytes(), first)

    def test_txt_contains_only_space_separated_integers(self):
        self.write_source([record(1, ['python', 'java'])])
        self.export(format='txt')
        self.assertEqual(self.output.read_text(), '1 2\n')

    def test_bad_source_keeps_existing_outputs(self):
        self.write_source([record(1, ['python', 'java'])])
        self.export()
        old_output, old_mapping = self.output.read_bytes(), self.mapping.read_bytes()
        for content in ('', '{bad\n', json.dumps(record(1, ['python', 'python']))+'\n',
                        '\n'.join([json.dumps(record(1, ['python','java']))]*2)+'\n'):
            with self.subTest(content=content):
                self.source.write_text(content)
                with self.assertRaises(ValueError): self.export()
                self.assertEqual(self.output.read_bytes(), old_output)
                self.assertEqual(self.mapping.read_bytes(), old_mapping)

    def test_invalid_mapping_rejected(self):
        self.write_source([record(1, ['python', 'java'])])
        for mapping in ({'python': 1, 'java': 1}, {'python': True}, {'Python': 1}, []):
            with self.subTest(mapping=mapping):
                self.mapping.write_text(json.dumps(mapping))
                with self.assertRaises(ValueError): self.export()
                self.assertFalse(self.output.exists())

    def test_source_and_output_aliases_rejected(self):
        self.write_source([record(1, ['python', 'java'])])
        before = self.source.read_bytes()
        for output, mapping in ((self.source,self.mapping), (self.output,self.source),
                                (self.output,self.output)):
            with self.assertRaises(ValueError):
                export_numeric(self.source,output,mapping)
        self.output.hardlink_to(self.source)
        with self.assertRaises(ValueError): self.export()
        self.assertEqual(self.source.read_bytes(), before)

    def test_source_change_aborts_without_publishing(self):
        from unittest.mock import patch
        from StackOverFlow.validation import validate_record
        self.write_source([record(1, ['python', 'java'])])
        mutated = False

        def validate_and_append(row):
            nonlocal mutated
            validate_record(row)
            if not mutated:
                mutated = True
                with self.source.open('a') as fh:
                    fh.write(json.dumps(record(2, ['python', 'scrapy'])) + '\n')

        with patch('tools.export_numeric.validate_record', side_effect=validate_and_append):
            with self.assertRaisesRegex(ValueError, 'Input changed'):
                self.export()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.mapping.exists())

    def test_partial_publication_keeps_old_baskets_decodable(self):
        import os
        from unittest.mock import patch
        self.write_source([record(1, ['python', 'java'])])
        self.export()
        old_output = self.output.read_bytes()
        old_mapping = json.loads(self.mapping.read_text())
        self.write_source([record(1, ['python', 'java']), record(2, ['scrapy', 'java'])])
        real_replace = os.replace

        def fail_basket_publish(src, dst):
            if Path(dst) == self.output:
                raise OSError('simulated publication failure')
            return real_replace(src, dst)

        with patch('tools.export_numeric.os.replace', side_effect=fail_basket_publish):
            with self.assertRaises(OSError): self.export()
        self.assertEqual(self.output.read_bytes(), old_output)
        mapping = json.loads(self.mapping.read_text())
        self.assertEqual({tag: mapping[tag] for tag in old_mapping}, old_mapping)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])
