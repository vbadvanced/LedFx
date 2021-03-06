from ledfx.utils import BaseRegistry, RegistryLoader
from abc import abstractmethod
from threading import Thread
from ledfx.events import DeviceUpdateEvent, Event
import voluptuous as vol
import numpy as np
import importlib
import pkgutil
import logging
import time
import os
import re

_LOGGER = logging.getLogger(__name__)

@BaseRegistry.no_registration
class Device(BaseRegistry):

    CONFIG_SCHEMA = vol.Schema({
        vol.Required('name', description='Friendly name for the device'): str,
        vol.Optional('max_brightness', description='Max brightness for the device', default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional('center_offset', description='Number of pixels from the preceived center of the device', default=0): int,
        vol.Optional('refresh_rate', description='Rate that pixels are sent to the device', default=60): int,
        vol.Optional('force_refresh', description='Force the device to always refresh', default=False): bool,
        vol.Optional('preview_only', description='Preview the pixels without updating the device', default=False): bool
    })

    _active = False
    _output_thread = None
    _active_effect = None

    def __init__(self, ledfx, config):
        self._ledfx = ledfx
        self._config = config

    def __del__(self):
        if self._active:
            self.deactivate()

    @property
    def pixel_count(self):
        pass

    def set_effect(self, effect, start_pixel = None, end_pixel = None):
        if self._active_effect != None:
            self._active_effect.deactivate()

        self._active_effect = effect
        self._active_effect.activate(self.pixel_count)
        #self._active_effect.setDirtyCallback(self.process_active_effect)
        if not self._active:
            self.activate()

    def clear_effect(self):
        if self._active_effect != None:
            self._active_effect.deactivate()
            self._active_effect = None
        
        if self._active:
            # Clear all the pixel data before deactiving the device
            assembled_frame = np.zeros((self.pixel_count, 3))
            self.flush(assembled_frame)
            self._ledfx.events.fire_event(DeviceUpdateEvent(
                self.id, assembled_frame))

            self.deactivate()

    @property
    def active_effect(self):
        return self._active_effect

    def process_active_effect(self):
        # Assemble the frame if necessary, if nothing changed just sleep
        assembled_frame = self.assemble_frame()
        if assembled_frame is not None:
            if not self._config['preview_only']:
                self.flush(assembled_frame)

            def trigger_device_update_event(): 
                self._ledfx.events.fire_event(DeviceUpdateEvent(
                    self.id, assembled_frame))
            self._ledfx.loop.call_soon_threadsafe(trigger_device_update_event)

    def thread_function(self):
        # TODO: Evaluate switching over to asyncio with UV loop optimization
        # instead of spinning a seperate thread.
        sleep_interval = 1 / self._config['refresh_rate']

        if self._active:
            self._ledfx.loop.call_later(sleep_interval, self.thread_function)
            self.process_active_effect()

        # while self._active:
        #     start_time = time.time()
    
        #     self.process_active_effect()

        #     # Calculate the time to sleep accounting for potential heavy
        #     # frame assembly operations
        #     time_to_sleep = sleep_interval - (time.time() - start_time)
        #     if time_to_sleep > 0:
        #         time.sleep(time_to_sleep)
        # _LOGGER.info("Output device thread terminated.")

    def assemble_frame(self):
        """
        Assembles the frame to be flushed. Currently this will just return
        the active channels pixels, but will eventaully handle things like
        merging multiple segments segments and alpha blending channels
        """
        frame = None
        if self._active_effect._dirty:
            frame = np.clip(self._active_effect.pixels * self._config['max_brightness'], 0, 255)
            if self._config['center_offset']:
                frame = np.roll(frame, self._config['center_offset'], axis = 0)

            self._active_effect._dirty = self._config['force_refresh']

        return frame

    def activate(self):
        self._active = True
        #self._device_thread = Thread(target = self.thread_function)
        #self._device_thread.start()
        self._device_thread = None
        self.thread_function()

    def deactivate(self):
        self._active = False
        if self._device_thread:
            self._device_thread.join()
            self._device_thread = None

    @abstractmethod
    def flush(self, data):
        """
        Flushes the provided data to the device. This abstract medthod must be 
        overwritten by the device implementation.
        """

    @property
    def name(self):
        return self._config['name']

    @property
    def max_brightness(self):
        return self._config['max_brightness'] * 256
    
    @property
    def refresh_rate(self):
        return self._config['refresh_rate']


class Devices(RegistryLoader):
    """Thin wrapper around the device registry that manages devices"""

    PACKAGE_NAME = 'ledfx.devices'

    def __init__(self, ledfx):
        super().__init__(ledfx, Device, self.PACKAGE_NAME)

        def cleanup_effects(e):
            self.clear_all_effects()

        self._ledfx.events.add_listener(
            cleanup_effects, Event.LEDFX_SHUTDOWN)

    def create_from_config(self, config):
        for device in config:
            _LOGGER.info("Loading device from config: {}".format(device))
            self._ledfx.devices.create(
                id = device['id'],
                type = device['type'],
                config = device['config'],
                ledfx = self._ledfx)

    def clear_all_effects(self):
        for device in self.values():
            device.clear_effect()

    def get_device(self, device_id):
        for device in self.values():
            if device_id == device.id:
                return device
        return None


