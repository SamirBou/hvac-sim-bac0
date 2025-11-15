# Level 2

**HVACSim** is a lightweight simulated HVAC controller and Human–Machine Interface (HMI) designed for ICS/OT training, testing, and red-team/blue-team scenarios.
It exposes a virtual **BACnet device** with writable controls and real-time physical simulation of room temperature, chiller output, and airflow.

---

## Features

### BACnet-Exposed Virtual Device

Implements standard BACnet objects:

* **Binary Output**: emergency stop
* **Analog Outputs**: temperature setpoint, intake fan %, exhaust fan %
* **Analog Inputs**: current room temperature, chiller load %

### Steady State/Physics Simulation

The control loop models:

* fan-based cooling
* internal heat load
* actuator lag and sensor noise

### Interactive HMI

The built-in HMI provides:

* trend graph of temperature vs. time
* chiller, intake, and exhaust trend mini-charts
* emergency stop button with state feedback
* sliders for setpoint and fan speeds

## Requirements

Install:

```bash
pip install -r requirements.txt
```

---

## Running the Simulator

1. Ensure you have a BACpypes `.ini` file (example: `BACpypes.ini`).
2. Start the simulation:

```bash
python3 hvac_sim.py --ini ./BACpypes.ini
```

3. The HMI opens automatically and the BACnet device begins responding to network requests.

---

## BACnet Object Map

| Type | Object Name                 | Description                  |
| ---- | --------------------------- | ---------------------------- |
| AO0  | `temperature_setpoint_c`    | Desired room temperature     |
| AO1  | `intake_fan_speed_percent`  | Intake fan control           |
| AO2  | `exhaust_fan_speed_percent` | Exhaust fan control          |
| BO0  | `emergency_stop`            | Kill switch for chiller/fans |
| AI0  | `current_temperature_c`     | Measured room temperature    |
| AI1  | `chiller_speed_percent`     | PI-controlled chiller load   |

---

## Stopping the Simulator

Close the HMI window or press **Ctrl+C**.
Both the control loop and BACnet stack shut down.