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
from matplotlib.widgets import Slider, Button

from bacpypes.consolelogging import ConfigArgumentParser
from bacpypes.core import run, stop
from bacpypes.app import BIPSimpleApplication
from bacpypes.object import (
    DeviceObject,
    AnalogInputObject,
    AnalogOutputObject,
    BinaryOutputObject,
)

current_temp_c = 22.0
chiller_speed_pct = 30.0
chiller_integral = 0.0

temperature_setpoint_c = 23.0
intake_fan_speed = 30.0
exhaust_fan_speed = 30.0
emergency_stop = False

TICK_SECONDS = 1.0

AMBIENT_TEMP_C = 24.0
INTERNAL_LOAD_DEGC = 5.0
ROOM_TIME_CONSTANT = 120.0

AIRFLOW_MAX_COOL = 1.5 / 60.0
CHILLER_MAX_COOL = 10.0 / 60.0

CHILLER_KP = 40.0
CHILLER_KI = 0.3
CHILLER_LAG = 0.30
CHILLER_INT_LIMIT = 200.0

NOISE_TEMP = 0.05
NOISE_CHILLER = 0.8


class HVACApplication(BIPSimpleApplication):
    pass


def build_objects(device_name: str, device_id: int):
    device = DeviceObject(
        objectIdentifier=("device", device_id),
        objectName=device_name,
        vendorIdentifier=15,
    )

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

    return device, [
        device,
        ao_setpoint,
        ao_intake,
        ao_exhaust,
        bo_e_stop,
        ai_temp,
        ai_chiller,
    ]


def hvac_loop(
    ao_setpoint,
    ao_intake,
    ao_exhaust,
    bo_e_stop,
    ai_temp,
    ai_chiller,
    data_buf,
    running_evt,
):
    global current_temp_c, chiller_speed_pct, chiller_integral

    print("[HVACSim] Control loop started.")
    while running_evt.is_set():
        try:
            setpoint = float(ao_setpoint.presentValue)
            intake = float(ao_intake.presentValue)
            exhaust = float(ao_exhaust.presentValue)
            e_stop = bool(bo_e_stop.presentValue)

            airflow = max(0.0, min(100.0, (intake + exhaust) / 2.0))

            if e_stop:
                chiller_target = 0.0
                airflow = 0.0
                chiller_integral = 0.0
            else:
                error_c = current_temp_c - setpoint
                chiller_integral += error_c * TICK_SECONDS
                chiller_integral = max(
                    -CHILLER_INT_LIMIT, min(CHILLER_INT_LIMIT, chiller_integral)
                )
                raw_target = CHILLER_KP * error_c + CHILLER_KI * chiller_integral
                chiller_target = max(0.0, min(100.0, raw_target))

            chiller_speed_pct += (chiller_target - chiller_speed_pct) * CHILLER_LAG
            chiller_speed_pct += random.uniform(-NOISE_CHILLER, NOISE_CHILLER)
            chiller_speed_pct = max(0.0, min(100.0, chiller_speed_pct))

            load_temp = AMBIENT_TEMP_C + INTERNAL_LOAD_DEGC

            cooling_power = (airflow / 100.0) * AIRFLOW_MAX_COOL + (
                chiller_speed_pct / 100.0
            ) * CHILLER_MAX_COOL

            dTdt = ((load_temp - current_temp_c) / ROOM_TIME_CONSTANT) - cooling_power
            current_temp_c += dTdt * TICK_SECONDS

            current_temp_c += random.uniform(-NOISE_TEMP, NOISE_TEMP)
            current_temp_c = max(10.0, min(40.0, current_temp_c))

            ai_temp.presentValue = current_temp_c
            ai_chiller.presentValue = chiller_speed_pct

            now = time.time()
            data_buf["time"].append(now)
            data_buf["temp"].append(current_temp_c)
            data_buf["setp"].append(setpoint)
            data_buf["chill"].append(chiller_speed_pct)
            data_buf["intake"].append(airflow)
            data_buf["exhaust"].append(exhaust)

            if int(now) % 10 == 0:
                print(
                    f"Tset={setpoint:.1f}°C | T={current_temp_c:.1f}°C | "
                    f"Airflow={airflow:.0f}% | Chiller={chiller_speed_pct:.0f}% | "
                    f"E-Stop={'ON' if e_stop else 'OFF'}"
                )

            time.sleep(TICK_SECONDS)

        except Exception as e:
            print(f"[HVACSim] Error in loop: {e}")
            time.sleep(2.0)


