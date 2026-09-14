"""Opt-in real feed smoke test: RUN_REAL_FEED=1 pytest tests/api/test_realtime_integration.py."""

import os
from urllib.request import Request, urlopen

import pytest
from google.transit import gtfs_realtime_pb2


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_FEED") != "1", reason="live upstream test is opt-in"
)
@pytest.mark.parametrize("path", ["vehiclePositions", "tripUpdates", "serviceAlerts"])
def test_real_uga_realtime_payload_decodes(path: str):
    url = f"https://passio3.com/uga/passioTransit/gtfs/realtime/{path}"
    with urlopen(
        Request(url, headers={"User-Agent": "UGABusTest/0.1"}), timeout=20
    ) as response:
        payload = response.read()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(payload)
    assert feed.header.gtfs_realtime_version
