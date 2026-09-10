"""
Append mode has to be able to read back the files it writes.

The CSV carries observation times in one of two shapes, chosen by the
channel's *Use Single Timestamp column* setting: one combined ``timestamp``
column, or separate ``date`` and ``time`` columns. ``build_csv_header``
decides which, ``ObsRecord.to_row`` writes it, and — the part that used to be
missing — ``csv_to_records`` has to read the same one back when append mode
merges today's file with the incoming records.

Reading it unconditionally as date/time raised ``KeyError: 'date'`` on the
default configuration (write mode *append*, *Use Single Timestamp column* on),
which crashed the whole dispatch run the moment a daily file already existed.

Pure functions: no Django, no network, no S3.
"""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from adl_s3_plugin.dispatchers.s3 import (
    S3FileError,
    build_csv_header,
    create_csv_file,
    csv_to_records,
    prepare_csv_files_append_mode,
)
from adl_s3_plugin.s3 import S3Error

UTC = ZoneInfo("UTC")
PARAMS = ["air_temperature", "relative_humidity"]


def obs_time(hour, minute=0):
    return datetime(2026, 3, 14, hour, minute, tzinfo=UTC)


class FakeChannel:
    """The handful of attributes the append-mode preparer actually reads."""

    def __init__(self, prefix="observations"):
        self.timezone = UTC
        self.prefix = prefix

    def get_parameter_mapping_values(self):
        return list(PARAMS)


class FakeClient:
    """An S3 client backed by a dict; a miss raises S3Error like the real one."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get(self, remote_path):
        if remote_path not in self.objects:
            raise S3Error(f"no such key: {remote_path}", status=404)
        return self.objects[remote_path]


def incoming(hour, temperature, humidity, station_id="STN1", wigos_id="0-20000-0-STN1"):
    return {
        "station_id": station_id,
        "wigos_id": wigos_id,
        "timestamp": obs_time(hour),
        "values": {"air_temperature": temperature, "relative_humidity": humidity},
    }


def existing_file(header, rows):
    """Build the bytes of a CSV already sitting in the bucket."""
    from adl_s3_plugin.dispatchers.s3 import ObsRecord

    records = [
        ObsRecord(
            station_id=r["station_id"],
            wigos_id=r["wigos_id"],
            timestamp=pd.Timestamp(r["timestamp"]),
            values=r["values"],
        )
        for r in rows
    ]
    return create_csv_file(records, header, UTC, include_header=True).getvalue()


class CsvRoundTripTests(unittest.TestCase):
    """What to_row writes, csv_to_records must read — in both header shapes."""

    def assert_round_trips(self, use_single_timestamp):
        header = build_csv_header(PARAMS, include_wigos_id=True,
                                  use_single_timestamp=use_single_timestamp)
        rows = [incoming(6, 21.5, 80), incoming(7, 22.0, 78)]
        content = existing_file(header, rows)

        records = csv_to_records(content, header, PARAMS, UTC)

        self.assertEqual([r.timestamp for r in records], [obs_time(6), obs_time(7)])
        self.assertEqual([r.station_id for r in records], ["STN1", "STN1"])
        self.assertEqual(records[0].values["air_temperature"], 21.5)
        self.assertEqual(records[1].values["relative_humidity"], 78)

    def test_single_timestamp_column_round_trips(self):
        # The regression: this raised KeyError('date') before the fix.
        self.assert_round_trips(use_single_timestamp=True)

    def test_separate_date_and_time_columns_round_trip(self):
        self.assert_round_trips(use_single_timestamp=False)

    def test_unparseable_timestamp_is_reported_as_a_file_error(self):
        header = build_csv_header(PARAMS, include_wigos_id=True, use_single_timestamp=True)
        content = (
            b"station_id,wigos_id,timestamp,air_temperature,relative_humidity\n"
            b"STN1,0-20000-0-STN1,not-a-timestamp,21.5,80\n"
        )

        with self.assertRaises(S3FileError):
            csv_to_records(content, header, PARAMS, UTC)


class AppendMergeTests(unittest.TestCase):
    """The merge itself, in the configuration the model ships with."""

    def prepare(self, use_single_timestamp, objects=None):
        channel = FakeChannel()
        client = FakeClient(objects)
        header = build_csv_header(PARAMS, include_wigos_id=True,
                                  use_single_timestamp=use_single_timestamp)
        return channel, client, header

    def test_merges_into_an_existing_single_timestamp_file(self):
        """
        Default configuration: append + single timestamp, with today's file
        already in the bucket. Two of the three incoming records are already
        there; only the new one is added, and nothing is duplicated.
        """
        channel, client, header = self.prepare(use_single_timestamp=True)
        remote_path = "observations/0-20000-0-STN1/WIGOS_0-20000-0-STN1_20260314.csv"
        client.objects[remote_path] = existing_file(
            header, [incoming(6, 21.5, 80), incoming(7, 22.0, 78)]
        )

        csv_files = prepare_csv_files_append_mode(
            [incoming(7, 22.0, 78), incoming(8, 23.1, 75)],
            channel, client, use_single_timestamp=True,
        )

        self.assertEqual(len(csv_files), 1)
        written, path = csv_files[0]
        self.assertEqual(path, remote_path)

        merged = csv_to_records(written.getvalue(), header, PARAMS, UTC)
        self.assertEqual(
            [r.timestamp for r in merged], [obs_time(6), obs_time(7), obs_time(8)]
        )
        self.assertEqual(merged[2].values["air_temperature"], 23.1)

    def test_nothing_is_uploaded_when_every_incoming_record_is_already_there(self):
        channel, client, header = self.prepare(use_single_timestamp=True)
        remote_path = "observations/0-20000-0-STN1/WIGOS_0-20000-0-STN1_20260314.csv"
        client.objects[remote_path] = existing_file(header, [incoming(6, 21.5, 80)])

        csv_files = prepare_csv_files_append_mode(
            [incoming(6, 21.5, 80)], channel, client, use_single_timestamp=True,
        )

        self.assertEqual(csv_files, [])

    def test_creates_the_file_when_none_exists_yet(self):
        channel, client, header = self.prepare(use_single_timestamp=True)

        csv_files = prepare_csv_files_append_mode(
            [incoming(6, 21.5, 80)], channel, client, use_single_timestamp=True,
        )

        self.assertEqual(len(csv_files), 1)
        written, _ = csv_files[0]
        records = csv_to_records(written.getvalue(), header, PARAMS, UTC)
        self.assertEqual([r.timestamp for r in records], [obs_time(6)])

    def test_date_time_configuration_still_merges(self):
        channel, client, header = self.prepare(use_single_timestamp=False)
        remote_path = "observations/0-20000-0-STN1/WIGOS_0-20000-0-STN1_20260314.csv"
        client.objects[remote_path] = existing_file(header, [incoming(6, 21.5, 80)])

        csv_files = prepare_csv_files_append_mode(
            [incoming(7, 22.0, 78)], channel, client, use_single_timestamp=False,
        )

        self.assertEqual(len(csv_files), 1)
        merged = csv_to_records(csv_files[0][0].getvalue(), header, PARAMS, UTC)
        self.assertEqual([r.timestamp for r in merged], [obs_time(6), obs_time(7)])


if __name__ == "__main__":
    unittest.main()
