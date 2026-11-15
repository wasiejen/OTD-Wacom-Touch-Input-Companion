"""Compatibility shim over the wtd package (Phase C C1, review Item 3).

touch_controller was split into the wtd package; this shim re-exports every
old name so existing imports keep working until every consumer is re-pointed
at wtd directly, then this file is deleted.

Read semantics: every forwarded name resolves through the wtd modules
(lookup order cli, devices, gestures, mapping, sender, protocol, config), so
a global rebind inside one module - main() rebinding the module-level config
- is seen by shim readers immediately. Writes forward to every wtd module
that holds the name plus the shim's own mirror dict, so single-namespace
monkeypatching keeps working and pytest's undo (which reads the recorded
value from the target's own dict) restores the same view it captured. Names
no wtd module holds live on the shim itself.
"""
import sys
import types

from wtd import cli as _wtd_cli
from wtd import config as _wtd_config
from wtd import devices as _wtd_devices
from wtd import gestures as _wtd_gestures
from wtd import mapping as _wtd_mapping
from wtd import protocol as _wtd_protocol
from wtd import sender as _wtd_sender

# Read order: cli first so main()'s rebinding of the module-level config
# global resolves to the live instance; write order is irrelevant because
# writes touch every holder.
_HOLDERS = (_wtd_cli, _wtd_devices, _wtd_gestures, _wtd_mapping,
            _wtd_sender, _wtd_protocol, _wtd_config)


class _TouchControllerShim(types.ModuleType):
    """Resolve forwarded reads through the modules; forward writes to all."""

    def __getattribute__(self, name):
        try:
            forwarded = object.__getattribute__(self, "_forwarded")
        except AttributeError:
            return object.__getattribute__(self, name)
        if name in forwarded:
            for mod in object.__getattribute__(self, "_shim_holders"):
                try:
                    return mod.__dict__[name]
                except KeyError:
                    continue
            raise AttributeError(
                "module 'touch_controller' (shim over wtd) has no "
                "attribute %r" % (name,))
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        if name == "__class__":
            types.ModuleType.__setattr__(self, name, value)
            return
        try:
            holders = object.__getattribute__(self, "_shim_holders")
        except AttributeError:
            holders = ()
        for mod in holders:
            if name in mod.__dict__:
                mod.__dict__[name] = value
        self.__dict__[name] = value

    def __dir__(self):
        names = set(self.__dict__)
        names.update(object.__getattribute__(self, "_forwarded"))
        return sorted(names)


sys.modules[__name__].__class__ = _TouchControllerShim

# Mirror every name the pre-split module exposed (its full namespace, imports
# included) into the shim dict, keeping the dunder names of the shim itself.
# pytest's monkeypatch records undo values from the target's own dict, so the
# mirrors must be present for undo to restore instead of delete.
_self = sys.modules[__name__]
_self.__dict__["_shim_holders"] = _HOLDERS
_fwdset = set()
for _mod in _HOLDERS:
    for _name, _val in _mod.__dict__.items():
        if not _name.startswith("__") and _name not in _self.__dict__:
            _self.__dict__[_name] = _val
            _fwdset.add(_name)
_self.__dict__["_forwarded"] = frozenset(_fwdset)
del _mod, _name, _val, _fwdset, _self

if __name__ == "__main__":
    main()
