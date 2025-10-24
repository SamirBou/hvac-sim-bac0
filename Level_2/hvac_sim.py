#!/usr/bin/env python3
"""
BACnet HVAC Simulator

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
import contextlib
import random
import time
from collections import deque
from pathlib import Path
import yaml

# --- BACpypes3 ---
from bacpypes3.local.analog import AnalogInputObject, AnalogOutputObject
from bacpypes3.local.binary import BinaryOutputObject
from bacpypes3.local.device import DeviceObject
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.app import Application

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
    # When false, outputs must be driven by the Caldera client;
    # the simulator only reads those outputs and updates sensors.
    "auto_control": True,

    "enable_ui": True,
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
AUTO_CONTROL_DEFAULT = bool(cfg.get("auto_control", True))
ENABLE_UI    = bool(cfg.get("enable_ui", True))

# Process (Inputs)
current_temp_c     = float(cfg["initial"]["current_temp_c"])    # AI:0
chiller_speed_pct  = float(cfg["initial"]["chiller_speed_pct"]) # AI:1

# Outputs (Controls)
setpoint_c         = float(cfg["initial"]["setpoint_c"])        # AO:0
intake_fan_pct     = float(cfg["initial"]["intake_fan_pct"])    # AO:1
exhaust_fan_pct    = float(cfg["initial"]["exhaust_fan_pct"])   # AO:2
emergency_stop     = bool(cfg["initial"]["emergency_stop"])     # BO:0

cooling_gain_per_fan = float(cfg["dynamics"]["cooling_gain_per_fan"])
chiller_gain         = float(cfg["dynamics"]["chiller_gain"])
ambient_warm_drift   = float(cfg["dynamics"]["ambient_warm_drift"])
noise_temp           = float(cfg["dynamics"]["noise_temp"])
noise_chiller        = float(cfg["dynamics"]["noise_chiller"])

_ui = None
if ENABLE_UI:
    import tkinter as tk
    from tkinter import ttk

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    class MiniUI:
        """Controls and plots UI."""
        HISTORY_SECONDS = 100.0

        def __init__(self, base_tick: float, init_setpoint: float, auto_on: bool, e_stop: bool):
            self.base_tick = base_tick
            self.start = time.monotonic()
            self.window = self.HISTORY_SECONDS
            maxlen = int(self.window / max(0.05, base_tick)) + 5

            # Deques for time-series
            self.t = deque(maxlen=maxlen)
            self.temp = deque(maxlen=maxlen)
            self.setp = deque(maxlen=maxlen)
            self.intake = deque(maxlen=maxlen)
            self.exhaust = deque(maxlen=maxlen)
            self.chill = deque(maxlen=maxlen)

            # ----- Tk Layout -----
            self.root = tk.Tk()
            self.root.title("HVACSim")
            self.root.geometry("900x420")
            self.root.resizable(False, False)

            top = ttk.Frame(self.root)
            top.pack(side="top", fill="x", padx=10, pady=8)

            # Controls
            self.var_setpoint = tk.DoubleVar(value=init_setpoint)
            ttk.Label(top, text="Setpoint (°C)").pack(side="left")
            s = ttk.Scale(top, from_=16.0, to=30.0, variable=self.var_setpoint, length=180, orient="horizontal")
            s.pack(side="left", padx=(6, 20))

            self.var_auto = tk.BooleanVar(value=auto_on)
            ttk.Checkbutton(top, text="Auto Control", variable=self.var_auto).pack(side="left", padx=(0, 10))

            self.var_estop = tk.BooleanVar(value=e_stop)
            ttk.Checkbutton(top, text="E-Stop", variable=self.var_estop).pack(side="left", padx=(0, 20))

            ttk.Label(top, text="Speed").pack(side="left")
            self.var_speed = tk.DoubleVar(value=1.0)
            ttk.Scale(top, from_=0.25, to=4.0, variable=self.var_speed, length=160, orient="horizontal").pack(side="left", padx=6)

            # Readouts
            self.lbl_temp = ttk.Label(top, text="T = --.- °C")
            self.lbl_temp.pack(side="right")
            self.lbl_chill = ttk.Label(top, text="Chiller = -- %  ")
            self.lbl_chill.pack(side="right")

            fig = Figure(figsize=(9, 2.6), dpi=100)
            gs = fig.add_gridspec(
                2, 3,
                width_ratios=[2.4, 0.9, 0.9],
                height_ratios=[1.0, 0.6],
            )

            self.ax_temp = fig.add_subplot(gs[:, 0])
            self.ax_temp.set_title("Room Temperature")
            self.ax_temp.set_xlabel("Time (s)")
            self.ax_temp.set_ylabel("°C")
            self.ax_temp.set_ylim(10, 40)
            self.ax_temp.grid(True, alpha=0.25)
            (self.line_temp,) = self.ax_temp.plot([], [], lw=2, label="T")
            (self.line_setp,) = self.ax_temp.plot([], [], lw=2, label="Setpoint")
            self.ax_temp.legend(loc="upper right")

            self.ax_intake = fig.add_subplot(gs[0, 1])
            self.ax_exhaust = fig.add_subplot(gs[0, 2])
            self.ax_chill = fig.add_subplot(gs[1, 1:])

            for ax, title in [
                (self.ax_intake, "Intake %"),
                (self.ax_exhaust, "Exhaust %"),
                (self.ax_chill, "Chiller %")]:
                ax.set_ylim(0, 100)
                ax.set_xlim(-self.window, 0)
                ax.set_title(title)
                ax.grid(True, alpha=0.2)

            self.ax_intake.tick_params(axis="x", labelbottom=False)
            self.ax_exhaust.tick_params(axis="x", labelbottom=False)
            self.ax_chill.tick_params(axis="x", labelbottom=True)

            (self.line_intake,) = self.ax_intake.plot([], [], lw=2)
            (self.line_exhaust,) = self.ax_exhaust.plot([], [], lw=2)
            (self.line_chill,) = self.ax_chill.plot([], [], lw=2)

            fig.subplots_adjust(top=0.92, bottom=0.12, left=0.07, right=0.98, hspace=0.35)

            self.canvas = FigureCanvasTkAgg(fig, master=self.root)
            self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # UI -> sim
        def get_setpoint(self) -> float:
            return float(self.var_setpoint.get())
        def get_auto(self) -> bool:
            return bool(self.var_auto.get())
        def get_estop(self) -> bool:
            return bool(self.var_estop.get())
        def get_speed(self) -> float:
            return max(0.25, min(4.0, float(self.var_speed.get())))

        # sim -> UI
        def push(self, temp_c: float, setp_c: float, chill: float, intake: float, exhaust: float) -> None:
            now = time.monotonic() - self.start
            self.t.append(now)
            self.temp.append(temp_c)
            self.setp.append(setp_c)
            self.chill.append(chill)
            self.intake.append(intake)
            self.exhaust.append(exhaust)
            self.lbl_temp.configure(text=f"T = {temp_c:.1f} °C")
            self.lbl_chill.configure(text=f"Chiller = {chill:.0f} %")

        def redraw(self) -> None:
            if not self.t:
                return
            now = self.t[-1]
            x = [ti - now for ti in self.t]
            xmin, xmax = -self.window, 0

            self.line_temp.set_data(x, list(self.temp))
            self.line_setp.set_data(x, list(self.setp))
            self.ax_temp.set_xlim(xmin, xmax)

            for ax, line, series in [
                (self.ax_intake, self.line_intake, self.intake),
                (self.ax_exhaust, self.line_exhaust, self.exhaust),
                (self.ax_chill,   self.line_chill,   self.chill),
            ]:
                line.set_data(x, list(series))
                ax.set_xlim(xmin, xmax)

            self.canvas.draw_idle()

        async def pump(self):
            while True:
                self.root.update_idletasks()
                self.root.update()
                self.redraw()
                await asyncio.sleep(0.05)

device = DeviceObject(
    objectIdentifier=("device", DEVICE_ID),
    objectName=DEVICE_NAME,
    vendorIdentifier=15,
)

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

OBJECT_LIST = [device, ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller]

async def control_loop():
    """Control dynamics to maintain a steady room temperature around AO:0."""
    global current_temp_c, chiller_speed_pct

    i = 0  # Thin out console logs a bit
    while True:
        # --- Read controls (UI if present, else from BACnet) ---
        setpoint = float(_ui.get_setpoint()) if _ui else float(ao_setpoint.presentValue)
        use_auto = bool(_ui.get_auto()) if _ui else AUTO_CONTROL_DEFAULT
        e_stop   = bool(_ui.get_estop()) if _ui else bool(bo_e_stop.presentValue)

        # Keep BACnet points in sync with the UI (if UI exists)
        if _ui:
            ao_setpoint.presentValue = setpoint
            bo_e_stop.presentValue   = e_stop

        # --- Automatic control logic (if not driven externally) ---
        if use_auto:
            temp_err = current_temp_c - setpoint
            if e_stop:
                ao_intake.presentValue = 0.0
            else:
                base = max(0.0, min(100.0, 50.0 + 2.5 * temp_err))
                ao_intake.presentValue  = base
            ao_exhaust.presentValue = float(ao_intake.presentValue)

        intake = float(ao_intake.presentValue or 0.0)
        exhaust = float(ao_exhaust.presentValue or 0.0)

        if e_stop:
            effective_cooling = 0.0
        else:
            airflow_pct = max(0.0, min(100.0, (intake + exhaust) / 2.0))
            effective_cooling = (
                airflow_pct * cooling_gain_per_fan
                + (chiller_speed_pct * chiller_gain / 100.0)
            )

        if effective_cooling > 0.0 and current_temp_c > setpoint:
            current_temp_c -= effective_cooling
        else:
            current_temp_c += ambient_warm_drift

        chiller_speed_pct = max(0.0, min(100.0, ((intake + exhaust) / 2.0) + random.uniform(-noise_chiller, noise_chiller)))
        current_temp_c += random.uniform(-noise_temp, noise_temp)
        current_temp_c = max(10.0, min(current_temp_c, 40.0))

        ai_temp.presentValue = current_temp_c
        ai_chiller.presentValue = chiller_speed_pct

        if _ui:
            _ui.push(temp_c=current_temp_c, setp_c=setpoint,
                     chill=chiller_speed_pct, intake=intake, exhaust=exhaust)

        if i % 5 == 0:
            print(
                f"Tset={setpoint:.1f}°C | T={current_temp_c:.1f}°C | "
                f"Intake={intake:.0f}% | Exhaust={exhaust:.0f}% | "
                f"Chiller={chiller_speed_pct:.0f}% | E-Stop={'ON' if e_stop else 'OFF'}"
            )
        i += 1

        speed = _ui.get_speed() if _ui else 1.0
        await asyncio.sleep(max(0.05, TICK_SECONDS / speed))

async def main():
    print(f"[HVACSim] Device '{DEVICE_NAME}' at {DEVICE_IP} (id {DEVICE_ID})")
    print(f"AUTO_CONTROL (default) = {AUTO_CONTROL_DEFAULT}")

    args = SimpleArgumentParser().parse_args()
    args.localAddress = DEVICE_IP

    app = Application.from_object_list(OBJECT_LIST, args)

    tasks = []
    shutdown_evt = asyncio.Event()

    if ENABLE_UI:
        global _ui
        _ui = MiniUI(
            base_tick=TICK_SECONDS,
            init_setpoint=setpoint_c,
            auto_on=AUTO_CONTROL_DEFAULT,
            e_stop=emergency_stop,
        )

        def _on_close():
            shutdown_evt.set()
            try:
                _ui.root.destroy()
            except Exception:
                pass

        _ui.root.protocol("WM_DELETE_WINDOW", _on_close)
        tasks.append(asyncio.create_task(_ui.pump()))

    tasks.append(asyncio.create_task(control_loop()))

    try:
        if ENABLE_UI:
            await shutdown_evt.wait()
        else:
            await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        with contextlib.suppress(Exception):
            await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[HVACSim] Stopped.")
