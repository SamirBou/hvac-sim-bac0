#!/usr/bin/env python3
"""
Simulates an HVAC system as a BACnet/IP device using BAC0 for Caldera for OT.

Objects:
  - AO:0 temperature_setpoint_c      (writable)
  - AO:1 intake_fan_speed_percent    (writable)
  - AO:2 exhaust_fan_speed_percent   (writable)
  - BO:0 emergency_stop              (writable)
  - AI:0 current_temperature_c       (read-only)
  - AI:1 chiller_speed_percent       (read-only)

To run:
    python3 hvac_sim_bac0.py

Authors:
    Original by Capstone Group: University of Hawaii at Manoa Group 9 2025
    Developers: Jake Dickinson, Elijah Saloma
    Advisor: Samir Boussarhane
"""

import random
import time
import threading
import signal
import asyncio
from collections import deque

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Slider, Button

import BAC0
from BAC0.core.devices.local.factory import ObjectFactory

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

ao_setpoint = None
ao_intake = None
ao_exhaust = None
bo_e_stop = None
ai_temp = None
ai_chiller = None
bacnet = None


def start_bacnet(device_id, address):
    global ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller, bacnet

    async def _start():
        nonlocal bacnet
        bacnet = await BAC0.start(ip=address, deviceId=device_id)
        return bacnet

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bacnet = loop.run_until_complete(_start())
    factory = ObjectFactory(bacnet)

    ao_setpoint = factory.analog_output(
        objectIdentifier=("analogOutput", 0),
        objectName="temperature_setpoint_c",
        presentValue=temperature_setpoint_c,
        description="Desired room temperature (°C)",
        relinquishDefault=23.0,
    )
    ao_intake = factory.analog_output(
        objectIdentifier=("analogOutput", 1),
        objectName="intake_fan_speed_percent",
        presentValue=intake_fan_speed,
        description="Intake fan speed (%)",
        relinquishDefault=30.0,
    )
    ao_exhaust = factory.analog_output(
        objectIdentifier=("analogOutput", 2),
        objectName="exhaust_fan_speed_percent",
        presentValue=exhaust_fan_speed,
        description="Exhaust fan speed (%)",
        relinquishDefault=30.0,
    )
    bo_e_stop = factory.binary_output(
        objectIdentifier=("binaryOutput", 0),
        objectName="emergency_stop",
        presentValue=emergency_stop,
        description="Emergency stop (True/False)",
        relinquishDefault=False,
    )
    ai_temp = factory.analog_input(
        objectIdentifier=("analogInput", 0),
        objectName="current_temperature_c",
        presentValue=current_temp_c,
        description="Measured room temperature (°C)",
    )
    ai_chiller = factory.analog_input(
        objectIdentifier=("analogInput", 1),
        objectName="chiller_speed_percent",
        presentValue=chiller_speed_pct,
        description="Chiller load (%)",
    )

    loop.run_forever()


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
        6,
        left=0.05,
        right=0.95,
        bottom=0.05,
        top=0.9,
        wspace=0.3,
        hspace=0.4,
    )

    ax_temp = fig.add_subplot(gs[0, :3])
    ax_chill = fig.add_subplot(gs[0, 3:])
    ax_ctrl = fig.add_subplot(gs[1, :])
    ax_fans = fig.add_subplot(gs[2, :])
    ax_e_stop = fig.add_subplot(gs[3, 0])
    ax_setpoint = fig.add_subplot(gs[3, 1])
    ax_intake = fig.add_subplot(gs[3, 2])
    ax_exhaust = fig.add_subplot(gs[3, 3])
    ax_reset = fig.add_subplot(gs[3, 4])
    ax_quit = fig.add_subplot(gs[3, 5])

    (temp_line,) = ax_temp.plot([], [], color=TEMP_COLOR, linewidth=2, label="Temp (°C)")
    (setp_line,) = ax_temp.plot([], [], color=SETPOINT_COLOR, linewidth=2, label="Setpoint (°C)")
    ax_temp.set_xlim(0, 600)
    ax_temp.set_ylim(15, 35)
    ax_temp.set_title("Temperature")
    ax_temp.legend()
    ax_temp.grid(True)

    (chill_line,) = ax_chill.plot([], [], color=CHILLER_COLOR, linewidth=2)
    ax_chill.set_xlim(0, 600)
    ax_chill.set_ylim(0, 100)
    ax_chill.set_title("Chiller Load (%)")
    ax_chill.grid(True)

    (ctrl_line,) = ax_ctrl.plot([], [], color=TEMP_COLOR, linewidth=2)
    ax_ctrl.set_xlim(0, 600)
    ax_ctrl.set_ylim(15, 35)
    ax_ctrl.set_title("Temperature Control")
    ax_ctrl.grid(True)

    (intake_line,) = ax_fans.plot([], [], color=INTAKE_COLOR, linewidth=2, label="Intake (%)")
    (exhaust_line,) = ax_fans.plot([], [], color=EXHAUST_COLOR, linewidth=2, label="Exhaust (%)")
    ax_fans.set_xlim(0, 600)
    ax_fans.set_ylim(0, 100)
    ax_fans.set_title("Fan Speeds")
    ax_fans.legend()
    ax_fans.grid(True)

    setpoint_slider = Slider(ax_setpoint, "Setpoint (°C)", 15, 35, valinit=temperature_setpoint_c)
    intake_slider = Slider(ax_intake, "Intake (%)", 0, 100, valinit=intake_fan_speed)
    exhaust_slider = Slider(ax_exhaust, "Exhaust (%)", 0, 100, valinit=exhaust_fan_speed)
    e_stop_button = Button(ax_e_stop, "E-Stop", color="red" if emergency_stop else "green")
    reset_button = Button(ax_reset, "Reset", color="blue")
    quit_button = Button(ax_quit, "Quit", color="gray")

    def update_plot(frame):
        if not data_buf["time"]:
            return temp_line, setp_line, chill_line, ctrl_line, intake_line, exhaust_line

        times = list(data_buf["time"])
        temps = list(data_buf["temp"])
        setps = list(data_buf["setp"])
        chills = list(data_buf["chill"])
        intakes = list(data_buf["intake"])
        exhausts = list(data_buf["exhaust"])

        x_min = max(0, times[-1] - 600)
        x_max = times[-1]

        ax_temp.set_xlim(x_min, x_max)
        ax_chill.set_xlim(x_min, x_max)
        ax_ctrl.set_xlim(x_min, x_max)
        ax_fans.set_xlim(x_min, x_max)

        temp_line.set_data(times, temps)
        setp_line.set_data(times, setps)
        chill_line.set_data(times, chills)
        ctrl_line.set_data(times, temps)
        intake_line.set_data(times, intakes)
        exhaust_line.set_data(times, exhausts)

        return temp_line, setp_line, chill_line, ctrl_line, intake_line, exhaust_line

    def setpoint_changed(val):
        ao_setpoint.presentValue = val

    def intake_changed(val):
        ao_intake.presentValue = val

    def exhaust_changed(val):
        ao_exhaust.presentValue = val

    def e_stop_clicked(event):
        nonlocal emergency_stop
        emergency_stop = not emergency_stop
        bo_e_stop.presentValue = emergency_stop
        e_stop_button.color = "red" if emergency_stop else "green"
        e_stop_button.label.set_text("E-Stop" if not emergency_stop else "Reset")
        fig.canvas.draw()

    def reset_clicked(event):
        global current_temp_c, chiller_speed_pct, chiller_integral
        current_temp_c = 22.0
        chiller_speed_pct = 30.0
        chiller_integral = 0.0
        ao_setpoint.presentValue = temperature_setpoint_c
        ao_intake.presentValue = intake_fan_speed
        ao_exhaust.presentValue = exhaust_fan_speed
        bo_e_stop.presentValue = False
        setpoint_slider.set_val(temperature_setpoint_c)
        intake_slider.set_val(intake_fan_speed)
        exhaust_slider.set_val(exhaust_fan_speed)
        e_stop_button.color = "green"
        e_stop_button.label.set_text("E-Stop")
        fig.canvas.draw()

    def quit_clicked(event):
        running_evt.clear()
        plt.close("all")

    setpoint_slider.on_changed(setpoint_changed)
    intake_slider.on_changed(intake_changed)
    exhaust_slider.on_changed(exhaust_changed)
    e_stop_button.on_clicked(e_stop_clicked)
    reset_button.on_clicked(reset_clicked)
    quit_button.on_clicked(quit_clicked)

    ani = FuncAnimation(fig, update_plot, interval=1000, blit=True, cache_frame_data=False)
    plt.show()