def c_to_f(value_c: float) -> float:
    return value_c * 9.0 / 5.0 + 32.0


def start_plot(
    data_buf,
    running_evt,
    ao_setpoint,
    ao_intake,
    ao_exhaust,
    bo_e_stop,
):
    TEMP_COLOR = "#007ACC"
    SETPOINT_COLOR = "#FF8C00"
    CHILLER_COLOR = "#004B6B"
    INTAKE_COLOR = "#228B22"
    EXHAUST_COLOR = "#9B1C31"

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.titlesize": 14,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
            "axes.facecolor": "#f5f5f5",
            "figure.facecolor": "#f5f5f5",
            "grid.color": "#d0d0d0",
            "axes.edgecolor": "#666666",
        }
    )

    fig = plt.figure(figsize=(11, 6))
    fig.canvas.manager.set_window_title("Server Room HMI")

    gs = fig.add_gridspec(
        4,
        4,
        height_ratios=[3.5, 1.0, 1.0, 1.5],
        width_ratios=[1.0, 1.0, 1.0, 1.0],
        wspace=0.6,
        hspace=0.7,
    )

    ax_temp = fig.add_subplot(gs[0, 0:3])
    ax_chill = fig.add_subplot(gs[0, 3])
    ax_intake = fig.add_subplot(gs[1, 3])
    ax_exhaust = fig.add_subplot(gs[2, 3])
    ax_controls = fig.add_subplot(gs[1:4, 0:3])
    ax_controls.axis("off")

    (line_temp,) = ax_temp.plot(
        [], [], lw=2, label="Current Temp (°F)", color=TEMP_COLOR
    )
    (line_setp,) = ax_temp.plot(
        [], [], lw=2, linestyle="--", label="Setpoint (°F)", color=SETPOINT_COLOR
    )

    ax_temp.set_title("Server Room Temperature")
    ax_temp.set_xlabel("Time (s)")
    ax_temp.set_ylabel("Temperature (°F)")
    ax_temp.legend(loc="upper right", frameon=True)

    for ax in (ax_temp, ax_chill, ax_intake, ax_exhaust):
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    (line_chill,) = ax_chill.plot([], [], lw=2, color=CHILLER_COLOR)
    ax_chill.set_ylabel("Chiller (%)")
    ax_chill.set_ylim(0, 100)
    ax_chill.set_title("Chiller", pad=6)

    (line_intake,) = ax_intake.plot([], [], lw=2, color=INTAKE_COLOR)
    ax_intake.set_ylabel("Intake (%)")
    ax_intake.set_ylim(0, 100)
    ax_intake.set_xlabel("Time (s)")

    (line_exhaust,) = ax_exhaust.plot([], [], lw=2, color=EXHAUST_COLOR)
    ax_exhaust.set_ylabel("Exhaust (%)")
    ax_exhaust.set_ylim(0, 100)
    ax_exhaust.set_xlabel("Time (s)")

    ctrl_pos = ax_controls.get_position()
    left = ctrl_pos.x0
    width = ctrl_pos.width
    bottom = ctrl_pos.y0
    height = ctrl_pos.height
    slider_h = height / 6.0

    fig.text(
        left + 0.01 * width,
        bottom + height - slider_h * 0.3,
        "Controls",
        fontsize=12,
        fontweight="bold",
    )

    ax_s_setp = fig.add_axes(
        [left + 0.02 * width, bottom + 4 * slider_h, width * 0.7, slider_h * 0.6]
    )
    ax_s_intake = fig.add_axes(
        [left + 0.02 * width, bottom + 3 * slider_h, width * 0.7, slider_h * 0.6]
    )
    ax_s_exhaust = fig.add_axes(
        [left + 0.02 * width, bottom + 2 * slider_h, width * 0.7, slider_h * 0.6]
    )

    btn_width = width * 0.2
    btn_height = slider_h * 2.1
    btn_left = left + 0.81 * width
    btn_bottom = bottom + 2.3 * slider_h
    ax_btn_estop = fig.add_axes([btn_left, btn_bottom, btn_width, btn_height])

    initial_setp_f = c_to_f(float(ao_setpoint.presentValue))

    s_setp = Slider(
        ax=ax_s_setp,
        label="Setpoint (°F)",
        valmin=60.0,
        valmax=85.0,
        valinit=initial_setp_f,
        facecolor=TEMP_COLOR,
    )
    s_intake = Slider(
        ax=ax_s_intake,
        label="Intake Fan (%)",
        valmin=0.0,
        valmax=100.0,
        valinit=float(ao_intake.presentValue),
        facecolor=INTAKE_COLOR,
    )
    s_exhaust = Slider(
        ax=ax_s_exhaust,
        label="Exhaust Fan (%)",
        valmin=0.0,
        valmax=100.0,
        valinit=float(ao_exhaust.presentValue),
        facecolor=EXHAUST_COLOR,
    )

    for s in (s_setp, s_intake, s_exhaust):
        if s.valtext is not None:
            s.valtext.set_fontweight("bold")

    btn_estop = Button(ax_btn_estop, "E-STOP: OFF")
    btn_estop.label.set_fontweight("bold")

    def on_setp_change(val_f):
        ao_setpoint.presentValue = (val_f - 32.0) * 5.0 / 9.0

    def on_intake_change(val_pct):
        ao_intake.presentValue = float(val_pct)

    def on_exhaust_change(val_pct):
        ao_exhaust.presentValue = float(val_pct)

    s_setp.on_changed(on_setp_change)
    s_intake.on_changed(on_intake_change)
    s_exhaust.on_changed(on_exhaust_change)

    def update_estop_button():
        if bool(bo_e_stop.presentValue):
            btn_estop.label.set_text("E-STOP: ON")
            btn_estop.label.set_color("white")
            btn_estop.color = "#b22222"
            btn_estop.hovercolor = "#b22222"
        else:
            btn_estop.label.set_text("E-STOP: OFF")
            btn_estop.label.set_color("black")
            btn_estop.color = "#d3d3d3"
            btn_estop.hovercolor = "#e0e0e0"

        btn_estop.ax.set_facecolor(btn_estop.color)
        fig.canvas.draw_idle()

    def on_estop_clicked(_event):
        bo_e_stop.presentValue = not bool(bo_e_stop.presentValue)
        update_estop_button()

    btn_estop.on_clicked(on_estop_clicked)
    update_estop_button()

    def animate(_):
        if not data_buf["time"]:
            return line_temp, line_setp, line_chill, line_intake, line_exhaust

        t0 = data_buf["time"][0]
        x = [t - t0 for t in data_buf["time"]]

        temp_f = [c_to_f(c) for c in data_buf["temp"]]
        setp_f = [c_to_f(c) for c in data_buf["setp"]]

        line_temp.set_data(x, temp_f)
        line_setp.set_data(x, setp_f)
        line_chill.set_data(x, data_buf["chill"])
        line_intake.set_data(x, data_buf["intake"])
        line_exhaust.set_data(x, data_buf["exhaust"])

        xmax = x[-1]
        xmin = max(0.0, xmax - 120.0)
        for ax in (ax_temp, ax_chill, ax_intake, ax_exhaust):
            ax.set_xlim(xmin, xmax + 1.0)

        tmin = min(temp_f)
        tmax = max(temp_f)
        pad = 2.0
        ax_temp.set_ylim(tmin - pad, tmax + pad)

        return line_temp, line_setp, line_chill, line_intake, line_exhaust

    anim = FuncAnimation(fig, animate, interval=1000, cache_frame_data=False)
    fig._anim = anim

    def _on_close(_evt):
        running_evt.clear()
        try:
            stop()
        except Exception:
            pass

    fig.canvas.mpl_connect("close_event", _on_close)

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

    device.maxApduLengthAccepted = int(args.ini.maxapdulengthaccepted)
    device.segmentationSupported = args.ini.segmentationsupported
    device.vendorIdentifier = int(args.ini.vendoridentifier)
    device.protocolServicesSupported = app.get_services_supported().value

    for obj in objects[1:]:
        app.add_object(obj)

    print(
        f"[HVACSim] Device '{device_name}' ready on {args.ini.address} (ID {device_id})"
    )

    data_buf = {
        k: deque(maxlen=600)
        for k in ["time", "temp", "setp", "chill", "intake", "exhaust"]
    }

    running_evt = threading.Event()
    running_evt.set()

    core_thread = threading.Thread(target=run, name="bacpypes-core", daemon=True)
    core_thread.start()

    ctl_thread = threading.Thread(
        target=hvac_loop,
        args=(
            objects[1],
            objects[2],
            objects[3],
            objects[4],
            objects[5],
            objects[6],
            data_buf,
            running_evt,
        ),
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

    start_plot(
        data_buf,
        running_evt,
        objects[1],
        objects[2],
        objects[3],
        objects[4],
    )

    ctl_thread.join(timeout=1.0)
    core_thread.join(timeout=1.0)
    print("[HVACSim] Shut down.")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message="no signal handlers for child threads") # Harmless; related to vis.

    main()
