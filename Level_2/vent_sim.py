#!/usr/bin/env python3
"""
BACnet Ventilation Simulator (BACpypes3)

I/O
---
Inputs (Sensors)
  - room_temperature_c  (AI:1)  — degrees Celsius
  - carbon_dioxide_ppm  (AI:2)  — parts per million

Outputs (Controls)
  - fan_command_on      (BO:1)  — False/True
  - damper_open_percent (AO:1)  — 0..100 %

To start simulation:
    > python vent_sim.py
"""

import asyncio
import random
import yaml
from pathlib import Path

from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address
from bacpypes3.local.object import (
    AnalogInputObject,
    AnalogOutputObject,
    BinaryOutputObject,
)

DEFAULTS = {
    "device_ip": "127.0.0.1",
    "device_id": 1001,
    "device_name": "VentSim",
    "initial": {
        "room_temp_c": 25.0,
        "co2_ppm": 900.0,
    },
    "thresholds": {
        "temp_c": 24.0,
        "co2_ppm": 850.0,
    },
    "tick_seconds": 1.0,
}

cfg_path = Path("config.yaml")
cfg = DEFAULTS.copy()

if cfg_path.exists():
    with cfg_path.open("r") as f:
        loaded = yaml.safe_load(f) or {}
    cfg.update({k: v for k, v in loaded.items() if k not in ("initial", "thresholds")})
    for section in ("initial", "thresholds"):
        sec = dict(DEFAULTS[section])
        sec.update(loaded.get(section, {}) or {})
        cfg[section] = sec

DEVICE_IP = str(cfg["device_ip"])
DEVICE_ID = int(cfg["device_id"])
DEVICE_NAME = str(cfg["device_name"])

room_temp = float(cfg["initial"]["room_temp_c"])
co2_ppm = float(cfg["initial"]["co2_ppm"])

TEMP_THRESHOLD = float(cfg["thresholds"]["temp_c"])
CO2_THRESHOLD = float(cfg["thresholds"]["co2_ppm"])

TICK_SECONDS = float(cfg["tick_seconds"])

device = DeviceObject(
    objectIdentifier=("device", DEVICE_ID),
    objectName=DEVICE_NAME,
    vendorIdentifier=15,
)
app = Application(device, Address(DEVICE_IP))

# Inputs
ai_temp = AnalogInputObject(
    objectIdentifier=("analogInput", 1),
    objectName="room_temperature_c",
    presentValue=room_temp,
)
ai_co2 = AnalogInputObject(
    objectIdentifier=("analogInput", 2),
    objectName="carbon_dioxide_ppm",
    presentValue=co2_ppm,
)

# Outputs (Controls)
bo_fan = BinaryOutputObject(
    objectIdentifier=("binaryOutput", 1),
    objectName="fan_command_on",
    presentValue=False,
)
ao_damper = AnalogOutputObject(
    objectIdentifier=("analogOutput", 1),
    objectName="damper_open_percent",
    presentValue=0.0,
)

for obj in (ai_temp, ai_co2, bo_fan, ao_damper):
    app.add_object(obj)

async def control_loop():
    """Control dynamics to maintain steady state."""
    global room_temp, co2_ppm

    while True:
        # Control logic
        fan_on = room_temp > TEMP_THRESHOLD
        damper_percent = 50.0 if co2_ppm > CO2_THRESHOLD else 0.0

        # TODO: We need to make these dynamics/Physics
        # more realistic with actual temp calculations.
        if fan_on and damper_percent > 0:
            room_temp -= 0.5 # Cool faster when venting
            co2_ppm   -= 5.0 # CO2 drops with outside air
        else:
            room_temp += random.uniform(-0.05, 0.05)
            co2_ppm   += random.uniform(-2.0, 2.0)

        room_temp = max(15.0, min(room_temp, 30.0))
        co2_ppm   = max(400.0, min(co2_ppm, 2000.0))

        # Publish to BACnet objects
        ai_temp.presentValue = room_temp
        ai_co2.presentValue = co2_ppm
        bo_fan.presentValue = fan_on
        ao_damper.presentValue = damper_percent

        print(
            f"T={room_temp:.1f}°C | CO2={co2_ppm:.0f}ppm | "
            f"Fan={'ON' if fan_on else 'OFF'} | Damper={damper_percent:.0f}%"
        )

        await asyncio.sleep(TICK_SECONDS)

async def main():
    print(f"[VentSim] Device '{DEVICE_NAME}' at {DEVICE_IP} (id {DEVICE_ID})")
    print(f"Thresholds: Temp > {TEMP_THRESHOLD}°C | CO2 > {CO2_THRESHOLD} ppm")
    for o in (ai_temp, ai_co2, bo_fan, ao_damper):
        print(f"  - {o.objectIdentifier}: {o.objectName}")

    asyncio.create_task(control_loop())
    await app.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[VentSim] Stopped.")
