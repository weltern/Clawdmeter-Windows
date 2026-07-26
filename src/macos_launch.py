"""Tell a macOS sign-in launch apart from the user opening the app.

Windows and Linux register a startup command, so they can pass ``--startup`` and
the app knows to stay in the tray. ``SMAppService`` (see ``macos_login_item``)
launches the .app with no arguments at all, so on macOS that flag never arrives
and Clawdmeter would pop its dashboard open at every sign-in.

Nothing about the process distinguishes the two launches — this was measured on
macOS 15.6.1, not assumed: argv, the parent process (``launchd`` either way) and
``XPC_SERVICE_NAME`` are byte-identical at login and on a normal open.

Apple's actual answer is the ``kAEOpenApplication`` Apple Event, which carries
``keyAELaunchedAsLogInItem`` in ``keyAEPropData`` when launchd started us at
login. AppKit only exposes that event while it is handling it, so it has to be
read during ``applicationDidFinishLaunching``. Qt owns the NSApplication
delegate, so we observe ``NSApplicationDidFinishLaunchingNotification`` instead
— same window, no delegate to fight over. Confirmed in a real PySide6 app:

    login:   eventID=0x6f617070 ('oapp')  propdata=0x6c676974 ('lgit')
    normal:  eventID=0x6f617070 ('oapp')  propdata=None

Two things that looked like easier answers and are not:

* ``NSApplicationLaunchIsDefaultLaunchKey`` in the notification's userInfo is
  ``True`` for *both* launches. It reads like the flag we want; it isn't.
* The event is unreadable anywhere else — before ``exec()``, from a zero-delay
  timer, and 1.5s in, ``currentAppleEvent`` is None. And the relative order of a
  zero-delay timer and the notification is *itself* different between the two
  launches, so no timer can stand in for the observer.
"""

from __future__ import annotations

import sys

# Four-character codes from Carbon's AppleEvents.h, computed rather than
# imported: the pyobjc bindings that define them aren't among our dependencies.
KAE_OPEN_APPLICATION = int.from_bytes(b"oapp", "big")
KEY_AE_PROP_DATA = int.from_bytes(b"prdt", "big")
KEY_AE_LAUNCHED_AS_LOGIN_ITEM = int.from_bytes(b"lgit", "big")

# How long to wait for the notification before assuming a normal launch. It
# fires within milliseconds in practice; this only matters if a future macOS
# stops sending it, and the failure it prevents is an app that starts with no
# window and no explanation.
FALLBACK_MS = 3000

# Observers are held weakly by NSNotificationCenter, so the watcher has to
# outlive this function or the notification lands on a freed object.
_watchers: list = []


def is_login_launch_event(event) -> bool:
    """True if `event` is the open-application event for a sign-in launch."""
    if event is None:
        return False
    try:
        if int(event.eventID()) != KAE_OPEN_APPLICATION:
            return False
        prop = event.paramDescriptorForKeyword_(KEY_AE_PROP_DATA)
        if prop is None:
            return False
        return int(prop.enumCodeValue()) == KEY_AE_LAUNCHED_AS_LOGIN_ITEM
    except Exception:      # noqa: BLE001 - a malformed event is not a login one
        return False


class _Decision:
    """Delivers the verdict exactly once.

    The notification and the fallback timer race by design; whichever arrives
    first wins and the other is ignored. Showing the window twice is harmless,
    but *deciding* twice would let a late fallback override a real answer.
    """

    def __init__(self, callback):
        self._callback = callback
        self._settled = False

    @property
    def settled(self) -> bool:
        return self._settled

    def settle(self, is_login_launch: bool) -> None:
        if self._settled:
            return
        self._settled = True
        self._callback(is_login_launch)


def detect(callback, *, fallback_ms: int = FALLBACK_MS) -> None:
    """Call ``callback(is_login_launch)`` once, as soon as it can be known.

    Must be called *before* ``QApplication.exec()`` — the notification fires
    inside it. Off macOS, or if the Cocoa bindings aren't available, the answer
    is an immediate False, which is the safe direction: the window shows.
    """
    decision = _Decision(callback)
    if sys.platform != "darwin":
        decision.settle(False)
        return
    try:
        from Foundation import (NSAppleEventManager, NSNotificationCenter,
                                NSObject)
    except Exception:      # noqa: BLE001 - no pyobjc; behave like Windows
        decision.settle(False)
        return

    class _LaunchWatcher(NSObject):
        def onLaunch_(self, _note):
            evt = NSAppleEventManager.sharedAppleEventManager().currentAppleEvent()
            decision.settle(is_login_launch_event(evt))

    watcher = _LaunchWatcher.alloc().init()
    _watchers.append(watcher)
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        watcher, b"onLaunch:", "NSApplicationDidFinishLaunchingNotification",
        None)

    # Belt and braces: never leave the app windowless because a notification
    # didn't arrive.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(fallback_ms, lambda: decision.settle(False))
