#!/usr/bin/env python3
"""
BACnet HVAC Simulator (BACpypes3)

I/O
---
Outputs (Controls)
  - temperature_setpoint_c        (AO:0) - degrees Celsius
  - intake_fan_speed_percent      (AO:1) - 0..100 %
  - exhaust_fan_speed_percent     (AO:2) - 0..100 %
  - emergency_stop                (BO:0) - False/True

Inputs (Sensors)
  - current_temperature_c         (AI:0) - degrees Celsius
  - chiller_speed_percent         (AI:1) - 0..100 %

To start simulation:
    > python hvac_sim.py
"""

import asyncio
import random
import yaml
from pathlib import Path

# --- BACpypes3 ---
try:
    from bacpypes3.local.analog import AnalogInputObject, AnalogOutputObject
    from bacpypes3.local.binary import BinaryOutputObject
except ImportError:
    from bacpypes3.local.object import (  # type: ignore
        AnalogInputObject,
        AnalogOutputObject,
        BinaryOutputObject,
    )

from bacpypes3.argparse import SimpleArgumentParser
try:
    from bacpypes3.app import Application
except ImportError:
    from bacpypes3.ipv4.app import Application  # type: ignore


# --- Defaults (Overridden by YAML in config.yml) ---
DEFAULTS = {
    "device_ip": "127.0.0.1",
    "device_id": 2001,
    "device_name": "HVACSim",

    # Initial process state for HVAC (inputs) and outputs
    "initial": {
        "current_temp_c": 25.0,         # AI:0
        "chiller_speed_pct": 30.0,      # AI:1
        "setpoint_c": 23.0,             # AO:0
        "intake_fan_pct": 20.0,         # AO:1
        "exhaust_fan_pct": 20.0,        # AO:2
        "emergency_stop": False,        # BO:0
    },

    # Dynamics knobs (consistent but not accurate Physics)
    "dynamics": {
        "cooling_gain_per_fan": 0.04,   # °C per tick at 100% per fan
        "chiller_gain": 0.06,           # °C per tick at 100% chiller
        "ambient_warm_drift": 0.03,     # °C per tick toward warmer when idle
        "noise_temp": 0.03,             # °C random noise
        "noise_chiller": 1.0,           # % random noise
    },

    "tick_seconds": 1.0,

    # When true, the simulator will set AO:1/AO:2 to maintain the HVAC setpoint.
    # When false, outputs must be driven by the Caldera client from another VM;
    # the simulator only reads those outputs and updates sensors.
    "auto_control": True,
}

# --- Load config.yaml ---
cfg_path = Path("config.yaml")
cfg = DEFAULTS.copy()
if cfg_path.exists():
    with cfg_path.open("r") as f:
        loaded = yaml.safe_load(f) or {}
    for k, v in loaded.items():
        if k not in ("initial", "dynamics"):
            cfg[k] = v
    for section in ("initial", "dynamics"):
        s = dict(DEFAULTS[section])
        s.update((loaded.get(section) or {}))
        cfg[section] = s

# --- Unpack configuration ---
DEVICE_IP    = str(cfg["device_ip"])
DEVICE_ID    = int(cfg["device_id"])
DEVICE_NAME  = str(cfg["device_name"])
TICK_SECONDS = float(cfg["tick_seconds"])
AUTO_CONTROL = bool(cfg.get("auto_control", True))

# Process state (inputs)
current_temp_c     = float(cfg["initial"]["current_temp_c"])    # AI:0
chiller_speed_pct  = float(cfg["initial"]["chiller_speed_pct"]) # AI:1

# Outputs (controls)
setpoint_c         = float(cfg["initial"]["setpoint_c"])        # AO:0
intake_fan_pct     = float(cfg["initial"]["intake_fan_pct"])    # AO:1
exhaust_fan_pct    = float(cfg["initial"]["exhaust_fan_pct"])   # AO:2
emergency_stop     = bool(cfg["initial"]["emergency_stop"])     # BO:0

# Dynamics gains
cooling_gain_per_fan = float(cfg["dynamics"]["cooling_gain_per_fan"])
chiller_gain         = float(cfg["dynamics"]["chiller_gain"])
ambient_warm_drift   = float(cfg["dynamics"]["ambient_warm_drift"])
noise_temp           = float(cfg["dynamics"]["noise_temp"])
noise_chiller        = float(cfg["dynamics"]["noise_chiller"])

