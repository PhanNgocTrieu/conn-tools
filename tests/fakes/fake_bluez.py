"""A tiny stand-in for bluetoothd, enough to test `ctt-agent server -t gatt` without a radio.

Runs on a private D-Bus daemon (address in DBUS_SYSTEM_BUS_ADDRESS):
  /org/bluez/hci0   org.bluez.GattManager1          RegisterApplication / UnregisterApplication
                    org.bluez.LEAdvertisingManager1 RegisterAdvertisement / UnregisterAdvertisement
  /test             test.FakeBluez                  Connect(mtu) -> (notify fd, write fd)  = a "PC" connects
                                                    Info() -> the info characteristic value
                                                    Advert() -> the advertisement properties
Like BlueZ, Connect() calls AcquireNotify on the notify characteristic and AcquireWrite on the
write characteristic of the registered application, with the options {"mtu", "device", "link"}.
"""

import os
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

SERVICE = "a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a"
RX = "a0c70002-8f5e-4d3a-9c2b-1f0e5d4c3b2a"
TX = "a0c70003-8f5e-4d3a-9c2b-1f0e5d4c3b2a"
INFO = "a0c70004-8f5e-4d3a-9c2b-1f0e5d4c3b2a"
DEVICE = "/org/bluez/hci0/dev_11_22_33_44_55_66"


class Adapter(dbus.service.Object):
    def __init__(self, bus):
        super().__init__(bus, "/org/bluez/hci0")
        self.bus = bus
        self.chars = {}     # uuid -> (owner, path, flags)
        self.adv = {}

    @dbus.service.method("org.bluez.GattManager1", in_signature="oa{sv}", out_signature="",
                         sender_keyword="sender")
    def RegisterApplication(self, app, options, sender=None):
        om = dbus.Interface(self.bus.get_object(sender, app), "org.freedesktop.DBus.ObjectManager")
        for path, ifaces in om.GetManagedObjects().items():
            c = ifaces.get("org.bluez.GattCharacteristic1")
            if c:
                if "WriteAcquired" not in c and "NotifyAcquired" not in c and "read" not in c["Flags"]:
                    raise dbus.exceptions.DBusException("characteristic without Acquire support")
                self.chars[str(c["UUID"])] = (sender, path, [str(f) for f in c["Flags"]])
        if SERVICE not in [str(i["org.bluez.GattService1"]["UUID"])
                           for i in om.GetManagedObjects().values() if "org.bluez.GattService1" in i]:
            raise dbus.exceptions.DBusException("CTT service missing")

    @dbus.service.method("org.freedesktop.DBus.Properties", in_signature="ss", out_signature="v")
    def Get(self, iface, name):
        if iface == "org.bluez.Adapter1" and name == "Address":
            return "AA:BB:CC:00:11:22"
        raise dbus.exceptions.DBusException("org.freedesktop.DBus.Error.InvalidArgs")

    @dbus.service.method("org.bluez.GattManager1", in_signature="o", out_signature="")
    def UnregisterApplication(self, app):
        self.chars.clear()

    @dbus.service.method("org.bluez.LEAdvertisingManager1", in_signature="oa{sv}", out_signature="",
                         sender_keyword="sender")
    def RegisterAdvertisement(self, adv, options, sender=None):
        props = dbus.Interface(self.bus.get_object(sender, adv), "org.freedesktop.DBus.Properties")
        self.adv = {str(k): v for k, v in props.GetAll("org.bluez.LEAdvertisement1").items()}

    @dbus.service.method("org.bluez.LEAdvertisingManager1", in_signature="o", out_signature="")
    def UnregisterAdvertisement(self, adv):
        self.adv = {}


class Test(dbus.service.Object):
    def __init__(self, bus, adapter):
        super().__init__(bus, "/test")
        self.bus = bus
        self.a = adapter

    def _chrc(self, uuid):
        owner, path, _ = self.a.chars[uuid]
        return dbus.Interface(self.bus.get_object(owner, path), "org.bluez.GattCharacteristic1")

    @dbus.service.method("test.FakeBluez", in_signature="q", out_signature="hhq")
    def Connect(self, mtu):
        opts = dbus.Dictionary({"mtu": dbus.UInt16(mtu), "device": dbus.ObjectPath(DEVICE), "link": "LE"},
                               signature="sv")
        nfd, nmtu = self._chrc(TX).AcquireNotify(opts)
        wfd, wmtu = self._chrc(RX).AcquireWrite(opts)
        return nfd, wfd, min(int(nmtu), int(wmtu))

    @dbus.service.method("test.FakeBluez", in_signature="", out_signature="s")
    def Info(self):
        return bytes(self._chrc(INFO).ReadValue(dbus.Dictionary({}, signature="sv"))).decode()

    @dbus.service.method("test.FakeBluez", in_signature="", out_signature="a{sv}")
    def Advert(self):
        return self.a.adv


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.bus.BusConnection(os.environ["DBUS_SYSTEM_BUS_ADDRESS"])
    name = dbus.service.BusName("org.bluez", bus)  # noqa: F841 - keeps the name
    adapter = Adapter(bus)
    Test(bus, adapter)
    print("ready", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    sys.exit(main())
