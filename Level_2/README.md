# Level 2

BACnet/IP **HVAC process simulator** built with BACpypes3.
This level extends the Level 1 discovery device into a functional building control system that emulates temperature, airflow, and chiller dynamics using real BACnet analog and binary objects.

The simulator models a simple HVAC zone with:

* **Outputs (controls):** temperature setpoint, intake fan, exhaust fan, emergency stop
* **Inputs (sensors):** current temperature and chiller speed

When `auto_control` is `true`, the simulator automatically drives its outputs to maintain the configured temperature setpoint. When `false`, external clients (such as the **Caldera BACnet plugin**) can manipulate the outputs directly to test automation or attack scenarios.

Configuration (IP address, device ID, starting values, control behavior, etc.) is read from a YAML file (`config.yaml`).

Level 2 demonstrates a steady-state feedback system rather than a static endpoint, supporting more realistic process emulation and data collection for adversary emulation.

### Why BACpypes3

BACpypes3 is the modern, asyncio-based successor to the original BACpypes library used in Level 1. Level 2 adopts BACpypes3 to ensure compatibility with current Caldera BACnet tooling and to simplify concurrent simulation of multiple process loops due to the improved I/O handling in BACpypes3.

### Run the Simulator

Install dependencies (only once per env or machine)
```bash
pip3 install -r requirements.txt
```

Start the simulation (can be stopped with Ctrl+C)
```bash
python3 hvac_sim.py
```

### Example Output

```
[HVACSim] Device 'HVACSim' at 127.0.0.1 (id 2001)
AUTO_CONTROL = True
  - analog-output,0: temperature_setpoint_c
  - analog-output,1: intake_fan_speed_percent
  - analog-output,2: exhaust_fan_speed_percent
  - binary-output,0: emergency_stop
  - analog-input,0: current_temperature_c
  - analog-input,1: chiller_speed_percent

Tset=23.0°C | T=21.4°C | Intake=46% | Exhaust=46% | Chiller=45% | E-Stop=OFF
Tset=23.0°C | T=21.6°C | Intake=47% | Exhaust=47% | Chiller=46% | E-Stop=OFF
Tset=23.0°C | T=21.8°C | Intake=47% | Exhaust=47% | Chiller=47% | E-Stop=OFF
```

This confirms that the BACnet device is running and updating its sensor values dynamically.