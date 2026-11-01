"""Contract tests without GUI or third-party packages."""
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from gs1_batch_contract import verify_dm_batch_database


def fmt(config, fields="tmp_batch.tmp_batch.DM"):
    class FakeDatabases:
        Count = 1
        Configuration = config

        def GetDatabase(self, identifier):
            raise RuntimeError("BarTender cannot find database 12345678")

    return SimpleNamespace(
        Databases=FakeDatabases(),
        UsedNames=lambda kind, sep: fields if kind == 1 else "",
    )


class StandaloneGS1PreflightTests(unittest.TestCase):
    def setUp(self):
        self.logger = Mock()
        self.xml = '<Configuration><RecordSet Name="tmp_batch"/></Configuration>'

    def write_csv(self, path, codes):
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["ShortName", "ShortGTIN", "EXP_DATE",
                            "PROD_DATE", "PART_NUM", "DM", "NUM"],
                delimiter="\t",
            )
            writer.writeheader()
            for n, dm in enumerate(codes, 1):
                writer.writerow({"DM": dm, "NUM": n})
    
    def test_importable_without_gui_and_validates_two_distinct_records(self):
        codes = [
            "010460718695152021" + "123456789ABCD" + "\x1d93" + "ABCD",
            "010460718695152021" + "123456789ABCE" + "\x1d93" + "WXYZ",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self.write_csv(path, codes)
            rows = [{"DM": code} for code in codes]
            self.assertTrue(verify_dm_batch_database(
                fmt(self.xml), str(path), self.logger, rows
            ))
            self.assertFalse(verify_dm_batch_database(
                fmt(self.xml), str(path), self.logger, rows[::-1]
            ))

    def test_invalid_source_and_stale_bindings_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tmp_batch.csv"
            self.write_csv(path, ["CODE-1", "CODE-1"])
            self.assertFalse(verify_dm_batch_database(
                fmt(self.xml), str(path), self.logger
            ))
            self.write_csv(path, ["CODE-1", "CODE-2"])
            self.assertFalse(verify_dm_batch_database(
                fmt(self.xml, "wrong.tmp_batch.DM"), str(path), self.logger
            ))
            stale = '<Configuration><RecordSet Name="tmp_batch"/><FileName>stale.csv</FileName></Configuration>'
            self.assertFalse(verify_dm_batch_database(
                fmt(stale), str(path), self.logger
            ))


if __name__ == "__main__":
    unittest.main()
