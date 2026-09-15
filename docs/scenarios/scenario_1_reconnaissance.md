# Scenario 1: BACnet Reconnaissance

## Overview

| | |
|---|---|
| Tactic | Discovery |
| Techniques | [T0846 Remote System Discovery](https://attack.mitre.org/techniques/T0846/), [T0888 Remote System Information Discovery](https://attack.mitre.org/techniques/T0888/), [T0861 Point & Tag Identification](https://attack.mitre.org/techniques/T0861/) |
| Target | HVACSim BACnet device (instance 101) |
| Impact | The attacker enumerates the device and all HVAC points; no state change |

## Objective

Discover HVACSim on the network and enumerate its BACnet object list and point
values without writing anything. This builds the point map an attacker needs
before manipulating the process.

## Fact Variables

| Fact | Description | Type | Default |
|---|---|---|---|
| `bacnet.device.instance` | HVACSim device instance | int | 101 |
| `bacnet.obj.type` | Object type to enumerate | string | device |
| `bacnet.obj.instance` | Device object instance | int | 101 |
| `bacnet.obj.property` | Property id (object-list = 76) | int | 76 |
| `bacnet.read.index` | Array index (-2 = whole array) | int | -2 |
| `bacnet.object.type` | Point type to read | string | analog-input |
| `bacnet.object.instance` | Point instance | int | 1, 2 |

## Caldera Operation

| Step | Ability | Ability ID | Facts Used |
|---|---|---|---|
| 1 | BACnet Who-Is | b93bd80e-3a70-11eb-adc1-0242ac120002 | (broadcast) |
| 2 | BACnet Device Collection - Basic | 485e97e7-c352-432d-b8d3-fa8460e4fe49 | `bacnet.device.instance` |
| 3 | BACnet Object Collection - Basic | bd13ac81-b932-463d-95aa-a22aeefbc9ac | `bacnet.obj.*`, `bacnet.read.index` |
| 4 | BACnet Read Property | 47432648-5678-11eb-ae93-0242ac130002 | `bacnet.object.type=analog-input`, `bacnet.object.instance=1` (temperature) |
| 5 | BACnet Read Property | 47432648-5678-11eb-ae93-0242ac130002 | `bacnet.object.type=analog-input`, `bacnet.object.instance=2` (chiller) |

## Expected Observations

- HVACSim answers Who-Is with an I-Am (device instance 101).
- The object-list enumeration returns all six HVAC objects: AV:1, AO:1, AO:2, BV:1, AI:1, AI:2.
- Reads return the live room temperature and chiller load with no authentication.
- No process state changes; the HMI is unaffected.
