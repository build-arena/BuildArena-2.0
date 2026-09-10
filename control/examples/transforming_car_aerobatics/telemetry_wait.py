import time
from controller_sdk.telemetry_codec import TelemetryCodecError

def next_frame(c):
    deadline=time.monotonic()+1
    while True:
        try:return c.next_sample(timeout=5)
        except TelemetryCodecError as e:
            if 'Could not obtain one stable BAT4/BTM4 commit' not in str(e) or time.monotonic()>deadline:raise