def main():
    args = ConfigArgumentParser(description=__doc__).parse_args()
    device_name = args.ini.objectname or "HVACSim"
    device_id = int(args.ini.objectidentifier)
    address = args.ini.address

    core_thread = threading.Thread(target=start_bacnet, args=(device_id, address), name="bac0-core", daemon=True)
    core_thread.start()

    # wait for objects to be created
    while ao_setpoint is None:
        time.sleep(0.1)

    objects = [ao_setpoint, ao_intake, ao_exhaust, bo_e_stop, ai_temp, ai_chiller]

    print(
        f"[HVACSim] Device '{device_name}' ready on {address} (ID {device_id})"
    )

    data_buf = {
        k: deque(maxlen=600)
        for k in ["time", "temp", "setp", "chill", "intake", "exhaust"]
    }

    running_evt = threading.Event()
    running_evt.set()

    ctl_thread = threading.Thread(
        target=hvac_loop,
        args=(
            objects[0],
            objects[1],
            objects[2],
            objects[3],
            objects[4],
            objects[5],
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
            if bacnet:
                bacnet.disconnect()
        except Exception:
            pass
        plt.close("all")

    signal.signal(signal.SIGINT, _sigint)

    start_plot(
        data_buf,
        running_evt,
        objects[0],
        objects[1],
        objects[2],
        objects[3],
    )

    ctl_thread.join(timeout=1.0)
    core_thread.join(timeout=1.0)
    print("[HVACSim] Shut down.")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message="no signal handlers for child threads")  # Harmless; related to vis.

    main()