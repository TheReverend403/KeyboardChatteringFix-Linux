import logging
import os
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, DefaultDict, Dict, Union

import libevdev
import typer
from libevdev import Device, InputEvent
from libevdev.const import EventCode


def send_event_packet(device: Device, event: InputEvent):
    """Send an event packet properly ending with a SYN_REPORT."""

    packet = [event, InputEvent(libevdev.EV_SYN.SYN_REPORT, 0)]
    device.send_events(packet)


def start_filtering(
    evdev: Device, event_filter: Callable[[InputEvent], Union[InputEvent, None]]
):
    """Start filtering input from a device."""

    # grab the device to ourselves - now only we see the events it emits
    evdev.grab()
    # create a copy of the device that we can write to - this will emit the filtered events to anyone who listens
    uidev = evdev.create_uinput_device()

    while True:
        # since the descriptor is blocking, this blocks until there are events available
        for event in evdev.events():
            if (f := event_filter(event)) is not None:
                send_event_packet(uidev, f)


class ChatterFix(object):
    device: Path
    threshold: int
    _last_key_up: Dict[EventCode, int] = dict()
    _key_pressed: DefaultDict[EventCode, bool] = defaultdict(bool)

    def __init__(self, device: Path, threshold: int):
        self.device = device
        self.threshold = threshold

    def run(self):
        # we don't want to (incompletely) capture stuff triggered by starting the script itself
        time.sleep(1)
        with self._get_device() as device:
            start_filtering(device, self._filter_chatter)

    @contextmanager
    def _get_device(self) -> Device:
        """Safely get an evdev device handle."""

        with open(self.device, "rb") as fd:
            evdev = Device(fd)
            yield evdev

    def _filter_chatter(self, event: InputEvent) -> Union[InputEvent, None]:
        """Filter input events that are coming too fast"""

        # no need to relay those - we are going to emit our own
        if event.matches(libevdev.EV_SYN) or event.matches(libevdev.EV_MSC):
            logging.debug(f"Got {event.code} - filtering")
            return None

        # some events we don't want to filter, like EV_LED for toggling NumLock and the like, and also key hold events
        if not event.matches(libevdev.EV_KEY) or event.value > 1:
            logging.debug(f"Got {event.code} - letting through")
            return event

        # the values are 0 for up, 1 for down and 2 for hold
        if event.value == 0:
            if not self._key_pressed[event.code]:
                logging.info(f"Got {event.code} up, but key is not pressed - filtering")
                return None

            logging.debug(f"Got {event.code} up - letting through")
            self._last_key_up[event.code] = event.sec * 1e6 + event.usec
            self._key_pressed[event.code] = False
            return event

        prev = self._last_key_up.get(event.code)
        now = event.sec * 1e6 + event.usec

        if prev is None or now - prev > self.threshold * 1e3:
            logging.debug(f"Got {event.code} down - letting through")
            self._key_pressed[event.code] = True
            return event

        logging.info(
            f"Got {event.code} down, but last key up was {(now - prev) / 1e3} ms ago - filtering"
        )
        return None


def main(device: Path, threshold: int = 30):
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="[%(levelname)s] %(message)s"
    )

    if not os.access(device, os.W_OK):
        logging.error("Cannot write to device.")
        raise typer.Exit(code=1)

    chatterfix = ChatterFix(device=device, threshold=threshold)
    chatterfix.run()


if __name__ == "__main__":
    typer.run(main)
