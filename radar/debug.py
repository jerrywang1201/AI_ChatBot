# radar_desc_summary_only.py
import os
import sys
from radarclient import RadarClient
from radarclient.authenticationstrategy import AuthenticationStrategyAppleConnect

if len(sys.argv) < 2:
    print("Usage: python3 radar_desc_summary_only.py <RADAR_ID>")
    sys.exit(1)

rid = int(sys.argv[1])


auth = AuthenticationStrategyAppleConnect()
auth.appleconnect_username = os.environ["APPLECONNECT_USER"]
auth.appleconnect_password = os.environ["APPLECONNECT_PASS"]
rc = RadarClient(authentication_strategy=auth)


radar = rc.radars_for_ids([rid])[0]


summary = radar.description.summary
if callable(summary):
    summary = summary()


if hasattr(summary, "text"):
    summary = summary.text

print(f"Title: {radar.title}")
print("Description summary:")
print(summary)