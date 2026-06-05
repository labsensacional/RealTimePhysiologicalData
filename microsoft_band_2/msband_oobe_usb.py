#!/usr/bin/env python3
"""
Read or escape Microsoft Band 2 OOBE over USB using msband-lib-9th.

Usage:
    .venv-msband/bin/python msband_oobe_usb.py --status
    .venv-msband/bin/python msband_oobe_usb.py --unlock
"""
import argparse
import datetime as dt
import os
import sys


MSBAND_LIB_PATH = os.environ.get("MSBAND_LIB_PATH", "/tmp/msband-lib-9th/src")
sys.path.insert(0, MSBAND_LIB_PATH)

from msband.protocol import USBInterface  # noqa: E402
from msband.static import FirmwareApp, Profile  # noqa: E402
from msband.static.command import (  # noqa: E402
    CoreModuleWhoAmI,
    FireballUINavigateToScreen,
    OobeFinalize,
    OobeGetStage,
    OobeSetStage,
    ProfileGetDataApp,
    ProfileSetDataApp,
    SystemSettingsOobeCompleteGet,
    SystemSettingsSetEphemerisFile,
    SystemSettingsSetTimeZone,
    TimeSetUtcTime,
)
from msband.static.oobe import OobeStage  # noqa: E402
from msband.static.screen import Screen  # noqa: E402
from msband.static.timezone import GMT  # noqa: E402


def connect_band():
    return USBInterface("")


def read_status(iband):
    whoami = iband.command(CoreModuleWhoAmI)
    oobe_complete = iband.command(SystemSettingsOobeCompleteGet)
    stage = iband.command(OobeGetStage)
    print(f"WhoAmI: {whoami}")
    print(f"OOBE complete: {oobe_complete}")
    print(f"OOBE stage: {stage}")
    return whoami, oobe_complete, stage


def escape_oobe(iband):
    whoami, oobe_complete, stage = read_status(iband)

    if whoami != FirmwareApp.App:
        raise RuntimeError(f"Band is not running the main app firmware: {whoami}")

    if oobe_complete:
        print("Band is already out of OOBE.")
        return

    if stage == OobeStage.PreStateCharging:
        raise RuntimeError("Band is in PreStateCharging; put it on the setup screen first.")

    if stage == OobeStage.PreStateLanguageSelect:
        raise RuntimeError("Select a language on the Band before running unlock.")

    print("Reading profile...")
    profile: Profile = iband.command(ProfileGetDataApp)

    print("Advancing OOBE update stages...")
    iband.command(OobeSetStage, Stage=OobeStage.CheckingForUpdate)
    iband.command(OobeSetStage, Stage=OobeStage.StartingUpdate)
    iband.command(OobeSetStage, Stage=OobeStage.UpdateComplete)

    now = dt.datetime.now(dt.timezone.utc)

    print("Setting UTC time...")
    iband.command(TimeSetUtcTime, NewTime=now)

    print("Navigating to OOBE boot screen...")
    iband.command(FireballUINavigateToScreen, Screen=Screen.OobeBoot)

    print("Writing dummy ephemeris file...")
    iband.command(SystemSettingsSetEphemerisFile, Data=b"\0" * 130)

    print("Setting timezone...")
    iband.command(SystemSettingsSetTimeZone, TimeZone=GMT)

    print("Setting final OOBE stage...")
    iband.command(OobeSetStage, Stage=OobeStage.WaitingOnPhoneToCompleteOobe)

    print("Updating profile...")
    profile.DeviceName = "Liberated Band"
    profile.Telemetry = False
    profile.LastSync = now
    profile.HwagChangeTime = now
    profile.DeviceNameChangeTime = now
    profile.LocaleSettingsChangeTime = now
    profile.LanguageChangeTime = now
    iband.command(ProfileSetDataApp, Profile=profile)

    print("Finalizing OOBE...")
    iband.command(OobeFinalize)

    print("Done. Reading final status...")
    read_status(iband)


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--status", action="store_true", help="read status only")
    action.add_argument("--unlock", action="store_true", help="attempt USB OOBE escape")
    args = parser.parse_args()

    iband = connect_band()
    if args.status:
        read_status(iband)
    else:
        escape_oobe(iband)


if __name__ == "__main__":
    main()
