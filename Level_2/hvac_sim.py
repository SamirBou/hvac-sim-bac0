#!/usr/bin/env python3
"""
Simulates an HVAC system as a BACnet/IP device for Caldera for OT.

Objects:
  - AO:0 temperature_setpoint_c      (writable)
  - AO:1 intake_fan_speed_percent    (writable)
  - AO:2 exhaust_fan_speed_percent   (writable)
  - BO:0 emergency_stop              (writable)
  - AI:0 current_temperature_c       (read-only)
  - AI:1 chiller_speed_percent       (read-only)

To run:
    python3 hvac_sim.py --ini ./BACpypes.ini --debug bacpypes.udp
"""

import random
import time
import threading
import signal
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

from bacpypes.consolelogging import ConfigArgumentParser
from bacpypes.core import run, stop
from bacpypes.app import BIPSimpleApplication
from bacpypes.object import (
    DeviceObject,
    AnalogInputObject,
    AnalogOutputObject,
    BinaryOutputObject,
)

# --- Steady process state (sensors) ---
current_temp_c = 22.0
chiller_speed_pct = 30.0

# --- Control outputs (AOs/BOs) ---
temperature_setpoint_c = 23.0
intake_fan_speed = 30.0
exhaust_fan_speed = 30.0
emergency_stop = False

# --- Dynamics (rough physics) ---
COOLING_GAIN_PER_FAN = 0.03
CHILLER_GAIN = 0.05
AMBIENT_WARM_DRIFT = 0.02
NOISE_TEMP = 0.05
NOISE_CHILLER = 0.8
TICK_SECONDS = 1.0


class HVACApplication(BIPSimpleApplication):
    """BACnet application for the simulated HVAC device."""
    pass


def build_objects(device_name: str, device_id: int):
    """Create all BACnet objects for the simulator."""

    device = DeviceObject(
        objectIdentifier=("device", device_id),
        objectName=device_name,
        vendorIdentifier=15,
    )

    # Writable controls
    ao_setpoint = AnalogOutputObject(
        objectIdentifier=("analogOutput", 0),
        objectName="temperature_setpoint_c",
        presentValue=temperature_setpoint_c,
        description="Desired room temperature (°C)",
        relinquishDefault=23.0,
    )
    ao_intake = AnalogOutputObject(
        objectIdentifier=("analogOutput", 1),
        objectName="intake_fan_speed_percent",
        presentValue=intake_fan_speed,
        description="Intake fan speed (%)",
        relinquishDefault=30.0,
    )
    ao_exhaust = AnalogOutputObject(
        objectIdentifier=("analogOutput", 2),
        objectName="exhaust_fan_speed_percent",
        presentValue=exhaust_fan_speed,
        description="Exhaust fan speed (%)",
        relinquishDefault=30.0,
    )
    bo_e_stop = BinaryOutputObject(
        objectIdentifier=("binaryOutput", 0),
        objectName="emergency_stop",
        presentValue=emergency_stop,
        description="Emergency stop (True/False)",
        relinquishDefault=False,
    )

    # Read-only sensors
    ai_temp = AnalogInputObject(
        objectIdentifier=("analogInput", 0),
        objectName="current_temperature_c",
        presentValue=current_temp_c,
        description="Measured room temperature (°C)",
    )
    ai_chiller = AnalogInputObject(
        objectIdentifier=("analogInput", 1),
        objectName="chiller_speed_percent",
        presentValue=chiller_speed_pct,
        description="Chiller load (%)",
    )

    return device, [device, ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller]


def hvac_loop(ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller, data_buf, running_evt):
    """Main process loop running in background thread."""
    global current_temp_c, chiller_speed_pct

    print("[HVACSim] Control loop started.")
    while running_evt.is_set():
        try:
            setpoint = float(ao_setpoint.presentValue)
            intake = float(ao_intake.presentValue)
            exhaust = float(ao_exhaust.presentValue)
            e_stop = bool(bo_e_stop.presentValue)

            # Effective cooling based on airflow and chiller (if not e-stop)
            if e_stop:
                effective_cooling = 0.0
            else:
                airflow = max(0.0, min(100.0, (intake + exhaust) / 2.0))
                effective_cooling = (
                    airflow * COOLING_GAIN_PER_FAN
                    + chiller_speed_pct * CHILLER_GAIN / 100.0
                )

            if effective_cooling > 0.0 and current_temp_c > setpoint:
                current_temp_c -= effective_cooling
            else:
                current_temp_c += AMBIENT_WARM_DRIFT

            # Noise and bounds
            current_temp_c += random.uniform(-NOISE_TEMP, NOISE_TEMP)
            current_temp_c = max(10.0, min(current_temp_c, 40.0))

            # Chiller (with noise)
            chiller_speed_pct = max(
                0.0,
                min(100.0, ((intake + exhaust) / 2.0) + random.uniform(-NOISE_CHILLER, NOISE_CHILLER)),
            )

            # Update sensors
            ai_temp.presentValue = current_temp_c
            ai_chiller.presentValue = chiller_speed_pct

            now = time.time()
            data_buf["time"].append(now)
            data_buf["temp"].append(current_temp_c)
            data_buf["setp"].append(setpoint)
            data_buf["chill"].append(chiller_speed_pct)
            data_buf["fan"].append((intake + exhaust) / 2.0)

            if int(now) % 2 == 0:
                print(
                    f"Tset={setpoint:.1f}°C | T={current_temp_c:.1f}°C | "
                    f"Intake={intake:.0f}% | Exhaust={exhaust:.0f}% | "
                    f"Chiller={chiller_speed_pct:.0f}% | E-Stop={'ON' if e_stop else 'OFF'}"
                )

            time.sleep(TICK_SECONDS)

        except Exception as e:
            print(f"[HVACSim] Error in loop: {e}")
            time.sleep(2.0)