# --- Outputs (Controls) ---
ao_setpoint = AnalogOutputObject(
    objectIdentifier=("analog-output", 0),
    objectName="temperature_setpoint_c",
    presentValue=setpoint_c,
)
ao_intake = AnalogOutputObject(
    objectIdentifier=("analog-output", 1),
    objectName="intake_fan_speed_percent",
    presentValue=intake_fan_pct,
)
ao_exhaust = AnalogOutputObject(
    objectIdentifier=("analog-output", 2),
    objectName="exhaust_fan_speed_percent",
    presentValue=exhaust_fan_pct,
)
bo_e_stop = BinaryOutputObject(
    objectIdentifier=("binary-output", 0),
    objectName="emergency_stop",
    presentValue=emergency_stop,
)

# --- Inputs (Sensors) ---
ai_temp = AnalogInputObject(
    objectIdentifier=("analog-input", 0),
    objectName="current_temperature_c",
    presentValue=current_temp_c,
)
ai_chiller = AnalogInputObject(
    objectIdentifier=("analog-input", 1),
    objectName="chiller_speed_percent",
    presentValue=chiller_speed_pct,
)


async def control_loop():
    """Control dynamics to maintain a steady room temperature around AO:0."""
    global current_temp_c, chiller_speed_pct

    while True:
        # --- Read outputs as the ground truth for controls ---
        setpoint = float(ao_setpoint.presentValue)
        e_stop   = bool(bo_e_stop.presentValue)

        if AUTO_CONTROL:
            temp_err = current_temp_c - setpoint
            if e_stop:
                ao_intake.presentValue = 0.0
                ao_exhaust.presentValue = 0.0
            else:
                # Map error to fan speeds (clamped 0..100)
                base = max(0.0, min(100.0, 50.0 + 2.5 * temp_err))
                ao_intake.presentValue  = base
                ao_exhaust.presentValue = base
        # else: Caldera is driving AO:1/AO:2 directly; do not override.

        intake = float(ao_intake.presentValue or 0.0)
        exhaust = float(ao_exhaust.presentValue or 0.0)

        # --- Process response ---
        if e_stop:
            effective_cooling = 0.0
        else:
            airflow_pct = max(0.0, min(100.0, (intake + exhaust) / 2.0))
            effective_cooling = (airflow_pct * cooling_gain_per_fan) + (chiller_speed_pct * chiller_gain / 100.0)

        # Temperature moves toward setpoint when cooling is applied; otherwise warms slightly.
        if effective_cooling > 0.0 and current_temp_c > setpoint:
            current_temp_c -= effective_cooling
        else:
            current_temp_c += ambient_warm_drift

        # Chiller speed tends to follow average fan demand (with noise), bounded 0..100.
        chiller_speed_pct = max(0.0, min(100.0, ((intake + exhaust) / 2.0) + random.uniform(-noise_chiller, noise_chiller)))

        current_temp_c += random.uniform(-noise_temp, noise_temp)
        current_temp_c = max(10.0, min(current_temp_c, 40.0))

        # --- Publish inputs (sensors) ---
        ai_temp.presentValue = current_temp_c
        ai_chiller.presentValue = chiller_speed_pct

        print(
            f"Tset={setpoint:.1f}°C | T={current_temp_c:.1f}°C | "
            f"Intake={intake:.0f}% | Exhaust={exhaust:.0f}% | "
            f"Chiller={chiller_speed_pct:.0f}% | E-Stop={'ON' if e_stop else 'OFF'}"
        )

        await asyncio.sleep(TICK_SECONDS)


async def main():
    print(f"[HVACSim] Device '{DEVICE_NAME}' at {DEVICE_IP} (id {DEVICE_ID})")
    print(f"AUTO_CONTROL = {AUTO_CONTROL}")

    for o in (ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller):
        print(f"  - {o.objectIdentifier}: {o.objectName}")

    args = SimpleArgumentParser().parse_args()
    args.localAddress = DEVICE_IP
    app = Application.from_args(args)

    app.device_object.objectIdentifier = ("device", DEVICE_ID)
    app.device_object.objectName = DEVICE_NAME

    # Register objects
    for obj in (ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller):
        app.add_object(obj)

    asyncio.create_task(control_loop())
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[HVACSim] Stopped.")
