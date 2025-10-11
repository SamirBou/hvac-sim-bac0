#!/usr/bin/env python3
"""
BACnet/IP device using BACpypes.
Usage:
    python3 bacnet_device.py --ini ./BACpypes.ini --debug bacpypes.udp
"""

from __future__ import annotations

import logging
import signal
import sys

from bacpypes.consolelogging import ConfigArgumentParser
from bacpypes.core import run, stop
from bacpypes.app import BIPSimpleApplication
from bacpypes.object import DeviceObject


def _setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("bacnet_device")
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    return logger


class MinimalApplication(BIPSimpleApplication):
    """Thin wrapper around BIPSimpleApplication."""
    pass


def _signal_handler(signum, _frame=None) -> None:
    _LOG.info("Signal %s received, stopping event loop...", signum)
    try:
        stop()
    except Exception:
        _LOG.exception("Error while stopping bacpypes")


_LOG = logging.getLogger("bacnet_device")


def main() -> None:
    global _LOG

    parser = ConfigArgumentParser(
        description="BACnet/IP device (BACpypes)"
    )
    args = parser.parse_args()

    # If user passed --debug modules, use DEBUG; otherwise INFO
    loglevel = logging.DEBUG if getattr(args, "debug", None) else logging.INFO
    _LOG = _setup_logging(level=loglevel)

    _LOG.info("Starting BACnet device")
    _LOG.debug("INI contents: %s", getattr(args, "ini", None))

    # Build device from INI values to get object name/identifier
    device = DeviceObject(
        objectName=args.ini.objectname,
        objectIdentifier=int(args.ini.objectidentifier),
        maxApduLengthAccepted=int(args.ini.maxapdulengthaccepted),
        segmentationSupported=args.ini.segmentationsupported,
        vendorIdentifier=int(args.ini.vendoridentifier),
    )
    _LOG.info(
        "Device object created: name=%s id=%s",
        device.objectName,
        device.objectIdentifier,
    )

    # Bind the application to the configured IP address
    app = MinimalApplication(device, args.ini.address)
    _LOG.info("Application bound to %s", args.ini.address)

    # Advertise the supported services over the network
    device.protocolServicesSupported = app.get_services_supported().value
    _LOG.debug("protocolServicesSupported=%r", device.protocolServicesSupported)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    _LOG.info("Event loop running (Ctrl+C to exit)")
    run()
    _LOG.info("Shutdown complete")


if __name__ == "__main__":
    main()