def start_plot(data_buf, running_evt):
    """Create plot for temperature and system response."""
    plt.style.use("seaborn-v0_8-darkgrid")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6))
    fig.canvas.manager.set_window_title("HVACSim View")

    line_temp, = ax1.plot([], [], label="Temperature (°C)", lw=2)
    line_setp, = ax1.plot([], [], label="Setpoint (°C)", lw=2, linestyle="--")
    ax1.set_ylabel("Temperature (°C)")
    ax1.legend(loc="upper right")

    line_chill, = ax2.plot([], [], label="Chiller (%)", lw=2)
    line_fan,   = ax2.plot([], [], label="Fan (%)", lw=2)
    ax2.set_xlabel("Time (s)")
    ax2.set_ylabel("Output Levels (%)")
    ax2.legend(loc="upper right")

    # Update callback
    def animate(_):
        if not data_buf["time"]:
            return line_temp, line_setp, line_chill, line_fan
        t0 = data_buf["time"][0]
        x = [t - t0 for t in data_buf["time"]]
        line_temp.set_data(x, data_buf["temp"])
        line_setp.set_data(x, data_buf["setp"])
        line_chill.set_data(x, data_buf["chill"])
        line_fan.set_data(x, data_buf["fan"])

        # Sliding window of 120s
        xmax = x[-1] if x else 0.0
        xmin = max(0.0, xmax - 120.0)
        ax1.set_xlim(xmin, xmax + 1.0)
        ax2.set_xlim(xmin, xmax + 1.0)

        try:
            tmin = min(data_buf["temp"])
            tmax = max(data_buf["temp"])
        except ValueError:
            tmin, tmax = 20.0, 25.0
        pad = 1.0
        ax1.set_ylim(tmin - pad, tmax + pad)

        # Fixed % range
        ax2.set_ylim(0, 100)
        return line_temp, line_setp, line_chill, line_fan

    anim = FuncAnimation(fig, animate, interval=1000, cache_frame_data=False)

    def _on_close(_evt):
        running_evt.clear()
        try:
            stop()
        except Exception:
            pass

    fig.canvas.mpl_connect("close_event", _on_close)

    plt.tight_layout()
    plt.show()

    running_evt.clear()
    try:
        stop()
    except Exception:
        pass


def main():
    parser = ConfigArgumentParser(description="BACnet HVAC Simulation Device")
    args = parser.parse_args()

    device_name = args.ini.objectname or "HVACSim"
    device_id = int(args.ini.objectidentifier)
    device, objects = build_objects(device_name, device_id)

    app = HVACApplication(device, args.ini.address)

    for obj in objects[1:]:
        app.add_object(obj)

    print(f"[HVACSim] Device '{device_name}' ready on {args.ini.address} (ID {device_id})")

    data_buf = {k: deque(maxlen=600) for k in ["time", "temp", "setp", "chill", "fan"]}

    running_evt = threading.Event()
    running_evt.set()

    # Start BACpypes core loop in a background thread
    core_thread = threading.Thread(target=run, name="bacpypes-core", daemon=True)
    core_thread.start()

    # Start the HVAC control loop (background thread)
    ctl_thread = threading.Thread(
        target=hvac_loop,
        args=(objects[1], objects[2], objects[3], objects[4], objects[5], objects[6], data_buf, running_evt),
        name="hvac-loop",
        daemon=True,
    )
    ctl_thread.start()

    def _sigint(_sig, _frm):
        running_evt.clear()
        try:
            stop()
        except Exception:
            pass
        plt.close("all")
    signal.signal(signal.SIGINT, _sigint)

    start_plot(data_buf, running_evt)

    ctl_thread.join(timeout=1.0)
    core_thread.join(timeout=1.0)
    print("[HVACSim] Shut down.")

if __name__ == "__main__":
    main()
