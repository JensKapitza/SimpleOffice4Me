import csv
import io
import unittest

from app.datalogger_reporting import csv_content, date_window, summarize


class DataLoggerReportingTests(unittest.TestCase):
    def test_window_has_exclusive_next_midnight(self):
        self.assertEqual(date_window('2024-02-29', '2024-02-29'),
                         ('2024-02-29T00:00:00+00:00', '2024-03-01T00:00:00+00:00'))
        for start, end in [('2024-02-30', '2024-02-29'), ('2024-02-29', ''),
                           ('2024-02-29', '2024-02-28'), ('2022-01-01', '2024-02-28')]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                date_window(start, end)

    def test_summary_does_not_include_questionable_samples(self):
        self.assertEqual(summarize([{'value': 18, 'quality': 'good'}, {'value': -200, 'quality': 'invalid'}]),
                         {'rows': 2, 'valid': 1, 'not_ok': 1, 'min': 18, 'max': 18, 'mean': 18})
        self.assertIsNone(summarize([])['mean'])

    def test_csv_utc_and_spreadsheet_safe_labels(self):
        channel = {'channel_id': 'x1', 'name': ' =1+1', 'unit': '°C'}
        rows = [{'measured_at': '2024-02-29T13:00:00+01:00', 'source_name': 'Werkstatt, Süd',
                 'source_kind': 'linux', 'value': -2, 'quality': 'good'}]
        row, = csv.DictReader(io.StringIO(csv_content(channel, rows)))
        self.assertEqual(row['time_utc'], '2024-02-29T12:00:00+00:00')
        self.assertEqual(row['channel'], "' =1+1")
        self.assertEqual(row['source'], 'Werkstatt, Süd')
        self.assertEqual(row['value'], '-2')
